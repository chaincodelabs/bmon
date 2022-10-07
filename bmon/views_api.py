from ninja import NinjaAPI

from bmon_infra.infra import get_bitcoind_hosts

api = NinjaAPI()


@api.get('/prom-config')
def prom_scrape_config(_):
    def get_wireguard_ip(host):
        bmon_wg = host.wireguards['wg-bmon']
        return bmon_wg.ip

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
        for host in get_bitcoind_hosts()
    ]
    return targets
