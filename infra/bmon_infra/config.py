#!/usr/bin/env python3
"""
Handles generation of .env configuration for both the bmon server and any
monitored bitcoind instances.

The configuration in .env is used in the docker-compose file, which is then fed
into each consituent service.

See also: ./infra.py, for how this is used to populate configuration on each host.

"""

import socket
import getpass
import typing as t
from string import Template
from pathlib import Path
from types import SimpleNamespace

from fscm import p
from clii import App


cli = App()
ENV = SimpleNamespace()

env_template = """
COMPOSE_PROFILES=${compose_profiles}
BMON_HOSTS_FILE=${hosts_file}
ENV_ROOT=${root}
UID=${uid}
BMON_HOSTNAME=${hostname}
BMON_DEBUG=${debug}

DB_HOST=${db_host}
DB_PASSWORD=${db_password}

BMON_REDIS_HOST=${redis_server_host}
BMON_REDIS_LOCAL_HOST=${redis_local_host}
BMON_REDIS_LOCAL_URL=redis://${redis_local_host}:6379/0
BMON_REDIS_SERVER_URL=redis://${redis_server_host}:6379/1

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
BITCOIN_DATA_PATH=${bitcoin_data_path}
BITCOIN_FLAGS=${bitcoin_flags}
BITCOIN_PRUNE=${bitcoin_prune}
BITCOIN_DBCACHE=${bitcoin_dbcache}
BITCOIN_DOCKER_TAG=${bitcoin_docker_tag}

PUSHOVER_USER=${pushover_user}
PUSHOVER_TOKEN=${pushover_token}
"""


dev_settings = dict(
    compose_profiles='bitcoind,server',
    hosts_file="./infra/hosts_dev.yml",
    root="./services/dev",
    uid=1000,
    db_host="db",
    db_password="bmon",
    redis_server_host="redis",
    redis_local_host="redis",
    prom_address="prom:9090",
    prom_exporter_port=9100,
    bitcoind_exporter_port=9332,
    prom_scrape_sd_url="http://web:8080/api/prom-config",
    bitcoin_rpc_host="bitcoind",
    bitcoin_rpc_port=18443,
    bitcoin_rpc_user="foo",
    bitcoin_rpc_password="bar",
    loki_port=3100,
    loki_host="loki",
    alertman_address="alertman:9093",
    promtail_port=9080,
    bitcoin_git_sha="?",
    bitcoin_version="?",
    bitcoin_data_path="./services/dev/bitcoin/data/regtest",
    bitcoin_flags="-regtest",
    bitcoin_prune=0,
    bitcoin_dbcache=None,
    bitcoin_docker_tag='latest',
    hostname=socket.gethostname(),
    pushover_user="",
    pushover_token="",
    debug=1,
)


def dev_env() -> str:
    return Template(env_template).substitute(**dev_settings)


def prod_settings(host, server_wireguard_ip: str) -> dict:
    # Don't print to console in prod; everything is done on the basis of the debug.log
    # anyway, so the stdout will just waste journald space.
    bitcoin_flags = "-printtoconsole=0"

    if host.bitcoin_prune is not None:
        bitcoin_flags += f" -prune={host.bitcoin_prune}"
    if host.bitcoin_dbcache is not None:
        bitcoin_flags += f" -dbcache={host.bitcoin_dbcache}"

    settings = dict(dev_settings)
    settings.update(
        debug=0,
        root="./services/prod",
        hosts_file="./infra/hosts_prod.yml",
        hostname=host.name,
        db_password=host.secrets.db_password,
        bitcoin_network="",
        bitcoin_data_path="./services/prod/bitcoin/data",
        bitcoin_flags=bitcoin_flags,
        bitcoin_prune=host.bitcoin_prune,
        bitcoin_dbcache=host.bitcoin_dbcache,
        bitcoin_version=host.bitcoin_version,
        bitcoin_docker_tag=(host.bitcoin_version or '?').lstrip('v'),
        bmon_hostnmae=host.name,
    )

    if 'server' in host.tags:
        # Many of these services are running in compose.
        settings.update(
            compose_profiles='server,prod',
            root="./services/prod",
            db_host="db",
            redis_server_host="redis",
            prom_address="prom:9090",
            prom_scrape_sd_url="http://web:8080/api/prom-config",
            bitcoin_rpc_port=8332,
            bitcoin_rpc_user="bmon",
            bitcoin_rpc_password=host.secrets.bitcoin_rpc_password,
            loki_host="loki",
            alertman_address="alertman:9093",
            pushover_user=host.secrets.pushover.user,
            pushover_token=host.secrets.pushover.token,
        )
    else:
        # a bitcoind instance
        settings.update(
            compose_profiles='bitcoind,prod,prod-bitcoind',
            db_host=server_wireguard_ip,
            redis_server_host=server_wireguard_ip,
            redis_local_host="redis-bitcoind",
            prom_address=f"{server_wireguard_ip}:9090",
            prom_scrape_sd_url=f"http://{server_wireguard_ip}/api/prom-config",
            bitcoin_rpc_port=8332,
            bitcoin_rpc_user="bmon",
            bitcoin_rpc_password=host.secrets.bitcoin_rpc_password,
            loki_host=server_wireguard_ip,
            alertman_address=f"{server_wireguard_ip}:9093",
        )

    return settings


