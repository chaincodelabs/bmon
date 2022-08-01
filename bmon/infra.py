#!/usr/bin/env python3
import socket
import typing as t
from textwrap import dedent
from dataclasses import dataclass
from pathlib import Path
from string import Template

import fscm
from fscm import run, mitogen_context, file_, docker, p


def stdout(*args, **kwargs) -> str:
    kwargs.setdefault("quiet", True)
    kwargs.setdefault("check", True)
    return run(*args, **kwargs).stdout.strip()


# Used for non-destructive querying of the repo.
GIT_REPO = Path.home() / "src" / "bitcoin"

BMON_DIR = Path('/bmon')
BMON_LOGS = BMON_DIR / 'logs'
BMON_DATA = BMON_DIR / 'data'
BMON_PROGRAMS = BMON_DIR / 'programs'


@dataclass
class Host:
    hostname: str
    username: str


@dataclass
class BMonInstallation:
    grafana_host: Host
    loki_host: Host
    bitcoin_hostnames: t.List[str]

    def provision(self):
        with mitogen_context(
            self.grafana_host.hostname, username=self.grafana_host.username
        ) as (_, context):
            context.call(setup_grafana)
            context.call(setup_alertmanager)
            context.call(setup_prometheus, self.bitcoin_hostnames)

        with mitogen_context(
            self.loki_host.hostname, username=self.loki_host.username
        ) as (_, context):
            context.call(setup_loki)


def _mk_docker_run_executable(cmd_name: str, docker_run_args: str) -> fscm.PathHelper:
    """
    Each system component has a related executable named /usr/local/bin/bmon-* which
    logs to stdout and is suitable for management by supervisord.

    For programs run via docker, standardize this script installation here.
    """
    run_cmd = dedent(
        f"""
        #!/bin/bash
        exec docker run --name=bmon_{cmd_name} \\
            {docker_run_args}
        """
    )

    binname = cmd_name.replace('_', '-')
    return p(f"/usr/local/bin/bmon-{binname}").contents(run_cmd).chmod('+x')



def setup_grafana(port: int = 3000):
    (datap := BMON_DATA / "grafana").mkdir(parents=True, exist_ok=True)
    (etc := datap / "etc").mkdir(exist_ok=True)
    (var := datap / "var").mkdir(exist_ok=True)

    if not (config := etc / "grafana.ini").exists():
        gh_url = (
            "https://raw.githubusercontent.com/grafana/grafana/main/conf/sample.ini"
        )
        run(f'curl -L "{gh_url}" > {config}')

    _mk_docker_run_executable('grafana', dedent(
        f'''
        -p {port}:{port} --user=$(id -u) --network=host \\
        -v {etc}:/etc/grafana \\
        -v {var}:/var/lib/grafana \\
        docker.io/grafana/grafana-enterprise"
        '''))


def setup_loki(
    port: int = 3100,
    alertmanager_host: str = "localhost",
    alertmanager_port: str = "9093",
):
    (datap := BMON_DATA / "loki").mkdir(parents=True, exist_ok=True)
    (etc := datap / "etc").mkdir(exist_ok=True)

    file_(
        etc / "local-config.yaml",
        Template(LOKI_CONFIG).substitute(
            ALERTMANAGER_HOST=alertmanager_host, ALERTMANAGER_PORT=alertmanager_port
        ),
    )
    _mk_docker_run_executable('loki', dedent(
        f'''
        -p {port}:{port} --user=$(id -u) --network=host \\
        -v {datap}:/loki \\
        -v {etc}:/etc/loki \\
        docker.io/grafana/loki -config.file=/etc/loki/local-config.yaml
        '''))


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
    bitcoin_hostnames: t.List[str],
    port: int = 9090,
):
    (datap := BMON_DATA / "prometheus").mkdir(parents=True, exist_ok=True)
    (etc := datap / "etc").mkdir(exist_ok=True)

    prom_conf = Template(PROMETHEUS_CONFIG).substitute()

    PROM_EXPORTER_PORT = 9100
    BITCOIND_EXPORTER_PORT = 9332

    for hostname in bitcoin_hostnames:
        prom_conf += (
            f"  - job_name: {hostname}\n"
            f"    static_configs: \n"
            f"      - targets: ['{hostname}:{PROM_EXPORTER_PORT}', '{hostname}:{BITCOIND_EXPORTER_PORT}']\n\n"
        )

    p(etc / "prometheus.yml").contents(prom_conf)

    _mk_docker_run_executable('prometheus', dedent(
        f'''
        -p {port}:{port} \\
        -v {etc}:/etc/prometheus \\
         docker.io/prom/prometheus
        '''))


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
    config_template: str | None = None,
    port: int = 9093,
):
    (datap := BMON_DATA / "alertmanager").mkdir(parents=True, exist_ok=True)
    flags = f"-p {port}:{port} --user $(id -u) --restart unless-stopped "
    args = ''

    if config_template:
        # TODO actually populate config
        flags += f'-v {datap / "config.yaml"}:/etc/alertmanager/config.yaml '
        args = '--config.file=/etc/alertmanager/config.yaml'


    _mk_docker_run_executable('alertmanager', dedent(
        f'''
        {flags} \\
        docker.io/prom/alertmanager {args}
        '''))


