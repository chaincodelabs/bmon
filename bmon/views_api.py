import datetime
import statistics
from dataclasses import dataclass

from ninja import NinjaAPI
from django.forms.models import model_to_dict

from bmon import models
from .bitcoin.api import gather_rpc, RPC_ERROR_RESULT
from bmon_infra import infra

api = NinjaAPI()


def _get_wireguard_ip(host):
    bmon_wg = host.wireguards['wg-bmon']
    return bmon_wg.ip


@api.get('/prom-config-bitcoind')
def prom_config_bitcoind(_):
    """Dynamic configuration for bitcoind prometheus monitoring endpoints."""
    bitcoind_hosts = [h for h in infra.get_hosts()[1].values() if 'bitcoind' in h.tags]
    out = []

    for host in bitcoind_hosts:
        wgip = _get_wireguard_ip(host)
        targets = [
            f'{wgip}:{host.bitcoind_exporter_port}',
            f'{wgip}:{infra.BMON_BITCOIND_EXPORTER_PORT}',
        ]
        if host.prom_exporter_port:
            targets.append(f'{wgip}:{host.prom_exporter_port}')

        out.append({
            'targets': targets,
            'labels': {
                'job': 'bitcoind',
                'hostname': host.name,
                'bitcoin_version': host.bitcoin_version,
                'bitcoin_dbcache': str(host.bitcoin_dbcache),
                'bitcoin_prune': str(host.bitcoin_prune),
            },
        })

    return out


@api.get('/prom-config-server')
def prom_config_server(_):
    """Dynamic configuration for bmon server prometheus monitoring endpoints."""
    hosts = infra.get_hosts()[1].values()
    [server] = [h for h in hosts if 'server' in h.tags]
    wgip = _get_wireguard_ip(server)

    return [{
        'targets': [
            f'{wgip}:{server.prom_exporter_port}',
            f'{wgip}:{infra.SERVER_EXPORTER_PORT}',
        ],
        'labels': {'job': 'server', 'hostname': server.name},
    }]


@api.get('/hosts')
def hosts(_):
    out = []
    hosts = infra.get_bitcoind_hosts()
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
        self.diffs: dict[str, float] = {host: t - self.min for host, t in times.items()}
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


@api.get('/crash')
def crash(_):
    """for testing sentry"""
    return 1 / 0
