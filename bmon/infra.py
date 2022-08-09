#!/usr/bin/env python3
# vim: set sw=4 tabstop=4
import socket
import enum
import typing as t
import textwrap
from dataclasses import dataclass
from pathlib import Path
from string import Template

import fscm
from fscm import run, mitogen_context, file_, docker, p


BMON_DIR = Path("/bmon")
BMON_LOGS = BMON_DIR / "logs"
BMON_DATA = BMON_DIR / "data"
BMON_PROGRAMS = BMON_DIR / "programs"

LOKI_PORT = 3100


@dataclass
class Host:
    hostname: str
    username: str
    ssh_port: int = 22


COMMON_SUPERVISOR_CONF = """
; supervisor config file

[unix_http_server]
file=/var/run/supervisor.sock
chmod=0700

[inet_http_server]
port=0.0.0.0:9001
username=bmon
password=foobar

[supervisord]
logfile=/var/log/supervisor/supervisord.log
pidfile=/var/run/supervisord.pid
childlogdir=/var/log/supervisor

; the below section must remain in the config file for RPC
; (supervisorctl/web interface) to work, additional interfaces may be
; added by defining them in separate rpcinterface: sections

[rpcinterface:supervisor]
supervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface

[supervisorctl]
serverurl=unix:///var/run/supervisor.sock ; use a unix:// URL  for a unix socket

; The [include] section can just contain the "files" setting.  This
; setting can list multiple files (separated by whitespace or
; newlines).  It can also contain wildcards.  The filenames are
; interpreted as relative to this file.  Included files *cannot*
; include files themselves.

[include]
files = /etc/supervisor/conf.d/*.conf

"""


def supervisor_program_config(program_name: str, running_user: str) -> str:
    """
    Return supervisor configration for non-bitcoind programs.

    Config for bitcoind is spelled out specifically below.
    """
    return textwrap.dedent(
        f"""
        [program:{program_name}]

        command = /usr/local/bin/bmon-{program_name}
        user = {running_user}
        process_name = {program_name}
        autostart = true
        startsecs = 2
        stopwaitsecs = 100

        stdout_logfile = /bmon/logs/{program_name}-stdout.log
        stdout_logfile_maxbytes = 10MB
        stdout_logfile_backups = 1

        stderr_logfile = /bmon/logs/{program_name}-stderr.log
        stderr_logfile_maxbytes = 10MB
        stderr_logfile_backups = 1

        """
    )


@dataclass
class BMonInstallation:
    grafana_host: Host
    loki_host: Host
    bitcoin_hostnames: t.List[str]

    def provision(self, mitogen_context):
        mitogen_context.call(
            setup_bmon_grafana_host, self.grafana_host.username, self.bitcoin_hostnames
        )

        mitogen_context.call(setup_bmon_loki_host, self.grafana_host.username)
        mitogen_context.call(reload_supervisor)


def reload_supervisor():
    fscm.run('supervisorctl reload', sudo=True)


def _setup_bmon_common(user: str):
    fscm.s.pkgs_install("git supervisor docker.io curl")
    fscm.s.group_member(user, "docker")

    for dir in (BMON_LOGS, BMON_DATA, BMON_PROGRAMS):
        p(dir).mkdir().chown(f"{user}:{user}")


def setup_bmon_grafana_host(user: str, bitcoin_hostnames):
    _setup_bmon_common(user)
    setup_grafana()
    setup_alertmanager()
    setup_prometheus(bitcoin_hostnames)
    setup_prom_exporter()

    p("/etc/supervisor/conf.d/grafana.conf", sudo=True).contents(
        supervisor_program_config("grafana", user)
    )

    p("/etc/supervisor/conf.d/alertmanager.conf", sudo=True).contents(
        supervisor_program_config("alertmanager", user)
    )

    p("/etc/supervisor/conf.d/prometheus.conf", sudo=True).contents(
        supervisor_program_config("prometheus", user)
    )

    p("/etc/supervisor/conf.d/prom_exporter.conf", sudo=True).contents(
        supervisor_program_config("prom-exporter", user)
    )

    # Kind of a hack; make sure everything is owned by the user.
    fscm.run(f"chown -R {user}:{user} /bmon")

    # TODO: this is done at Loki end, and they're the same machine for now
    # fscm.run('systemctl restart supervisor', sudo=True)
    # fscm.run('supervisorctl restart all', sudo=True)