def setup_promtail(
    loki_address: str,
    bitcoin_version: str,
    bitcoin_git_sha: str,
    bitcoin_logs_path: str,
    port: int = 9080,
):
    (datap := BMON_DATA / "promtail").mkdir(parents=True, exist_ok=True)

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
    uid = '$(id -u)'
    if running_podman():
        uid = "root"  # hack

    _mk_docker_run_executable('promtail', dedent(
        f'''
        -p {port}:{port} --user {uid} \\
        -v {bitcoin_logs_path}:/bitcoin-debug.log \\
        -v {datap}:/etc/promtail \\
        docker.io/grafana/promtail --config.file=/etc/promtail/config.yaml
        '''))


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
printtoconsole=1
"""

BITCOIND_SUPERVISOR_CONF = """
[program:bitcoind]

command = /usr/local/bin/bmon-bitcoind
process_name = bitcoind
autostart = true
startsecs = 10
stopwaitsecs = 600

stdout_logfile = /bmon/logs/bitcoind-stdout.log
stdout_logfile_maxbytes = 200MB
stdout_logfile_backups = 5

stderr_logfile = /bmon/logs/bitcoind-stderr.log
stderr_logfile_maxbytes = 100MB
stderr_logfile_backups = 5

"""

def _download_bitcoin(binary_dest_dir: Path):
    # TODO make this very primitive download method work across versions and
    # generally better.
    tar_path = fscm.download_and_check_sha(
        'https://bitcoincore.org/bin/bitcoin-core-23.0/bitcoin-23.0-x86_64-linux-gnu.tar.gz',
        '2cca490c1f2842884a3c5b0606f179f9f937177da4eadd628e3f7fd7e25d26d0')
    untar_dir_name = 'bitcoin-23.0'

    fscm.run(
        f'cd {tar_path.parent} && tar xvf {tar_path} && cd {untar_dir_name} && '
        f'mv bin/{{bitcoind,bitcoin-cli}} {binary_dest_dir}')
    fscm.run(f'rm -rf {tar_path} {tar_path.parent}/{untar_dir_name}')


def setup_bitcoind(
    git_sha: str, rpc_auth_line: str
):
    fscm.mkdir(datadir := BMON_DATA / "bitcoin")
    p(datadir / "bitcoin.conf").contents(
        BITCOIND_CONF.format(rpc_auth=rpc_auth_line, dbcache=1000))
    fscm.mkdir(bindir := BMON_PROGRAMS / 'bitcoin')

    _download_bitcoin(bindir)

    run_cmd = dedent(
        f"""
        #!/bin/bash
        exec {bindir}/bitcoind -datadir={datadir} -printtoconsole=1
        """
    )

    return p(f"/usr/local/bin/bmon-bitcoind").contents(run_cmd).chmod('+x')


def setup_bitcoind_exporter(
    rpc_user: str,
    rpc_password: str,
    port: int = 9332,
):
    (datap := BMON_DATA / "bitcoind-exporter").mkdir(parents=True, exist_ok=True)
    image_name = "jamesob/bitcoin-prometheus-exporter"

    if not (gitpath := datap / "src").exists():
        run(f"git clone https://github.com/{image_name} {gitpath}")

    if not docker.image_exists(image_name):
        with fscm.cd(gitpath):
            run(f"docker build --tag {image_name} .")

    uid = '$(id -u)'
    if running_podman():
        uid = "root"  # hack

    _mk_docker_run_executable('bitcoind_exporter', dedent(
        f'''
        -p {port}:{port} --user={uid} --network=host \\
        -e BITCOIN_RPC_HOST=localhost \\
        -e BITCOIN_RPC_USER={rpc_user} \\
        -e BITCOIN_RPC_PASSWORD={rpc_password} \\
        {image_name}
        '''))

def setup_prom_exporter():
    _mk_docker_run_executable('prom_exporter', dedent(
        f'''
        --net='host' --pid='host' -v '/:/host:ro,rslave' \\
        quay.io/prometheus/node-exporter --path.rootfs=/host
        '''))


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
