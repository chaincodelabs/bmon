import os
import signal
import sys
from pathlib import Path
from wsgiref.simple_server import make_server
import logging

import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bmon.settings")
django.setup()
from django.conf import settings
from clii import App
from prometheus_client import make_wsgi_app, Gauge, Counter

from . import bitcoind_tasks, models
from bmon_infra import infra


log = logging.getLogger(__name__)

cli = App()


LAST_BITCOIND_LOG_SEEN_AT = Gauge(
    "bmon_last_bitcoind_log_seen_at",
    "Time that the last bitcoind log line was processed",
)

BITCOIND_EVENT_TASKS_QUEUE_DEPTH = Gauge(
    "bmon_bitcoind_event_queue_depth",
    "The depth of the queue processing bitcoind events.",
)

BITCOIND_MEMPOOL_TASKS_QUEUE_DEPTH = Gauge(
    "bmon_bitcoind_mempool_queue_depth",
    "The depth of the queue processing bitcoind mempool activity.",
)

LAST_CONNECT_BLOCK_AT = Gauge(
    "bmon_last_connect_block_at",
    "Time of the block in the last ConnectBlockEvent",
)

MEMPOOL_ACTIVITY_CACHE_SIZE = Gauge(
    "bmon_mempool_activity_cache_size",
    "Size of the mempool activity cache",
    unit='mibibytes',
)

BITCOIND_LOG_SIZE = Gauge(
    "bmon_bitcoind_debug_log_size",
    "Size of the current debug.log",
    unit='mibibytes',
)


assert settings.BITCOIND_LOG_PATH
bitcoind_log = Path(settings.BITCOIND_LOG_PATH)


def refresh_metrics():
    log_dt = (
        models.LogProgress.objects.filter(host=settings.HOSTNAME)
        .order_by("-id")
        .values_list("timestamp", flat=True)
        .first()
    )

    if log_dt:
        LAST_BITCOIND_LOG_SEEN_AT.set(log_dt.timestamp())

    BITCOIND_EVENT_TASKS_QUEUE_DEPTH.set(len(bitcoind_tasks.events_q))
    BITCOIND_MEMPOOL_TASKS_QUEUE_DEPTH.set(len(bitcoind_tasks.mempool_q))

    cb = (
        models.ConnectBlockEvent.objects.filter(host=settings.HOSTNAME)
        .order_by("-id")
        .first()
    )

    if cb:
        LAST_CONNECT_BLOCK_AT.set(cb.timestamp.timestamp())

    if bitcoind_tasks.CURRENT_MEMPOOL_FILE.exists():
        MEMPOOL_ACTIVITY_CACHE_SIZE.set(
            os.path.getsize(bitcoind_tasks.CURRENT_MEMPOOL_FILE) / (1024 ** 2))

    if bitcoind_log.exists():
        BITCOIND_LOG_SIZE.set(
            os.path.getsize(bitcoind_log) / (1024 ** 2))


def sigterm_handler(signal, frame):
    print("exiting")
    sys.exit(0)


@cli.main
def main(addr="0.0.0.0", port=infra.BMON_BITCOIND_EXPORTER_PORT):
    app = make_wsgi_app()

    signal.signal(signal.SIGTERM, sigterm_handler)

    def refresh(*args, **kwargs):
        try:
            refresh_metrics()
        except Exception:
            log.exception("failed to refresh bitcoind worker metrics")

        return app(*args, **kwargs)

    httpd = make_server(addr, port, refresh)
    print(f"serving bitcoind monitor {addr}:{port}")
    httpd.serve_forever()