def setup_bmon_loki_host(user: str):
    _setup_bmon_common(user)
    setup_loki()

    p("/etc/supervisor/conf.d/loki.conf", sudo=True).contents(
        supervisor_program_config("loki", user)
    )

    # fscm.run('systemctl restart supervisor', sudo=True)

    # Kind of a hack; make sure everything is owned by the user.
    fscm.run(f"chown -R {user}:{user} /bmon")


def _mk_docker_run_executable(cmd_name: str, docker_run_args: str) -> fscm.PathHelper:
    """
    Each system component has a related executable named /usr/local/bin/bmon-* which
    logs to stdout and is suitable for management by supervisord.

    For programs run via docker, standardize this script installation here.
    """
    docker_run_args = textwrap.dedent(docker_run_args).replace("\n", " ")
    run_cmd = textwrap.dedent(
        f"""
        #!/bin/bash
        exec docker run --name=bmon_{cmd_name} --rm {docker_run_args}
        """
    ).lstrip()

    binname = cmd_name.replace("_", "-")
    return p(f"/usr/local/bin/bmon-{binname}", sudo=True).contents(run_cmd).chmod("755")


def setup_grafana(port: int = 3000):
    p(datap := BMON_DATA / "grafana").mkdir()
    p(etc := datap / "etc").mkdir()
    p(var := datap / "var").mkdir()

    if not (config := etc / "grafana.ini").exists():
        gh_url = (
            "https://raw.githubusercontent.com/grafana/grafana/main/conf/sample.ini"
        )
        run(f'curl -L "{gh_url}" > {config}')

    _mk_docker_run_executable(
        "grafana",
        (
            f"""
        -p {port}:{port} --user=$(id -u) --network=host
        -v {etc}:/etc/grafana
        -v {var}:/var/lib/grafana
        docker.io/grafana/grafana-enterprise
        """
        ),
    )

    p(ds := etc / "provisioning" / "datasources").mkdir()
    p(ds / "datasource.yml").contents(GRAFANA_DATA_SOURCES_YAML)


GRAFANA_DATA_SOURCES_YAML = f"""
apiVersion: 1

datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://localhost:9090

  - name: Loki
    type: loki
    access: proxy
    url: http://localhost:{LOKI_PORT}

  - name: Alertmanager
    type: alertmanager
    url: http://localhost:9093
    access: proxy

"""


def setup_loki(
    port: int = LOKI_PORT,
    alertmanager_host: str = "localhost",
    alertmanager_port: str = "9093",
):
    p(datap := BMON_DATA / "loki").mkdir()
    p(etc := datap / "etc").mkdir()

    file_(
        etc / "local-config.yaml",
        Template(LOKI_CONFIG).substitute(
            ALERTMANAGER_HOST=alertmanager_host,
            ALERTMANAGER_PORT=alertmanager_port,
            LOKI_PORT=LOKI_PORT,
        ),
    )
    _mk_docker_run_executable(
        "loki",
        (
            f"""
        -p {port}:{port} --user=$(id -u) --network=host
        -v {datap}:/loki
        -v {etc}:/etc/loki
        docker.io/grafana/loki -config.file=/etc/loki/local-config.yaml
        """
        ),
    )


