#!/usr/bin/env python3
import socket
import typing as t
from dataclasses import dataclass
from pathlib import Path
from string import Template

import fscm
from fscm import run, mitogen_context, file_, docker


def stdout(*args, **kwargs) -> str:
    kwargs.setdefault("quiet", True)
    kwargs.setdefault("check", True)
    return run(*args, **kwargs).stdout.strip()


# Used for non-destructive querying of the repo.
GIT_REPO = Path.home() / "src" / "bitcoin"


@dataclass
class Host:
    hostname: str
    username: str
    data_path: str


@dataclass
class BMonInstallation:
    grafana_host: Host
    loki_host: Host
    bitcoin_hostnames: t.List[str]

    def provision(self):
        with mitogen_context(
            self.grafana_host.hostname, username=self.grafana_host.username
        ) as (_, context):
            context.call(setup_grafana, self.grafana_host.data_path)
            context.call(setup_alertmanager, self.grafana_host.data_path)
            context.call(
                setup_prometheus, self.grafana_host.data_path, self.bitcoin_hostnames
            )

        with mitogen_context(
            self.loki_host.hostname, username=self.loki_host.username
        ) as (_, context):
            context.call(setup_loki, self.loki_host.data_path)


def setup_grafana(
    data_path: str,
    port: int = 3000,
):
    (datap := Path(data_path) / "grafana").mkdir(parents=True, exist_ok=True)
    (etc := datap / "etc").mkdir(parents=True, exist_ok=True)
    (var := datap / "var").mkdir(parents=True, exist_ok=True)
    container_name = "bmon_grafana"

    uid = stdout("id -u", check=True)

    if not (config := etc / "grafana.ini").exists():
        gh_url = (
            "https://raw.githubusercontent.com/grafana/grafana/main/conf/sample.ini"
        )
        run(f'curl -L "{gh_url}" > {config}')

    if not docker.container_exists(container_name):
        flags = (
            f"-p {port}:{port} --user={uid} --network=host "
            f"-v {etc}:/etc/grafana "
            f"-v {var}:/var/lib/grafana "
            "--restart unless-stopped "
        )
        run(
            f"docker create --name={container_name} {flags} docker.io/grafana/grafana-enterprise"
        )

    if not docker.is_container_up(container_name):
        run(f"docker start {container_name}")


def setup_loki(
    data_path: str,
    port: int = 3100,
    alertmanager_host: str = "localhost",
    alertmanager_port: str = "9093",
):
    (datap := Path(data_path) / "loki").mkdir(parents=True, exist_ok=True)
    (etc := datap / "etc").mkdir(parents=True, exist_ok=True)
    container_name = "bmon_loki"

    uid = stdout("id -u", check=True)

    file_(
        etc / "local-config.yaml",
        Template(LOKI_CONFIG).substitute(
            ALERTMANAGER_HOST=alertmanager_host, ALERTMANAGER_PORT=alertmanager_port
        ),
    )

    if not docker.container_exists(container_name):
        flags = (
            f"-p {port}:{port} --user={uid} --network=host "
            f"-v {datap}:/loki "
            f"-v {etc}:/etc/loki "
            "--restart=unless-stopped "
        )
        run(
            f"docker create --name={container_name} {flags} docker.io/grafana/loki "
            "-config.file=/etc/loki/local-config.yaml"
        )

    if not docker.is_container_up(container_name):
        run(f"docker start {container_name}")


LOKI_CONFIG = """
auth_enabled: false

server:
  http_listen_address: 0.0.0.0
  http_listen_port: 3100
  grpc_listen_port: 9096

common:
  path_prefix: /loki
  storage:
    filesystem:
      chunks_directory: /loki/chunks
      rules_directory: /loki/rules
  replication_factor: 1
  ring:
    instance_addr: 127.0.0.1
    kvstore:
      store: inmemory

schema_config:
  configs:
    - from: 2020-10-24
      store: boltdb-shipper
      object_store: filesystem
      schema: v11
      index:
        prefix: index_
        period: 24h

ruler:
  alertmanager_url: http://${ALERTMANAGER_HOST}:${ALERTMANAGER_PORT}
"""


