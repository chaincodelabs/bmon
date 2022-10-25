"""
Tasks that execute on the central bmon server.

There are only one of these workers, so here is where we queue up periodic analysis
tasks that should only be run in one place, with a view of the whole herd of bitcoind
nodes.
"""
from collections import defaultdict
import os
import logging

import django
from django.conf import settings
from huey import RedisHuey, crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bmon.settings")
django.setup()

from bmon import models
from bmon.bitcoin.api import gather_rpc, RPC_ERROR_RESULT


log = logging.getLogger(__name__)

server_q = RedisHuey("bmon-server", url=settings.REDIS_SERVER_URL)


@server_q.periodic_task(crontab(minute="*/10"))
def check_for_overlapping_peers():
    def getpeerinfo(rpc):
        return rpc.getpeerinfo()

    results = gather_rpc(getpeerinfo)
    peer_to_hosts = defaultdict(list)
    hosts_contacted = []

    for hostname, peers in results.items():
        if peers == RPC_ERROR_RESULT:
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
        event['peer_id'] = event.pop('peer')

    instance = Model.objects.create(**event)
    print(f"Saved {instance}")