LOKI_CONFIG = """
auth_enabled: false

server:
  http_listen_address: 0.0.0.0
  http_listen_port: ${LOKI_PORT}
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
    p(datap := BMON_DATA / "prometheus").mkdir()
    p(data_dir := datap / "data").mkdir()
    p(etc := datap / "etc").mkdir()

    prom_conf = Template(PROMETHEUS_CONFIG).substitute()
    this_hostname = fscm.run("hostname -f", quiet=True).stdout.strip()

    PROM_EXPORTER_PORT = 9100
    BITCOIND_EXPORTER_PORT = 9332

    # This host (prometheus host), should be monitored.
    prom_conf += (
        f"  - job_name: {this_hostname}\n"
        f"    static_configs: \n"
        f"      - targets: ['{this_hostname}:{PROM_EXPORTER_PORT}']\n\n"
    )

    for hostname in bitcoin_hostnames:
        prom_conf += (
            f"  - job_name: {hostname}\n"
            f"    static_configs: \n"
            f"      - targets: ['{hostname}:{PROM_EXPORTER_PORT}', '{hostname}:{BITCOIND_EXPORTER_PORT}']\n\n"
        )

    p(etc / "prometheus.yml").contents(prom_conf)

    _mk_docker_run_executable(
        "prometheus",
        (
            f"""
        -p {port}:{port} --user=$(id -u)
        -v {etc}:/etc/prometheus
        -v {data_dir}:/prometheus
         docker.io/prom/prometheus
        """
        ),
    )


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
    p(datap := BMON_DATA / "alertmanager").mkdir()
    flags = f"-p {port}:{port} --user $(id -u) "
    args = ""

    if config_template:
        # TODO actually populate config
        flags += f'-v {datap / "config.yaml"}:/etc/alertmanager/config.yaml '
        args = "--config.file=/etc/alertmanager/config.yaml"

    _mk_docker_run_executable(
        "alertmanager",
        (
            f"""
        {flags}
        docker.io/prom/alertmanager {args}
        """
        ),
    )


class BitcoinNet(str, enum.Enum):
    mainnet = "mainnet"
    regtest = "regtest"


@dataclass
class MonitoredBitcoind:
    """
    A host that runs bitcoind and reports data to the bmon server.
    """

    host: Host
    loki_address: str
    version: str
    rpc_user: str
    rpc_password: str
    net: BitcoinNet = BitcoinNet.mainnet
    install_sys_monitor: bool = False

    def provision(self, mitogen_context):
        mitogen_context.call(setup_bitcoin_host, self, self.host.username)
        mitogen_context.call(reload_supervisor)


BITCOIND_SUPERVISOR_CONF = """
[program:bitcoind]

command = /usr/local/bin/bmon-bitcoind
user = ${USER}
process_name = bitcoind
autostart = true
startsecs = 2
stopwaitsecs = 10

stdout_logfile = /bmon/logs/bitcoind-stdout.log
stdout_logfile_maxbytes = 200MB
stdout_logfile_backups = 5

stderr_logfile = /bmon/logs/bitcoind-stderr.log
stderr_logfile_maxbytes = 200MB
stderr_logfile_backups = 5

