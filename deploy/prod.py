#!/usr/bin/env python3
import os
import getpass
import re
import sys
import typing as t

from pathlib import Path
from clii import App

import fscm
from fscm.remote import Host, executor
from bmon.infra import MonitoredBitcoind, provision_bmon_server
from bmon.infra import provision_monitored_bitcoind, LOKI_PORT

cli = App()


def get_su_pass():
    if (from_env := os.environ.get('BMON_SERVER_PASSWORD')):
        return from_env
    elif (Path.home() / '.password-store' / 'fscm' / 'secrets.gpg').exists():
        return fscm.get_secrets(['home.servers'], 'fscm/secrets').home.servers.password
    else:
        return getpass.getpass('password for bmon servers: ')


fscm.remote.OPTIONS.default_connection_spec = (fscm.SSH(),)

server_host = Host("bmon.lan")

common_bitcoind_kwargs = dict(
    loki_address=f'{server_host.hostname}:{LOKI_PORT}',
    version="v23.0",
    rpc_user="foo",
    rpc_password="bar",
)

b1 = MonitoredBitcoind(
    "tp-i5-16g-1.lan",
    **common_bitcoind_kwargs
)

bitcoin_hosts = (
    b1,
)

all_hosts = (server_host, *bitcoin_hosts)


def _set_host_secrets():
    """Set sudo passwords so that they're cached for remote execution."""
    sudo_pass = get_su_pass()
    for host in all_hosts:
        host.secrets.sudo_password = sudo_pass


@cli.cmd
def provision():
    """Provision necessary dependencies and config files on hosts."""
    _set_host_secrets()
    with executor(server_host) as exec:
        exec.run(provision_bmon_server, [h.hostname for h in bitcoin_hosts])

    with executor(*bitcoin_hosts) as exec:
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
    _set_host_secrets()
    hosts = all_hosts
    if host_filter:
        hosts = [h for h in all_hosts if re.match(host_filter, h.hostname)]

    with executor(*hosts) as exec:
        if not (res := exec.run(_run_cmd, cmd)).ok:
            print(f"Command failed on hosts: {res.failed}")
            sys.exit(1)


def _run_cmd(cmd: str):
    return fscm.run(cmd)


if __name__ == "__main__":
    cli.run()
