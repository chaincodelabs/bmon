"""
Tasks that execute on the central bmon server.

There are only one of these workers, so here is where we queue up periodic analysis
tasks that should only be run in one place, with a view of the whole herd of bitcoind
nodes.
"""
import os
import time
import logging
import datetime
from collections import defaultdict

import django
import redis
from django.conf import settings
from huey import RedisHuey, crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bmon.settings")
django.setup()

from bmon import models, mempool, bitcoin
from .hosts import get_bitcoind_hosts_to_policy_cohort


log = logging.getLogger(__name__)

server_q = RedisHuey(
    "bmon-server",
    url=settings.REDIS_SERVER_URL,
    immediate=settings.TESTING,
)

# Special-case mempool events because they're so high volume; if something goes
# wrong, we don't want to disrupt other event types.
mempool_q = RedisHuey(
    "bmon-server-mempool",
    url=settings.REDIS_SERVER_URL,
    immediate=settings.TESTING,
    results=False,
)

redisdb = redis.Redis.from_url(settings.REDIS_SERVER_URL, decode_responses=True)


def get_mempool_aggregator() -> mempool.MempoolAcceptAggregator:
    """
    Cache this for 90 seconds; we want to refresh periodically in case host versions
    change, potentially putting them in a different policy cohort.
    """
    SECONDS_TO_CACHE = 90
    CACHE_KEY = '__cache'

    if (got := getattr(get_mempool_aggregator, CACHE_KEY, None)):
        [ts, cached] = got
        if (time.time() - ts) <= SECONDS_TO_CACHE:
            return cached

    hosts_to_policy = {
        h.name: v for h, v in get_bitcoind_hosts_to_policy_cohort().items()}

    agg = mempool.MempoolAcceptAggregator(redisdb, hosts_to_policy)

    setattr(get_mempool_aggregator, CACHE_KEY, (time.time(), agg))
    return get_mempool_aggregator()


@server_q.periodic_task(crontab(minute="*/10"))
def check_for_overlapping_peers():
    def getpeerinfo(rpc):
        return rpc.getpeerinfo()

    results = bitcoin.gather_rpc(getpeerinfo)
    peer_to_hosts = defaultdict(list)
    hosts_contacted = []

    for hostname, peers in results.items():
        if peers == bitcoin.RPC_ERROR_RESULT:
            log.warning("Unable to retrieve peers from host %r", hostname)
            continue

        hosts_contacted.append(hostname)
        for peer in peers:
            peer_to_hosts[peer["addr"]].append(hostname)

    print(
        "%d peers found across %d hosts (%s)"
        % (len(peer_to_hosts), len(hosts_contacted), ", ".join(hosts_contacted))
    )
    for peer, hosts in peer_to_hosts.items():
        if len(hosts) > 1:
            print("peer overlap detected for %r: %s" % (peer, hosts))


@server_q.task()
def persist_bitcoind_event(event: dict, _: str):
    modelname = event.pop("_model")
    Model = getattr(models, modelname)

    if Model == models.MempoolReject:
        # XXX this is an ugly hack: Django doesn't suffix with "_id" in `model_to_dict`;
        # come up with a better way of dealing with this.
        event["peer_id"] = event.pop("peer")

    if "host" in event:
        event["host_id"] = event.pop("host")

    instance = Model.objects.create(**event)
    print(f"Saved {instance}")


@mempool_q.task()
def process_mempool_accept(txid: str, seen_at: datetime.datetime, host: str):
    agg = get_mempool_aggregator()

    if agg.mark_seen(host, txid, seen_at) == mempool.PropagationStatus.CompleteAll:
        process_completed_propagations(txid)


@mempool_q.task()
def process_completed_propagations(txid: str):
    agg = get_mempool_aggregator()
    agg.process_completed_propagations([txid], assert_complete=True)


@mempool_q.periodic_task(crontab(minute="*/1"))
def process_aged_propagations():
    agg = get_mempool_aggregator()
    agg.process_all_aged()
