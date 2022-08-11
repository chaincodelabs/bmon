#!/usr/bin/env python3
import mitogen
import mitogen.core
import fscm
import os
import getpass

from pathlib import Path
from clii import App

from bmon.infra import Host, BMonInstallation, MonitoredBitcoind, LOKI_PORT

cli = App()

server_host = Host("tp-i5-16g-0.lan", "james")
b1 = Host("tp-i5-16g-1.lan", "james")

bitcoin_hosts = [
    b1,
]
bmon = BMonInstallation(
    server_host, server_host,
    bitcoin_hostnames=[h.hostname for h in bitcoin_hosts]
)

def get_host_context(router, host: Host, su_password: str) -> mitogen.core.Context:
    return fscm.get_context_from_spec(router, [
        fscm.SSH(hostname=host.hostname),
        fscm.Su(password=su_password)
    ], pickle_whitelist=[r'bmon\..*'])


def get_su_pass():
    if (from_env := os.environ.get('BMON_SERVER_PASSWORD')):
        return from_env
    elif (Path.home() / '.password-store' / 'fscm' / 'secrets.gpg').exists():
        return fscm.get_secrets(['home.servers'], 'fscm/secrets').home.servers.password
    else:
        return getpass.getpass('password for bmon servers: ')


@cli.cmd
def provision():
    """
    Install configuration files to hosts.
    """
    with fscm.mitogen_router() as router:

        su_pass = get_su_pass()
        server_context = get_host_context(router, server_host, su_pass)
        bmon.provision(server_context)

        for bhost in bitcoin_hosts:
            context = get_host_context(router, bhost, su_pass)
            mon = MonitoredBitcoind(
                bhost,
                loki_address=f"{server_host.hostname}:{LOKI_PORT}",
                version="v23.0",
                rpc_user="foo",
                rpc_password="bar",
            )
            mon.provision(context)


@cli.cmd
def status():
    """
    Check status on hosts.
    """
    su_pass = get_su_pass()

    with fscm.mitogen_router() as router:
        for host in [server_host] + bitcoin_hosts:
            print(host)
            con = get_host_context(router, host, su_pass)
            print(con.call(_get_status))


def _get_status():
    return fscm.run('supervisorctl status').stdout


if __name__ == "__main__":
    cli.run()
