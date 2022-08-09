#!/usr/bin/env python3
import mitogen
import mitogen.utils
import mitogen.core
import fscm

from clii import App

from bmon.infra import Host, BMonInstallation, MonitoredBitcoind, LOKI_PORT, BitcoinNet

cli = App()

server_host = Host("192.168.56.2", "root")
b1 = Host("192.168.56.3", "root")
b2 = Host("192.168.56.4", "root")

bitcoin_hosts = [
    b1,
    b2,
]

bmon = BMonInstallation(
    server_host, server_host, bitcoin_hostnames=[h.hostname for h in bitcoin_hosts]
)


def get_host_context(router, host: Host) -> mitogen.core.Context:
    return fscm.get_context_from_spec(
        router,
        [
            fscm.SSH(
                hostname=host.hostname,
                username=host.username,
                port=host.ssh_port,
                check_host_keys="ignore",
            ),
        ],
    )


@cli.cmd
def provision():
    """
    Install configuration files to hosts.
    """
    with fscm.mitogen_router() as router:
        server_context = get_host_context(router, server_host)
        bmon.provision(server_context)

        for bhost in bitcoin_hosts:
            context = get_host_context(router, bhost)
            mon = MonitoredBitcoind(
                bhost,
                loki_address=f"{server_host.hostname}:{LOKI_PORT}",
                version="v23.0",
                rpc_user="foo",
                rpc_password="bar",
                net=BitcoinNet.regtest,
            )
            mon.provision(context)


@cli.cmd
def status():
    """
    Check status on hosts.
    """
    with fscm.mitogen_router() as router:
        for host in [server_host] + bitcoin_hosts:
            print(host)
            con = get_host_context(router, host)
            print(con.call(_get_status))


def _get_status():
    return fscm.run("supervisorctl status").stdout


if __name__ == "__main__":
    mitogen.utils.log_to_file()
    cli.run()
