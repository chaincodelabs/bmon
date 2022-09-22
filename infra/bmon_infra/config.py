#!/usr/bin/env python3

from string import Template
from pathlib import Path
from types import SimpleNamespace
import typing as t

from fscm import p
from clii import App


cli = App()
ENV = SimpleNamespace()

env_template = """
ENV_ROOT=${root}
UID=${uid}

BMON_DATABASE_HOST=${db_host}
BMON_DATABASE_PORT=${db_port}
BMON_DATABASE_PASSWORD=${db_password}
BMON_DATABASE_URL=${db_url}

BMON_REDIS_HOST=${redis_central_host}
BMON_REDIS_LOCAL_HOST=${redis_local_host}
BMON_REDIS_LOCAL_URL=redis://${redis_local_host}:6379/0
BMON_REDIS_CENTRAL_URL=redis://${redis_central}:6379/1

PROM_ADDRESS=${prom_address}
PROM_EXPORTER_PORT=${prom_exporter_port}
BITCOIND_EXPORTER_PORT=${bitcoind_exporter_port}
PROM_SCRAPE_SD_URL=${prom_scrape_sd_url}

BITCOIN_RPC_HOST=${bitcoin_rpc_host}
BITCOIN_RPC_PORT=${bitcoin_rpc_port}
BITCOIN_RPC_USER=${bitcoin_rpc_user}
BITCOIN_RPC_PASSWORD=${bitcoin_rpc_password}

LOKI_PORT=${loki_port}
LOKI_HOST=${loki_host}
LOKI_ADDRESS=${loki_host}:${loki_port}

ALERTMAN_ADDRESS=${alertman_address}

PROMTAIL_PORT=${promtail_port}
BITCOIN_GIT_SHA=${bitcoin_git_sha}
BITCOIN_VERSION=${bitcoin_version}
BITCOIN_NETWORK_FLAG=${bitcoin_network}
"""


dev_settings = dict(
    root="./services/dev",
    uid=1000,
    db_host='bmon',
    db_password='bmon',
    db_port=5432,
    db_url='postgres://bmon:bmon@db:5432/bmon',
    redis_central_host='redis',
    redis_local_host='redis',
    prom_address='prom:9090',
    prom_exporter_port=9100,
    bitcoind_exporter_port=9332,
    prom_scrape_sd_url='http://web:8080/prom_scrape_config',
    bitcoin_rpc_host='bitcoind',
    bitcoin_rpc_port=18443,
    bitcoin_rpc_user='foo',
    bitcoin_rpc_password='bar',
    loki_port=3100,
    loki_host="loki",
    alertman_address="alertman:9093",
    promtail_port=9080,
    bitcoin_git_sha="?",
    bitcoin_version="?",
    bitcoin_network='-regtest',
)


def dev_env() -> str:
    return Template(env_template).substitute(**dev_settings)


def prod_settings(
    is_server: bool,
    db_password: str,
    bitcoin_rpc_password: str,
    bitcoin_git_sha: str = '?',
    bitcoin_version: str = '?',
) -> dict:
    servername = 'bmon.lan'

    prod_settings = dict(dev_settings)
    prod_settings.update(
        root="./services/prod",
        db_password=db_password,
        bitcoin_network='',
    )

    if is_server:
        # Many of these services are running in compose.
        prod_settings.update(
            root="./services/prod",
            db_host='db',
            db_url=f'postgres://bmon:{db_password}@db:5432/bmon',
            redis_central_host='redis',
            prom_address='prom:9090',
            prom_scrape_sd_url='http://web:8080/prom_scrape_config',
            bitcoin_rpc_port=8332,
            bitcoin_rpc_user='bmon',
            bitcoin_rpc_password=bitcoin_rpc_password,
            loki_host='loki',
            alertman_address='alertman:9093',
            bitcoin_git_sha=bitcoin_git_sha,
            bitcoin_version=bitcoin_version,
        )
    else:
        # a bitcoind instance
        prod_settings.update(
            db_host=servername,
            db_url=f'postgres://bmon:{db_password}@{servername}:5432/bmon',
            redis_central_host=servername,
            redis_local_host='redis-bitcoind',
            prom_address=f'{servername}:9090',
            prom_scrape_sd_url=f'http://{servername}/prom_scrape_config',
            bitcoin_rpc_port=8332,
            bitcoin_rpc_user='bmon',
            bitcoin_rpc_password=bitcoin_rpc_password,
            loki_host=servername,
            alertman_address=f'{servername}:9093',
            bitcoin_git_sha=bitcoin_git_sha,
            bitcoin_version=bitcoin_version,
        )

    return prod_settings


