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
import json
import os
import typing as t
from string import Template
from pathlib import Path
from types import SimpleNamespace

import yaml
from clii import App
from fscm.contrib import wireguard
from fscm import run, p


cli = App()
ENV = SimpleNamespace()
ENVD = {}

env_template = """
BMON_ENV=${bmon_env}
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
WEB_API_URL=${web_api_url}
SMTP_PASSWORD=${smtp_password}
SMTP_HOST=${smtp_host}
SMTP_USERNAME=${smtp_username}

BITCOIN_RPC_HOST=${bitcoin_rpc_host}
BITCOIN_RPC_PORT=${bitcoin_rpc_port}
BITCOIN_RPC_USER=${bitcoin_rpc_user}
BITCOIN_RPC_PASSWORD=${bitcoin_rpc_password}

LOKI_PORT=${loki_port}
LOKI_HOST=${loki_host}
LOKI_ADDRESS=${loki_host}:${loki_port}

ALERTMAN_ADDRESS=${alertman_address}

PROMTAIL_PORT=${promtail_port}
BITCOIN_DATA_PATH=${bitcoin_data_path}
BITCOIN_FLAGS=${bitcoin_flags}
BITCOIN_PRUNE=${bitcoin_prune}
BITCOIN_DBCACHE=${bitcoin_dbcache}

# Used to control which bitcoind container is pulled in docker-compose.yml.
BITCOIN_DOCKER_TAG=${bitcoin_docker_tag}

BITCOIN_GITSHA=${bitcoin_gitsha}
BITCOIN_GITREF=${bitcoin_gitref}
BITCOIN_VERSION=${bitcoin_version}

# Version information, as actually written out by `bitcoind -version` after the
# container is built.
BITCOIND_VERSION_PATH=${bitcoind_version_path}

PUSHOVER_USER=${pushover_user}
PUSHOVER_TOKEN=${pushover_token}

CHAINCODE_GCP_CRED_PATH=/chaincode-gcp.json

SENTRY_DSN=${sentry_dsn}
"""


DEV_HOSTS_FILE = "./infra/hosts_dev.yml"

dev_settings = dict(
    bmon_env="dev",
    compose_profiles="bitcoind,server",
    hosts_file=DEV_HOSTS_FILE,
    root="./services/dev",
    uid=1000,
    db_host="db",
    db_password="bmon",
    redis_server_host="redis",
    redis_local_host="redis",
    prom_address="prom:9090",
    prom_exporter_port=9100,
    bitcoind_exporter_port=9332,
    web_api_url="http://web:8080/",
    bitcoin_rpc_host="bitcoind",
    bitcoin_rpc_port=18443,
    bitcoin_rpc_user="foo",
    bitcoin_rpc_password="bar",
    loki_port=3100,
    loki_host="loki",
    alertman_address="alertman:9093",
    promtail_port=9080,
    bitcoin_data_path="./services/dev/bitcoin/data/regtest",
    bitcoin_flags="-regtest",
    bitcoin_prune=0,
    bitcoin_dbcache=450,
    hostname=socket.gethostname(),
    pushover_user="",
    pushover_token="",
    debug=1,
    sentry_dsn="",
    bitcoind_version_path="./services/dev/bmon/bitcoind_version",
    smtp_password="",
    smtp_host="",
    smtp_username="",
)


BMON_SSHKEY = Path.home() / ".ssh" / "bmon-ed25519"


class Host(wireguard.Host):
    def __init__(
        self,
        *args,
        bitcoin_docker_tag: str | None = None,
        bitcoin_prune: int = 0,
        bitcoin_dbcache: int = 450,
        bitcoin_extra_args: str | None = None,
        prom_exporter_port: int | None = 9100,
        bitcoind_exporter_port: int | None = 9332,
        outbound_wireguard: str | None = None,
        **kwargs,
    ):
        self.bitcoin_docker_tag = bitcoin_docker_tag
        self.bitcoin_prune = bitcoin_prune
        self.bitcoin_dbcache = bitcoin_dbcache
        self.bitcoin_extra_args = bitcoin_extra_args
        self.prom_exporter_port = prom_exporter_port
        self.bitcoind_exporter_port = bitcoind_exporter_port
        self.outbound_wireguard = outbound_wireguard

        if BMON_SSHKEY.exists():
            kwargs.setdefault("ssh_identity_file", BMON_SSHKEY)

        super().__init__(*args, **kwargs)

    @property
    def bmon_ip(self):
        """An IP that makes the host routable to any other bmon host."""
        return self.wireguards["wg-bmon"].ip


