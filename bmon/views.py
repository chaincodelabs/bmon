import statistics
import datetime
from dataclasses import dataclass
from typing import Dict, List

from django.http import HttpResponse
from django.shortcuts import render

from bmon_infra.infra import Host, get_bitcoind_hosts
from .models import ConnectBlockEvent
from .bitcoin.api import gather_rpc, RPC_ERROR_RESULT


@dataclass
class BlockConnView:
    height: int
    events: List[ConnectBlockEvent]

    def __post_init__(self):
        if not self.events:
            return

        def fromts(ts):
            return datetime.datetime.fromtimestamp(ts)

        times = {e.host: e.timestamp.timestamp() for e in self.events}
        self.date = self.events[0].date
        self.avg_got_time: datetime.datetime = fromts(statistics.mean(times.values()))
        self.stddev_got_time: float = statistics.pstdev(times.values())
        self.min: float = min(times.values())
        self.min_dt = fromts(self.min)
        self.diffs: Dict[str, float] = {host: t - self.min for host, t in times.items()}


def home(request):
    context = {'blockconnects': [], 'hosts': []}
    hosts = get_bitcoind_hosts()
    peer_info = gather_rpc(lambda r: r.getpeerinfo())
    chain_info = gather_rpc(lambda r: r.getblockchaininfo())

    for host in hosts:
        peers = peer_info[host.name]
        if peers == RPC_ERROR_RESULT:
            peers = []

        chain = chain_info[host.name]
        if chain == RPC_ERROR_RESULT:
            continue

        host.peers = {p['addr']: p['subver'] for p in peers}
        host.chaininfo = chain
        context['hosts'].append(host)

    heights = list(
        ConnectBlockEvent.objects.values_list("height", flat=True)
        .order_by("-height")
        .distinct()[:10]
    )
    cbs = list(ConnectBlockEvent.objects.filter(height__in=heights))

    for height in heights:
        height_cbs = [cb for cb in cbs if cb.height == height]
        context['blockconnects'].append(BlockConnView(height, height_cbs))

    return render(request, "bmon/home.html", context)