def prod_env(*args, **kwargs) -> str:
    settings = prod_settings(*args, **kwargs)
    return Template(env_template).substitute(**settings)


def grafana():
    return Path('./etc/grafana-template.ini').read_text()


def grafana_datasources():
    return Template(Path('./etc/grafana-datasources-template.yml').read_text()).substitute(
        PROM_ADDRESS=ENV.PROM_ADDRESS,
        LOKI_ADDRESS=ENV.LOKI_ADDRESS,
        ALERTMAN_ADDRESS=ENV.ALERTMAN_ADDRESS,
    )


def prom():
    return Template(Path('./etc/prom-template.yml').read_text()).substitute(
        ALERTMAN_ADDRESS=ENV.ALERTMAN_ADDRESS,
        PROM_SCRAPE_SD_URL=ENV.PROM_SCRAPE_SD_URL,
    )


def loki():
    return Template(Path('./etc/loki-template.yml').read_text()).substitute(
        LOKI_PORT=ENV.LOKI_PORT,
        ALERTMAN_ADDRESS=ENV.ALERTMAN_ADDRESS,
    )


def alertman():
    return Path('./etc/alertmanager-template.yml').read_text()


def promtail():
    return Template(Path('./etc/promtail-template.yml').read_text()).substitute(
        PROMTAIL_PORT=ENV.PROMTAIL_PORT,
        LOKI_ADDRESS=ENV.LOKI_ADDRESS,
        HOSTNAME='?',
        BITCOIN_GIT_SHA='?',
        BITCOIN_VERSION='?',
    )


def bitcoind():
    auth_line = get_bitcoind_auth_line(ENV.BITCOIN_RPC_USER, ENV.BITCOIN_RPC_PASSWORD)
    return Template(Path('./etc/bitcoin/bitcoin-template.conf').read_text()).substitute(
        RPC_AUTH_LINE=auth_line,
    )


def get_bitcoind_auth_line(username: str, password: str):
    """Copied from `./share/rpcauth/rpcauth.py`"""
    import hmac

    # Normally fixing the salt wouldn't be advisable, but we want the conf file to be
    # deterministic.
    salt = "a05b6fb53780e0b460cdd7387287f426"
    m = hmac.new(bytearray(salt, "utf-8"), bytearray(password, "utf-8"), "SHA256")
    password_hmac = m.hexdigest()
    return f"rpcauth={username}:{salt}${password_hmac}"


def make_services_data():
    p(root := Path(ENV.ENV_ROOT)).mkdir()

    p(grafetc := root / 'grafana' / 'etc').mkdir()
    p(grafetc / 'grafana.ini').contents(grafana())
    p(var := root / 'grafana' / 'var').mkdir()
    p(dashboards := var / 'dashboards').mkdir()
    p(dashboards / 'bitcoind.json').contents(
        Path('./etc/grafana/dashboards/bitcoind.json').read_text())
    p(prov := grafetc / 'provisioning').mkdir()
    p(datasources := prov / 'datasources').mkdir()
    p(dashprov := prov / 'dashboards').mkdir()
    p(dashprov / 'default.yml').contents(
        Path('./etc/grafana-dashboards-template.yml').read_text())
    p(datasources / 'datasource.yml').contents(grafana_datasources())

    p(lokipath := root / 'loki').mkdir()
    p(lokietc := lokipath / 'etc').mkdir()
    p(lokietc / 'local-config.yaml').contents(loki())

    p(prometc := root / 'prom' / 'etc').mkdir()
    p(root / 'prom' / 'data').mkdir()
    p(prometc / 'prometheus.yml').contents(prom())

    p(am := root / 'alertman').mkdir()
    p(am / 'config.yml').contents(alertman())

    p(btcdata := root / 'bitcoin' / 'data').mkdir()
    p(btcdata / 'bitcoin.conf').contents(bitcoind())

    p(promtailp := root / 'promtail').mkdir()
    p(promtailp / 'config.yml').contents(promtail())


@cli.main
@cli.arg('envfile', '-e')
@cli.arg('envtype', '-t', help="The type of environment. Choices: dev, prod")
def make_env(envfile: str = '.env', envtype: str = 'dev', envdict: t.Optional[dict] = None):
    if envtype == 'dev':
        p(envfile).contents(dev_env())
    else:
        # Don't autopopulate .env on prod.
        pass

    if not envdict:
        # Read from the envfile
        envdict = dict(
            i.split('=', 1) for i in
            filter(None, Path(envfile).read_text().splitlines()))

    global ENV
    ENV = SimpleNamespace(**envdict)
    make_services_data()


def main():
    cli.run()


if __name__ == "__main__":
    main()