def get_hosts(
    hosts_file_path: str | None = None,
) -> tuple[dict[str, wireguard.Server], dict[str, Host]]:
    """Return Host objects."""
    hostsfile = Path(hosts_file_path or os.environ["BMON_HOSTS_FILE"])
    data = yaml.safe_load(hostsfile.read_text())
    hosts = {str(name): Host.from_dict(name, d) for name, d in data["hosts"].items()}

    wg_servers: t.Dict[str, wireguard.Server] = {
        name: wireguard.Server.from_dict(name, d)
        for name, d in (data.get("wireguard") or {}).items()
    }

    return wg_servers, hosts  # type: ignore


def get_dev_host() -> Host:
    return [
        i for i in list(get_hosts(DEV_HOSTS_FILE)[1].values()) if i.name == "bitcoind"
    ][0]


def get_bitcoind_hosts() -> t.Tuple[Host, ...]:
    hosts = get_hosts()[1].values()
    return tuple(h for h in hosts if "bitcoind" in h.tags)


def dev_env(host) -> str:
    dev_settings.update(get_bitcoin_image_labels(host))
    dev_settings.update(
        bitcoin_docker_tag=host.bitcoin_docker_tag)
    return Template(env_template).substitute(**dev_settings)


def get_bitcoin_image_labels(host) -> dict:
    """
    TODO users of bmon shouldn't be required to make use of my docker image; figure
        out a way to optionally fall back to more primitive means of getting bitcoind
        params.
    """
    run(f"docker pull {host.bitcoin_docker_tag}")
    labels = json.loads(
        run(f"docker image inspect {host.bitcoin_docker_tag}", q=True)
        .assert_ok()
        .stdout
    )[0]["Config"]["Labels"]
    assert "bitcoin-version" in labels

    return dict(
        bitcoin_gitsha=labels["git-sha"],
        bitcoin_gitref=labels["git-ref"],
        bitcoin_version=labels["bitcoin-version"],
    )


def prod_settings(host, server_wireguard_ip: str) -> dict:
    # Don't print to console in prod; everything is done on the basis of the debug.log
    # anyway, so the stdout will just waste journald space.
    bitcoin_flags = f"-printtoconsole=0 {host.bitcoin_extra_args or ''}".strip()

    if host.bitcoin_prune is not None:
        bitcoin_flags += f" -prune={host.bitcoin_prune}"
    if host.bitcoin_dbcache is not None:
        bitcoin_flags += f" -dbcache={host.bitcoin_dbcache}"

    settings = dict(dev_settings)
    settings.update(
        bmon_env="prod",
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
        bitcoin_docker_tag=(host.bitcoin_docker_tag or ""),
        bitcoin_rpc_password=host.secrets.bitcoin_rpc_password,
        bmon_hostnmae=host.name,
        bitcoin_rpc_port=8332,
        bitcoin_rpc_user="bmon",
        sentry_dsn=host.secrets.sentry_dsn,
    )

    if "server" in host.tags:
        # Many of these services are running in compose.
        settings.update(
            compose_profiles="server,prod",
            db_host="db",
            redis_server_host="redis",
            prom_address="prom:9090",
            web_api_url="http://web:8080",
            loki_host="loki",
            alertman_address="alertman:9093",
            pushover_user=host.secrets.pushover.user,
            pushover_token=host.secrets.pushover.token,
            bitcoind_version_path="",
            smtp_password=host.secrets.smtp_password,
            smtp_host=host.secrets.smtp_host,
            smtp_username=host.secrets.smtp_username,

            # On bitcoind hosts, these are filled in from container labels. Because
            # *some* value is required for `Template.substitute`, put some dummies here.
            bitcoin_gitsha="",
            bitcoin_gitref="",
            bitcoin_version="",
        )
    else:
        # a bitcoind instance
        settings.update(
            compose_profiles="bitcoind,prod,prod-bitcoind",
            db_host=server_wireguard_ip,
            redis_server_host=server_wireguard_ip,
            redis_local_host="redis-bitcoind",
            prom_address=f"{server_wireguard_ip}:9090",
            web_api_url=f"http://{server_wireguard_ip}:8080",
            loki_host=server_wireguard_ip,
            alertman_address=f"{server_wireguard_ip}:9093",
            bitcoind_version_path="./services/prod/bmon/bitcoind_version",
        )
        settings.update(get_bitcoin_image_labels(host))

    return settings