def prod_env(host, server_wireguard_ip: str) -> str:
    settings = prod_settings(host, server_wireguard_ip)
    return Template(env_template).substitute(**settings)


def grafana():
    return Path("./etc/grafana-template.ini").read_text()


def grafana_datasources():
    return Template(
        Path("./etc/grafana-datasources-template.yml").read_text()
    ).substitute(
        PROM_ADDRESS=ENV.PROM_ADDRESS,
        LOKI_ADDRESS=ENV.LOKI_ADDRESS,
        ALERTMAN_ADDRESS=ENV.ALERTMAN_ADDRESS,
    )


def prom():
    return Template(Path("./etc/prom-template.yml").read_text()).substitute(
        ALERTMAN_ADDRESS=ENV.ALERTMAN_ADDRESS,
        PROM_SCRAPE_SD_URL=ENV.PROM_SCRAPE_SD_URL,
    )


def loki():
    return Template(Path("./etc/loki-template.yml").read_text()).substitute(
        LOKI_PORT=ENV.LOKI_PORT,
        ALERTMAN_ADDRESS=ENV.ALERTMAN_ADDRESS,
    )


def alertman():
    return Template(Path("./etc/alertmanager-template.yml").read_text()).substitute(
        PUSHOVER_TOKEN=ENV.PUSHOVER_TOKEN,
        PUSHOVER_USER=ENV.PUSHOVER_USER,
    )


def promtail(hostname: str | None = None):
    hostname = hostname or socket.gethostname()

    return Template(Path("./etc/promtail-template.yml").read_text()).substitute(
        PROMTAIL_PORT=ENV.PROMTAIL_PORT,
        LOKI_ADDRESS=ENV.LOKI_ADDRESS,
        HOSTNAME=hostname,
        BITCOIN_GIT_SHA=ENV.BITCOIN_GIT_SHA,
        BITCOIN_VERSION=ENV.BITCOIN_VERSION,
        BITCOIN_PRUNE=ENV.BITCOIN_PRUNE,
        BITCOIN_DBCACHE=ENV.BITCOIN_DBCACHE,
    )


def bitcoind():
    auth_line = get_bitcoind_auth_line(ENV.BITCOIN_RPC_USER, ENV.BITCOIN_RPC_PASSWORD)
    return Template(Path("./etc/bitcoin/bitcoin-template.conf").read_text()).substitute(
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


def make_services_data(hostname: str | None = None):
    user = getpass.getuser()
    p(root := Path(ENV.ENV_ROOT)).mkdir()

    p(root / 'postgres' / 'data').mkdir()

    p(grafetc := root / "grafana" / "etc").mkdir()
    p(grafetc / "grafana.ini").contents(grafana())
    p(var := root / "grafana" / "var").mkdir()
    p(dashboards := var / "dashboards").mkdir()
    p(dashboards / "bitcoind.json").contents(
        Path("./etc/grafana/dashboards/bitcoind.json").read_text()
    )
    p(prov := grafetc / "provisioning").mkdir()
    p(datasources := prov / "datasources").mkdir()
    p(dashprov := prov / "dashboards").mkdir()
    p(dashprov / "default.yml").contents(
        Path("./etc/grafana-dashboards-template.yml").read_text()
    )
    p(datasources / "datasource.yml").contents(grafana_datasources())

    p(lokipath := root / "loki").mkdir()
    p(lokietc := lokipath / "etc").mkdir()
    p(lokietc / "local-config.yaml").contents(loki())

    p(prometc := root / "prom" / "etc").mkdir()
    p(root / "prom" / "data").mkdir()
    p(prometc / "prometheus.yml").contents(prom())
    p(prometc / "alerts.yml").contents(Path("./etc/prom-alerts.yml").read_text())

    p(am := root / "alertman").mkdir()
    p(am / 'data').mkdir().chown(f'{user}:{user}')
    p(am / "config.yml").contents(alertman())

    p(btcdata := root / "bitcoin" / "data").mkdir()
    p(btcdata / "bitcoin.conf").contents(bitcoind())

    p(promtailp := root / "promtail").mkdir()
    p(promtailp / "config.yml").contents(promtail(hostname))

    p(root / 'redis' / 'data').mkdir()


@cli.main
@cli.arg("envfile", "-e")
@cli.arg("envtype", "-t", help="The type of environment. Choices: dev, prod")
def make_env(
    envfile: str = ".env",
    envtype: str = "dev",
    envdict: t.Optional[dict] = None,
    hostname: str = '',
):
    if envtype == "dev":
        p(envfile).contents(dev_env())
    else:
        # Don't autopopulate .env on prod.
        pass

    if not envdict:
        # Read from the envfile
        envdict = dict(
            i.split("=", 1)
            for i in filter(None, Path(envfile).read_text().splitlines())
        )

    global ENV
    ENV = SimpleNamespace(**envdict)
    make_services_data(hostname)


def main():
    cli.run()


if __name__ == "__main__":
    main()
