#!/usr/bin/env python3
import sys
import re
import typing as t

from clii import App
import fscm
import fscm.remote
from fscm.remote import executor, Host


from bmon.infra import provision_bmon_server, MonitoredBitcoind, LOKI_PORT
from bmon.infra import BitcoinNet, provision_monitored_bitcoind

cli = App()

common_host_kwargs = dict(
    username='vagrant',
    connection_spec=(fscm.SSH(username='vagrant', check_host_keys='ignore'),),
)

server_host = Host("bmon-server", **common_host_kwargs)

common_bitcoind_kwargs = dict(
    loki_address=f'{server_host.hostname}:{LOKI_PORT}',
    version="v23.0",
    rpc_user="foo",
    rpc_password="bar",
    net=BitcoinNet.regtest,
    **common_host_kwargs,
)

b1 = MonitoredBitcoind(
    "bmon-b1",
    **common_bitcoind_kwargs
)
b2 = MonitoredBitcoind(
    "bmon-b2",
    **common_bitcoind_kwargs
)

bitcoin_hosts = (
    b1,
    b2,
)
all_hosts = (server_host, *bitcoin_hosts)


@cli.cmd
def provision():
    """Provision necessary dependencies and config files on hosts."""
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