def prod_env(host, server_wireguard_ip: str) -> str:
    settings = prod_settings(host, server_wireguard_ip)
    return Template(env_template).substitute(**settings)


def template_with_env(file_path: str) -> str:
    return Template(Path(file_path).read_text()).substitute(**ENVD)


def get_bitcoind_auth_line(username: str, password: str):
    """Copied from `./share/rpcauth/rpcauth.py`"""
    import hmac

    # Normally fixing the salt wouldn't be advisable, but we want the conf file to be
    # deterministic.
    salt = "a05b6fb53780e0b460cdd7387287f426"
    m = hmac.new(bytearray(salt, "utf-8"), bytearray(password, "utf-8"), "SHA256")
    password_hmac = m.hexdigest()
    return f"rpcauth={username}:{salt}${password_hmac}"


def make_services_data(envtype: str):
    user = getpass.getuser()
    p(root := Path(ENV.ENV_ROOT)).mkdir()

    p(root / "postgres" / "data").mkdir()

    p(grafetc := root / "grafana" / "etc").mkdir()
    p(grafetc / "grafana.ini").contents(template_with_env("./etc/grafana-template.ini"))
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
    p(datasources / "datasource.yml").contents(
        template_with_env("./etc/grafana-datasources-template.yml")
    )

    p(lokipath := root / "loki").mkdir()
    p(lokietc := lokipath / "etc").mkdir()
    p(lokietc / "local-config.yaml").contents(
        template_with_env("./etc/loki-template.yml")
    )

    p(prometc := root / "prom" / "etc").mkdir()
    p(root / "prom" / "data").mkdir()
    p(prometc / "prometheus.yml").contents(template_with_env("./etc/prom-template.yml"))
    p(prometc / "alerts.yml").contents(Path("./etc/prom-alerts.yml").read_text())

    p(am := root / "alertman").mkdir()
    p(am / "data").mkdir().chown(f"{user}:{user}")
    p(am / "config.yml").contents(template_with_env("./etc/alertmanager-template.yml"))

    auth_line = get_bitcoind_auth_line(ENV.BITCOIN_RPC_USER, ENV.BITCOIN_RPC_PASSWORD)
    bitcoin_conf = Template(
        Path("./etc/bitcoin/bitcoin-template.conf").read_text()
    ).substitute(
        RPC_AUTH_LINE=auth_line,
    )

    p(btcdata := root / "bitcoin" / "data").mkdir()
    p(btcdata / "regtest").mkdir()
    p(btcdata / "bitcoin.conf").contents(bitcoin_conf)

    if envtype == "dev":
        p(btcdata2 := root / "bitcoin-02" / "data").mkdir()
        p(btcdata2 / "regtest").mkdir()
        p(btcdata2 / "bitcoin.conf").contents(bitcoin_conf)

    p(promtailp := root / "promtail").mkdir()
    p(promtailp / "config.yml").contents(
        template_with_env("./etc/promtail-template.yml")
    )

    p(root / "redis" / "data").mkdir()

    p(root / "bmon" / "mempool-activity-cache").mkdir()
    p(root / "bmon" / "credentials").mkdir()


def get_env_object(envfile: str | Path = ".env") -> SimpleNamespace:
    """Return the contents of the .env file in a namespace."""
    lines = Path(envfile).read_text().splitlines()
    lines = [line for line in lines if line and not line.startswith("#")]
    envdict = dict(i.split("=", 1) for i in lines)

    return SimpleNamespace(**envdict)


@cli.main
@cli.arg("envfile", "-e")
@cli.arg("envtype", "-t", help="The type of environment. Choices: dev, prod")
def make_env(
    envfile: str = ".env",
    envtype: str = "dev",
):
    # Don't autopopulate .env on prod; this happens in infra:deploy.
    if envtype == "dev":
        host = get_dev_host()
        p(envfile).contents(dev_env(host))

    global ENV
    global ENVD
    ENV = get_env_object(envfile)
    ENVD = ENV.__dict__
    make_services_data(envtype)


def main():
    cli.run()


if __name__ == "__main__":
    main()