"""


def setup_bitcoin_host(conf: MonitoredBitcoind, user: str):
    _setup_bmon_common(user)

    setup_bitcoind(
        "", get_bitcoind_auth_line(conf.rpc_user, conf.rpc_password), conf.net
    )

    bitcoin_logs_path = str(BMON_DATA / "bitcoin" / "debug.log")
    if conf.net == BitcoinNet.regtest:
        bitcoin_logs_path = str(BMON_DATA / "bitcoin" / "regtest" / "debug.log")

    setup_promtail(
        conf.loki_address,
        conf.version,
        "",
        bitcoin_logs_path,
    )
    bitcoin_rpc_port = 8332
    if conf.net == BitcoinNet.regtest:
        bitcoin_rpc_port = 18443

    setup_bitcoind_exporter(conf.rpc_user, conf.rpc_password, bitcoin_rpc_port)
    setup_prom_exporter()

    p("/etc/supervisor/conf.d/bitcoin.conf", sudo=True).contents(
        Template(BITCOIND_SUPERVISOR_CONF).substitute(USER=user)
    )

    p("/etc/supervisor/conf.d/promtail.conf", sudo=True).contents(
        supervisor_program_config("promtail", user)
    )

    p("/etc/supervisor/conf.d/bitcoind_exporter.conf", sudo=True).contents(
        supervisor_program_config("bitcoind-exporter", user)
    )

    p("/etc/supervisor/conf.d/prom_exporter.conf", sudo=True).contents(
        supervisor_program_config("prom-exporter", user)
    )

    # Kind of a hack; make sure everything is owned by the user.
    fscm.run(f"chown -R {user}:{user} /bmon")

    # fscm.run('systemctl restart supervisor', sudo=True)
    # fscm.run('supervisorctl restart all', sudo=True)


def setup_promtail(
    loki_address: str,
    bitcoin_version: str,
    bitcoin_git_sha: str,
    bitcoin_logs_path: str,
    port: int = 9080,
):
    p(datap := BMON_DATA / "promtail").mkdir()
    p(datap / "config.yaml").contents(
        Template(PROMTAIL_CONF).substitute(
            LOKI_ADDRESS=loki_address,
            BITCOIN_GIT_SHA=bitcoin_git_sha,
            BITCOIN_VERSION=bitcoin_version,
            HOSTNAME=socket.gethostname(),
            PORT=port,
        ),
    )
    uid = "$(id -u)"
    if running_podman():
        uid = "root"  # hack

    _mk_docker_run_executable(
        "promtail",
        (
            f"""
        -p {port}:{port} --user {uid}
        -v {bitcoin_logs_path}:/bitcoin-debug.log
        -v {datap}:/etc/promtail
        docker.io/grafana/promtail --config.file=/etc/promtail/config.yaml
        """
        ),
    )


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


def _download_bitcoin(binary_dest_dir: Path):
    """
    TODO make this very primitive download method work across versions and
    generally better.
    """
    if (bitcoind_bin := binary_dest_dir / "bitcoind").exists() and "v23.0.0" in run(
        f"{bitcoind_bin} -version | head -n 1", quiet=True
    ).stdout:
        return

    tar_path = fscm.download_and_check_sha(
        "https://bitcoincore.org/bin/bitcoin-core-23.0/bitcoin-23.0-x86_64-linux-gnu.tar.gz",
        "2cca490c1f2842884a3c5b0606f179f9f937177da4eadd628e3f7fd7e25d26d0",
    )
    untar_dir_name = "bitcoin-23.0"

    fscm.run(
        f"cd {tar_path.parent} && tar xvf {tar_path} && cd {untar_dir_name} && "
        f"mv bin/bitcoind bin/bitcoin-cli {binary_dest_dir}"
    )
    fscm.run(f"rm -rf {tar_path} {tar_path.parent}/{untar_dir_name}")


def setup_bitcoind(
    git_sha: str,
    rpc_auth_line: str,
    network: BitcoinNet,
):
    p(datadir := BMON_DATA / "bitcoin").mkdir()
    p(datadir / "bitcoin.conf").contents(
        BITCOIND_CONF.format(rpc_auth=rpc_auth_line, dbcache=1000)
    )
    fscm.mkdir(bindir := BMON_PROGRAMS / "bitcoin")

    _download_bitcoin(bindir)

    netarg = "-regtest" if network == BitcoinNet.regtest else ""
    run_cmd = textwrap.dedent(
        f"""
        #!/bin/bash
        exec {bindir}/bitcoind {netarg} -datadir={datadir} -printtoconsole=1
        """
    ).lstrip()

    p("/usr/local/bin/bmon-bitcoind", sudo=True).contents(run_cmd).chmod("755")


def setup_bitcoind_exporter(
    rpc_user: str,
    rpc_password: str,
    bitcoin_rpc_port: int,
    port: int = 9332,
):
    """
    TODO: include bitcoin version information in prom export
    """
    p(datap := BMON_DATA / "bitcoind-exporter").mkdir()
    image_name = "jamesob/bitcoin-prometheus-exporter"

    if not (gitpath := datap / "src").exists():
        run(f"git clone https://github.com/{image_name} {gitpath}")

    if not docker.image_exists(image_name):
        with fscm.cd(gitpath):
            run(f"docker build --tag {image_name} .")

    uid = "$(id -u)"
    if running_podman():
        uid = "root"  # hack

    _mk_docker_run_executable(
        "bitcoind_exporter",
        (
            f"""
        -p {port}:{port} --user={uid} --network=host
        -e BITCOIN_RPC_HOST=localhost
        -e BITCOIN_RPC_PORT={bitcoin_rpc_port}
        -e BITCOIN_RPC_USER={rpc_user}
        -e BITCOIN_RPC_PASSWORD={rpc_password}
        {image_name}
        """
        ),
    )


def setup_prom_exporter():
    _mk_docker_run_executable(
        "prom_exporter",
        (
            """
        --net='host' --pid='host' -v '/:/host:ro,rslave'
        quay.io/prometheus/node-exporter --path.rootfs=/host
        """
        ),
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
