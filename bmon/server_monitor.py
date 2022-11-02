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

from . import server_tasks, models
from .mempool import PolicyCohort, MempoolAcceptAggregator
from .hosts import get_bitcoind_hosts_to_policy_cohort
from bmon_infra import infra


log = logging.getLogger(__name__)

cli = App()


SERVER_EVENT_QUEUE_DEPTH = Gauge(
    "bmon_server_event_queue_depth",
    "The depth of the queue processing all events.",
)

SERVER_MEMPOOL_EVENT_QUEUE_DEPTH = Gauge(
    "bmon_server_mempool_event_queue_depth",
    "The depth of the queue processing for server mempool events.",
)

MEMPOOL_TOTAL_TXIDS_ACCEPTED = Gauge(
    "bmon_mempool_total_txids_accepted",
    "The number of unique transactions we've accepted to all mempools",
)

HOST_LABELS = ["host", "bitcoin_version", "region", "cohort"]

MEMPOOL_TOTAL_TXIDS_ACCEPTED_PER_HOST = Gauge(
    "bmon_mempool_total_txids_accepted_per_host",
    "The number of unique transactions we've accepted to a host's mempool",
    HOST_LABELS,
)

MEMPOOL_TOTAL_TXIDS_IN_HOUR = Gauge(
    "bmon_mempool_total_txids_accepted_last_hour",
    "The number of unique transactions we've accepted to all mempools in the last hour",
)


MEMPOOL_TOTAL_TXIDS_IN_HOUR_PER_HOST = Gauge(
    "bmon_mempool_total_txids_accepted_last_hour_per_host",
    "The number of unique transactions we've accepted to all mempools in the last hour "
    " per host",
    HOST_LABELS,
)

MEMPOOL_TOTAL_TXIDS_ACCEPTED_BY_ALL_IN_HOUR = Gauge(
    "bmon_mempool_total_txids_accepted_by_all_last_hour",
    "The number of txids accepted by all hosts in the last hour",
)

MEMPOOL_TOTAL_TXIDS_ACCEPTED_BY_COHORT_IN_HOUR = Gauge(
    "bmon_mempool_total_txids_accepted_by_cohort_last_hour",
    "The number of txids accepted by all hosts in a policy cohort in the last hour",
    ["cohort"],
)

MEMPOOL_MAX_PROPAGATION_SPREAD_IN_HOUR = Gauge(
    "bmon_mempool_max_propagation_spread_in_hour",
    "The greatest difference between the first host and the last host seeing a "
    "particular transaction (in the last hour)",
)

MEMPOOL_MIN_PROPAGATION_SPREAD_IN_HOUR = Gauge(
    "bmon_mempool_min_propagation_spread_in_hour",
    "The least difference between the first host and the last host seeing a "
    "particular transaction (in the last hour)",
)

REDIS_KEYS = Gauge(
    "bmon_server_redis_key_count",
    "The number of keys in the server redis instance",
)


def refresh_metrics(
    mempool_agg: MempoolAcceptAggregator | None = None,
    hosts_to_cohort: dict[models.Host, PolicyCohort] | None = None,
):
    SERVER_EVENT_QUEUE_DEPTH.set(len(server_tasks.server_q))
    SERVER_MEMPOOL_EVENT_QUEUE_DEPTH.set(len(server_tasks.mempool_q))

    host_to_cohort = hosts_to_cohort or get_bitcoind_hosts_to_policy_cohort()
    labels_for_host: dict[str, dict[str, str]] = {
        h.name: {
            "host": h.name,
            "bitcoin_version": h.bitcoin_version,
            "region": h.region,
            "cohort": cohort.name,
        }
        for h, cohort in host_to_cohort.items()
    }

    mempool_agg = mempool_agg or server_tasks.get_mempool_aggregator()
    MEMPOOL_TOTAL_TXIDS_ACCEPTED.set(mempool_agg.get_total_txids_processed())

    for host, total in mempool_agg.get_total_txids_processed_per_host().items():
        MEMPOOL_TOTAL_TXIDS_ACCEPTED_PER_HOST.labels(**labels_for_host[host]).set(total)

    REDIS_KEYS.set(server_tasks.redisdb.dbsize())

    total_txids_in_hour = 0
    total_txids_in_hour_per_host = {h: 0 for h in mempool_agg.host_to_cohort.keys()}
    total_txids_in_hour_by_all = 0
    total_txids_in_hour_by_cohort: dict[PolicyCohort, int] = {
        cohort: 0 for cohort in PolicyCohort
    }
    max_spread = 0.0
    min_spread = 1e6

    for event in mempool_agg.get_propagation_events():
        total_txids_in_hour += 1

        for host in event.host_to_timestamp:
            total_txids_in_hour_per_host[host] += 1

        if event.all_complete:
            total_txids_in_hour_by_all += 1

        for cohort in event.cohorts_complete:
            total_txids_in_hour_by_cohort[cohort] += 1

        if event.spread > max_spread:
            max_spread = event.spread

        if event.spread < min_spread:
            min_spread = event.spread

    MEMPOOL_TOTAL_TXIDS_IN_HOUR.set(total_txids_in_hour)

    for host, total in total_txids_in_hour_per_host.items():
        MEMPOOL_TOTAL_TXIDS_IN_HOUR_PER_HOST.labels(**labels_for_host[host]).set(total)

    MEMPOOL_TOTAL_TXIDS_ACCEPTED_BY_ALL_IN_HOUR.set(total_txids_in_hour_by_all)

    for cohort, total in total_txids_in_hour_by_cohort.items():
        MEMPOOL_TOTAL_TXIDS_ACCEPTED_BY_COHORT_IN_HOUR.labels(cohort.name).set(total)

    MEMPOOL_MAX_PROPAGATION_SPREAD_IN_HOUR.set(max_spread)
    MEMPOOL_MIN_PROPAGATION_SPREAD_IN_HOUR.set(min_spread)


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
