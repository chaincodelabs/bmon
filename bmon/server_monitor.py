import os
import signal
import sys
from wsgiref.simple_server import make_server
import logging

import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bmon.settings")
django.setup()
from clii import App
from prometheus_client import make_wsgi_app, Gauge

from . import server_tasks
from bmon_infra import infra


log = logging.getLogger(__name__)

cli = App()


SERVER_EVENT_QUEUE_DEPTH = Gauge(
    "bmon_server_event_queue_depth",
    "The depth of the queue processing all events.",
)


def refresh_metrics():
    SERVER_EVENT_QUEUE_DEPTH.set(len(server_tasks.server_q))


def sigterm_handler(*_):
    print("exiting")
    sys.exit(0)


@cli.main
def main(addr="0.0.0.0", port=infra.SERVER_EXPORTER_PORT):
    app = make_wsgi_app()

    signal.signal(signal.SIGTERM, sigterm_handler)

    def refresh(*args, **kwargs):
        try:
            refresh_metrics()
        except Exception:
            log.exception("failed to refresh bmon server metrics")

        return app(*args, **kwargs)

    httpd = make_server(addr, port, refresh)
    print(f"serving bmon server monitor {addr}:{port}")
    httpd.serve_forever()