def setup_prometheus(
    data_path: str,
    bitcoin_hostnames: t.List[str],
    port: int = 9090,
):
    (datap := Path(data_path) / "prometheus").mkdir(parents=True, exist_ok=True)
    (etc := datap / "etc").mkdir(parents=True, exist_ok=True)
    container_name = "bmon_prometheus"

    prom_conf = Template(PROMETHEUS_CONFIG).substitute()

    PROM_EXPORTER_PORT = 9100
    BITCOIND_EXPORTER_PORT = 9332

    for hostname in bitcoin_hostnames:
        prom_conf += (
            f"  - job_name: {hostname}\n"
            f"    static_configs: \n"
            f"      - targets: ['{hostname}:{PROM_EXPORTER_PORT}', '{hostname}:{BITCOIND_EXPORTER_PORT}']\n\n"
        )

    restart_needed = file_(etc / "prometheus.yml", prom_conf)

    if not docker.container_exists(container_name):
        flags = (
            f"-p {port}:{port} "
            f"-v {etc}:/etc/prometheus "
            "--restart unless-stopped "
        )
        run(f"docker create --name={container_name} {flags} docker.io/prom/prometheus ")

    if not docker.is_container_up(container_name):
        run(f"docker start {container_name}")
    elif restart_needed:
        run(f"docker restart {container_name}")


PROMETHEUS_CONFIG = """
global:
  scrape_interval:     15s
  evaluation_interval: 15s

rule_files:
  # - "first.rules"
  # - "second.rules"

scrape_configs:
"""


def setup_alertmanager(
    data_path: str,
    config_template: str = None,
    port: int = 9093,
):
    (datap := Path(data_path) / "alertmanager").mkdir(parents=True, exist_ok=True)
    container_name = "bmon_alertmanager"

    uid = stdout("id -u", check=True)

    flags = f"-p {port}:{port} --user {uid} --restart unless-stopped "

    if config_template:
        # TODO actually populate config
        flags += f'-v {datap / "config.yaml"}:/etc/alertmanager/config.yaml '

    if not docker.container_exists(container_name):
        run(
            f"docker create --name={container_name} {flags} docker.io/prom/alertmanager "
            f"{'--config.file=/etc/alertmanager/config.yaml' if config_template else ''}"
        )

    if not docker.is_container_up(container_name):
        run(f"docker start {container_name}")


def setup_promtail(
    data_path: str,
    loki_address: str,
    bitcoin_version: str,
    bitcoin_git_sha: str,
    bitcoin_logs_path: str,
    port: int = 9080,
):
    (datap := Path(data_path) / "promtail").mkdir(parents=True, exist_ok=True)
    container_name = "bmon_promtail"

    file_(
        datap / "config.yaml",
        Template(PROMTAIL_CONF).substitute(
            LOKI_ADDRESS=loki_address,
            BITCOIN_GIT_SHA=bitcoin_git_sha,
            BITCOIN_VERSION=bitcoin_version,
            HOSTNAME=socket.gethostname(),
            PORT=port,
        ),
    )
    uid = stdout("id -u", check=True)
    if running_podman():
        uid = "root"  # hack

    flags = (
        f"-p {port}:{port} --user {uid} "
        f"-v {bitcoin_logs_path}:/bitcoin-debug.log "
        f"-v {datap}:/etc/promtail "
        "--restart unless-stopped "
    )

    if not docker.container_exists(container_name):
        run(
            f"docker create --name={container_name} {flags} docker.io/grafana/promtail "
            f"--config.file=/etc/promtail/config.yaml"
        )

    if not docker.is_container_up(container_name):
        run(f"docker start {container_name}")


PROMTAIL_CONF = """
server:
  http_listen_port: $PORT
  grpc_listen_port: 0

positions:
  filename: /tmp/positions.yaml

clients:
- url: http://${LOKI_ADDRESS}:/loki/api/v1/push

scrape_configs:
- job_name: system
  static_configs:
  - targets:
      - localhost
    labels:
      job: bitcoin
      host: $HOSTNAME
      version: ${BITCOIN_VERSION}
      gitsha: ${BITCOIN_GIT_SHA}
      __path__: /bitcoin-debug.log
"""


def running_podman():
    return run("which podman", quiet=True, check=False).ok


BITCOIND_CONF = """
{rpc_auth}
dbcache={dbcache}
"""


