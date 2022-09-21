#!/usr/bin/env python3
# vim: set sw=4 tabstop=4
import os
import sys
import re
import typing as t
from pathlib import Path

import fscm
import fscm.remote
from clii import App
from fscm.remote import executor, SSH
from fscm import run, p, getstdout, lineinfile, systemd

from .config import prod_env


cli = App()


BMON_DIR = Path("/bmon")
BMON_LOGS = BMON_DIR / "logs"
BMON_DATA = BMON_DIR / "data"
BMON_PROGRAMS = BMON_DIR / "programs"

LOKI_PORT = 3100
REPO_URL = "https://github.com/jamesob/bmon.git"


class Host(fscm.remote.Host):
    def __init__(
        self,
        *args,
        is_server: bool = False,
        bitcoin_version: str | None = None,
        prom_exporter_port: int | None = 9102,
        **kwargs,
    ):
        self.is_server = is_server
        self.bitcoin_version = bitcoin_version
        self.prom_exporter_port = prom_exporter_port
        super().__init__(*args, **kwargs)


HOSTS = [
    SERVER_HOST := Host("bmon.lan", is_server=True),
    Host("tp-i5-16g-1.lan", bitcoin_version='v23.0')
]

BITCOIN_HOSTS = [
    h for h in HOSTS if h.bitcoin_version is not None
]


def _initialize_hosts():
    """Set sudo passwords so that they're cached for remote execution."""
    secrets = fscm.get_secrets(['bmon'], 'fscm/secrets').bmon
    assert secrets.sudo_password

    for host in HOSTS:
        host.secrets = secrets
        host.connection_spec = [SSH(check_host_keys='accept')]


fscm.remote.OPTIONS.pickle_whitelist = [r"bmon_infra\..*"]


def _run_in_bash(cmd, *args, **kwargs) -> fscm.RunReturn:
    return run(f"bash -i -c '{cmd}'", *args, **kwargs)


def _setup_bmon_common(user: str):
    fscm.s.pkgs_install("git supervisor docker.io curl")
    fscm.s.group_member(user, "docker")

    if run(f"loginctl show-user {user} | grep 'Linger=no'", quiet=True).ok:
        run(f"loginctl enable-linger {user}", sudo=True)

    if not (venv := Path.home() / '.venv').exists():
        run(f"python3 -m venv {venv}").assert_ok()

    lineinfile(
        f"/home/{user}/.bashrc",
        "source ${HOME}/.venv/bin/activate",
        regex="venv/bin/activate",
    )

    if not (bmon_path := Path.home() / "bmon").exists():
        run(f"git clone {REPO_URL} {bmon_path}").assert_ok()

    if not _run_in_bash("which bmon-config", quiet=True).ok:
        _run_in_bash(f"cd {bmon_path} && pip install -e ./infra").assert_ok()

    if not _run_in_bash("which docker-compose", quiet=True).ok:
        _run_in_bash("pip install docker-compose").assert_ok()

    run(f"cd {bmon_path} && git pull origin master").assert_ok()

    if p('/etc/docker/daemon.json', sudo=True).contents(
            '{ "log-driver": "journald" }').changes:
        run('systemctl restart docker', sudo=True).assert_ok()


def provision_bmon_server(host, parent):
    assert (username := getstdout("whoami")) != "root"

    _setup_bmon_common(username)
    os.chdir(bmon_path := Path.home() / "bmon")

    settings = dict(
        db_password=host.secrets.db_password,
        bitcoin_rpc_password=host.secrets.bitcoin_rpc_password,
        bitcoin_version=host.bitcoin_version,
    )

    p(bmon_path / ".env").contents(prod_env(is_server=True, **settings)).chmod("600")
    _run_in_bash("bmon-config -t prod")
    docker_compose = Path.home() / '.venv' / 'bin' / 'docker-compose'
    assert docker_compose.exists()

    _run_in_bash(f"{docker_compose} --profile server --profile prod build")

    p(sysd := Path.home() / '.config' / 'systemd' / 'user').mkdir()

    if p(sysd / 'bmon-server.service').contents(
        parent.template(
            './etc/systemd-server-unit.service',
            user=username,
            bmon_dir=bmon_path,
            docker_compose_path=docker_compose,
        )
    ).changes:
        run('systemctl --user daemon-reload')

    systemd.enable_service('bmon-server')

    fscm.s.pkgs_install('nginx')
    p('/etc/nginx/sites-enabled/default', sudo=True).rm()
    if p('/etc/nginx/sites-enabled/bmon.conf', sudo=True).contents(
            parent.get_file('./etc/server-nginx.conf')).changes:
        run('systemctl restart nginx', sudo=True)

    run("systemctl --user restart bmon-server").assert_ok()


def provision_monitored_bitcoind(host, parent):
    assert (username := getstdout("whoami")) != "root"

    _setup_bmon_common(username)
    os.chdir(bmon_path := Path.home() / "bmon")

    settings = dict(
        db_password=host.secrets.db_password,
        bitcoin_rpc_password=host.secrets.bitcoin_rpc_password,
        bitcoin_version=host.bitcoin_version,
    )

    p(bmon_path / ".env").contents(prod_env(is_server=False, **settings)).chmod("600")
    _run_in_bash("bmon-config -t prod")
    docker_compose = Path.home() / '.venv' / 'bin' / 'docker-compose'
    assert docker_compose.exists()

    _run_in_bash(f"{docker_compose} --profile bitcoind --profile prod build")

    p(sysd := Path.home() / '.config' / 'systemd' / 'user').mkdir()

    if p(sysd / 'bmon-bitcoind.service').contents(
        parent.template(
            './etc/systemd-bitcoind-unit.service',
            user=username,
            bmon_dir=bmon_path,
            docker_compose_path=docker_compose,
        )
    ).changes:
        run('systemctl --user daemon-reload')

    systemd.enable_service('bmon-bitcoind')
    run("systemctl --user restart bmon-server").assert_ok()


@cli.cmd
@cli.arg('type', '-t', help='Options: server, bitcoin')
def provision(type: str = ''):
    """Provision necessary dependencies and config files on hosts."""
    _initialize_hosts()

    if not type or type == 'server':
        with executor(SERVER_HOST) as exec:
            exec.allow_file_access('./etc/*', './etc/**/*')
            exec.run(provision_bmon_server)

    if not type or type.startswith('bitcoin'):
        with executor(*BITCOIN_HOSTS) as exec:
            exec.allow_file_access('./etc/*', './etc/**/*')
            exec.run(provision_monitored_bitcoind)


@cli.cmd
def status():
    """Check status on hosts."""
    run_on_all('supervisorctl status')


@cli.cmd
def bitcoind_logs():
    """Present a brief tail of all known bitcoind logs."""
    run_on_all('tail -n 20 /bmon/logs/bitcoind-stdout.log', host_filter=r'bmon-b\d+')


@cli.cmd
def run_on_all(cmd: str, host_filter: t.Optional[str] = None):
    """Run some command across all hosts."""
    _initialize_hosts()

    hosts = HOSTS
    if host_filter:
        hosts = [h for h in HOSTS if re.match(host_filter, h.name)]

    with executor(*hosts) as exec:
        if not (res := exec.run(_run_cmd, cmd)).ok:
            print(f"Command failed on hosts: {res.failed}")
            sys.exit(1)


def _run_cmd(cmd: str):
    return fscm.run(cmd)


def main():
    cli.run()


if __name__ == "__main__":
    # You should be using the bmon-deploy entrypoint though.
    main()
