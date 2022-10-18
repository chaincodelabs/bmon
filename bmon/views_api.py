from ninja import NinjaAPI
from django.forms.models import model_to_dict

from bmon import models
from bmon_infra.infra import get_hosts

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


@api.get('/mempool')
def mempool(_):
    mempool_accepts = models.MempoolAccept.objects.order_by('-id')[:400]
    return [model_to_dict(m) for m in mempool_accepts]


@api.get('/process-errors')
def process_errors(_):
    objs = models.ProcessLineError.objects.order_by('-id')[:400]
    return [model_to_dict(m) for m in objs]