def setup_bitcoind(
    data_path: str, git_sha: str, rpc_auth_line: str, bootstrap: str, dockerfile: str
):
    fscm.dir(datadir := Path(data_path) / "bitcoin")
    fscm.file_(datadir / "bootstrap.py", bootstrap)
    fscm.make_executable(datadir / "bootstrap.py")
    fscm.file_(datadir / "Dockerfile", dockerfile)
    fscm.file_(
        datadir / "bitcoin.conf",
        BITCOIND_CONF.format(rpc_auth=rpc_auth_line, dbcache=1000),
    )

    with fscm.cd(datadir):
        tagname = f"bmon/bitcoin:{git_sha}"
        run(f"docker build --build-arg VERSION=git:{git_sha} --tag {tagname} .", check=True)

    uid = stdout("id -u", check=True)

    flags = f"-p 8333:8333 -p 8332:8332 --user {uid} --restart unless-stopped "
    flags += f'-v {datadir}:/bitcoin-datadir '

    container_name = 'bmon_bitcoin'
    if not docker.container_exists(container_name):
        run(
            f"docker create --name={container_name} {flags} {tagname} "
            "bitcoind -datadir=/bitcoin-datadir"
        )

    if not docker.is_container_up(container_name):
        run(f"docker start {container_name}")

def setup_bitcoind_exporter(
    data_path: str,
    rpc_user: str,
    rpc_password: str,
    port: int = 9332,
):
    (datap := Path(data_path) / "bitcoind-exporter").mkdir(parents=True, exist_ok=True)
    container_name = "bmon_bitcoind_exporter"
    image_name = "jamesob/bitcoin-prometheus-exporter"

    if not (gitpath := datap / "src").exists():
        run(f"git clone https://github.com/{image_name} {gitpath}")

    if not docker.image_exists(image_name):
        with fscm.cd(gitpath):
            run(f"docker build --tag {image_name} .")

    uid = stdout("id -u", check=True)
    if running_podman():
        uid = "root"  # hack
    flags = (
        f"-p {port}:{port} --user={uid} --restart=unless-stopped --network=host "
        f"-e BITCOIN_RPC_HOST=localhost "
        f"-e BITCOIN_RPC_USER={rpc_user} "
        f"-e BITCOIN_RPC_PASSWORD={rpc_password} "
    )

    if not docker.container_exists(container_name):
        run(f"docker create --name={container_name} {flags} {image_name}")

    if not docker.is_container_up(container_name):
        run(f"docker start {container_name}")


def setup_prom_exporter():
    container_name = "bmon_prom_exporter"

    flags = "--net='host' --pid='host' -v '/:/host:ro,rslave' --restart unless-stopped "

    if not docker.container_exists(container_name):
        run(
            f"docker create --name={container_name} {flags} "
            "quay.io/prometheus/node-exporter --path.rootfs=/host"
        )

    if not docker.is_container_up(container_name):
        run(f"docker start {container_name}")


@dataclass
class MonitoredBitcoind:

    host: Host
    loki_address: str
    version: str
    rpc_user: str
    rpc_password: str
    install_sys_monitor: bool = False

    def provision(self):
        with mitogen_context(self.host.hostname, username=self.host.username) as (
            _,
            context,
        ):
            with fscm.cd(GIT_REPO):
                git_sha = run(
                    f"git rev-parse {self.version}", check=True
                ).stdout.strip()

                auth_return = run(
                    f"./share/rpcauth/rpcauth.py {self.rpc_user} {self.rpc_password}",
                    check=True,
                )
                [rpc_auth_line] = [
                    i.strip()
                    for i in auth_return.stdout.splitlines()
                    if i.startswith("rpcauth=")
                ]

            context.call(
                setup_bitcoind,
                self.host.data_path,
                git_sha,
                rpc_auth_line,
                fscm.template("../bootstrap-bitcoin/bootstrap.py"),
                fscm.template("../bootstrap-bitcoin/Dockerfile"),
            )
            context.call(
                setup_promtail,
                self.host.data_path,
                self.loki_address,
                self.version,
                git_sha,
                str(Path(self.host.data_path) / "bitcoin" / "debug.log"),
            )
            context.call(
                setup_bitcoind_exporter,
                self.host.data_path,
                self.rpc_user,
                self.rpc_password,
            )

            # Make this conditional because we may not want to make / or /proc
            # accessible to some random prometheus binary (in order to do this
            # kind of monitoring) unless the box is dedicated to running bitcoind.
            if self.install_sys_monitor:
                context.call(setup_prom_exporter)
