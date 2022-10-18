import datetime
import statistics
from dataclasses import dataclass

from ninja import NinjaAPI
from django.forms.models import model_to_dict

from bmon import models
from .bitcoin.api import gather_rpc, RPC_ERROR_RESULT
from bmon_infra.infra import get_hosts, Host, get_bitcoind_hosts

api = NinjaAPI()


@api.get('/prom-config')
def prom_scrape_config(_):
    def get_wireguard_ip(host):
        bmon_wg = host.wireguards['wg-bmon']
        return bmon_wg.ip

    hosts = get_hosts()[1].values()
    bitcoind_hosts = [h for h in hosts if 'bitcoind' in h.tags]
    [server] = [h for h in hosts if 'server' in h.tags]

    targets = [
        {
            'targets': list(filter(None, [
                f'{get_wireguard_ip(host)}:{host.bitcoind_exporter_port}',
                (
                    f'{get_wireguard_ip(host)}:{host.prom_exporter_port}' if
                    host.prom_exporter_port else ''
                ),
            ])),
            'labels': {
                'job': 'bitcoind',
                'hostname': host.name,
                'bitcoin_version': host.bitcoin_version,
                'bitcoin_dbcache': str(host.bitcoin_dbcache),
                'bitcoin_prune': str(host.bitcoin_prune),
            },
        }
        for host in bitcoind_hosts
    ]

    targets.append({
        'targets': [f'{get_wireguard_ip(server)}:{server.prom_exporter_port}'],
        'labels': {'job': 'server', 'hostname': server.name},
    })
    return targets


@api.get('/hosts')
def hosts(_):
    out = []
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

        out.append({
            'name': host.name,
            'peers': {p['addr']: p['subver'] for p in peers},
            'chaininfo': chain,
            'bitcoin_version': host.bitcoin_version,
        })

    return out


@dataclass
class BlockConnView:
    height: int
    events: list[models.ConnectBlockEvent]

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
        self.events = []


@api.get('/blocks')
def blocks(_):
    out = []
    heights = list(
        models.ConnectBlockEvent.objects.values_list("height", flat=True)
        .order_by("-height")
        .distinct()[:10]
    )
    cbs = list(models.ConnectBlockEvent.objects.filter(height__in=heights))

    for height in heights:
        height_cbs = [cb for cb in cbs if cb.height == height]
        out.append(BlockConnView(height, height_cbs).__dict__)

    return out


@api.get('/mempool')
def mempool(_):
    mempool_accepts = models.MempoolAccept.objects.order_by('-id')[:400]
    return [model_to_dict(m) for m in mempool_accepts]


@api.get('/process-errors')
def process_errors(_):
    objs = models.ProcessLineError.objects.order_by('-id')[:400]
    return [model_to_dict(m) for m in objs]
