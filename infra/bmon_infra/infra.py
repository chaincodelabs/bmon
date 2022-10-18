#!/usr/bin/env python3
# vim: set sw=4 tabstop=4
import os
import sys
import getpass
import re
import typing as t
from pathlib import Path
from textwrap import dedent

import yaml
import fscm
import fscm.remote
from clii import App
from fscm.remote import executor
from fscm.contrib import wireguard
from fscm import run, p, lineinfile, systemd

from .config import prod_env


cli = App()
cli.add_argument("-t", "--tag-filter")
cli.add_argument("-f", "--hostname-filter")

REPO_URL = "https://github.com/chaincodelabs/bmon.git"
VENV_PATH = Path.home() / ".venv"

fscm.remote.OPTIONS.pickle_whitelist = [r"bmon_infra\..*"]


class Host(wireguard.Host):
    def __init__(
        self,
        *args,
        bitcoin_version: str | None = None,
        bitcoin_prune: int = 0,
        bitcoin_dbcache: int = 450,
        prom_exporter_port: int | None = 9100,
        bitcoind_exporter_port: int | None = 9332,
        outbound_wireguard: str | None = None,
        **kwargs,
    ):
        self.bitcoin_version = bitcoin_version
        self.bitcoin_prune = bitcoin_prune
        self.bitcoin_dbcache = bitcoin_dbcache
        self.prom_exporter_port = prom_exporter_port
        self.bitcoind_exporter_port = bitcoind_exporter_port
        self.outbound_wireguard = outbound_wireguard
        super().__init__(*args, **kwargs)

    @property
    def bmon_ip(self):
        """An IP that makes the host routable to any other bmon host."""
        return self.wireguards["wg-bmon"].ip


def get_hosts() -> tuple[dict[str, wireguard.Server], dict[str, Host]]:
    hostsfile = Path(os.environ["BMON_HOSTS_FILE"])
    data = yaml.safe_load(hostsfile.read_text())
    hosts = {str(name): Host.from_dict(name, d) for name, d in data["hosts"].items()}

    wg_servers: t.Dict[str, wireguard.Server] = {
        name: wireguard.Server.from_dict(name, d)
        for name, d in (data.get("wireguard") or {}).items()
    }

    return wg_servers, hosts


def get_bitcoind_hosts() -> t.Tuple[Host]:
    hosts = get_hosts()[1].values()
    return tuple(h for h in hosts if "bitcoind" in h.tags)


def get_server_wireguard_ip() -> str:
    [server_host] = [h for h in get_hosts()[1].values() if "server" in h.tags]
    return str(server_host.bmon_ip)


def get_hosts_for_cli() -> t.Tuple[t.Dict[str, wireguard.Server], t.Dict[str, Host]]:
    wg_servers, hosts = get_hosts()

    if cli.args.tag_filter:
        hosts = {name: h for name, h in hosts.items() if cli.args.tag_filter in h.tags}
    if cli.args.hostname_filter:
        hosts = {
            name: h
            for name, h in hosts.items()
            if re.search(cli.args.hostname_filter, name)
        }

    secrets = fscm.get_secrets(["*"], "fscm/bmon")
    host_secrets = secrets.pop("_hosts")

    for host in hosts.values():
        host.secrets.update(secrets).update(
            getattr(host_secrets, host.name, fscm.Secrets())
        )

        if host.outbound_wireguard:
            print(f"loading outbound wireguard {host.outbound_wireguard!r} for {host}")
            host.secrets.outbound_wireguard = run(
                f"pass show fscm/bmon/{host.outbound_wireguard}",
                q=True,
            ).stdout

    for host in hosts.values():
        host.check_host_keys = "accept"

    return wg_servers, hosts


def main_remote(
    host: Host,
    parent: fscm.remote.Parent,
    rebuild_docker: bool,
    wgmap: t.Dict[str, wireguard.Server],
    server_wg_ip: str,
    no_restart: bool = False,
):
    user = getpass.getuser()
    fscm.s.pkgs_install(
        "git supervisor docker.io curl python3-venv python3-pip tcpdump nmap"
    )
    fscm.s.group_member(user, "docker")
    p(docker := Path.home() / ".docker").mkdir()
    p(docker / "config.json").contents('{ "detachKeys": "ctrl-z,z" }')

    p("/etc/wireguard", sudo=True).mkdir()
    if wg_privkey := getattr(host.secrets, "wg-privkey", None):
        p("/etc/wireguard/wg-bmon-privkey", sudo=True).contents(wg_privkey).chmod("600")

    wireguard.peer(host, wgmap)

    # TODO fix outbound wireguard
    if host.outbound_wireguard and False:
        wgname = host.outbound_wireguard
        p(f"/etc/wireguard/{wgname}.conf", sudo=True).contents(
            host.secrets.outbound_wireguard
        ).chmod("600")
        systemd.enable_service(
            "wg-quick@%s" % wgname, start=True, restart=True, sudo=True
        )

    if run(f"loginctl show-user {user} | grep 'Linger=no'", quiet=True).ok:
        run(f"loginctl enable-linger {user}", sudo=True)

    if not VENV_PATH.exists():
        run(f"python3 -m venv {VENV_PATH}").assert_ok()

    lineinfile(
        f"/home/{user}/.bashrc",
        "source ${HOME}/.venv/bin/activate",
        regex="venv/bin/activate",
    )

    lineinfile(f"/home/{user}/.bashrc", "alias dc=docker-compose", regex="alias dc=")

    if not (bmon_path := Path.home() / "bmon").exists():
        run(f"git clone {REPO_URL} {bmon_path}").assert_ok()

    if ".venv/bin/" not in os.environ["PATH"]:
        os.environ["PATH"] = f"{VENV_PATH / 'bin'}:{os.environ['PATH']}"

    if not run("which bmon-config", quiet=True).ok:
        run(f"cd {bmon_path} && pip install -e ./infra").assert_ok()

    if not run("which docker-compose", quiet=True).ok:
        run("pip install docker-compose").assert_ok()

    run(f"cd {bmon_path} && git pull origin master").assert_ok()

    if (
        p("/etc/docker/daemon.json", sudo=True)
        .contents('{ "log-driver": "journald" }')
        .changes
    ):
        run("systemctl restart docker", sudo=True).assert_ok()

    if lineinfile(
        "/etc/systemd/journald.conf",
        "SystemMaxUse=300M",
        "SystemMaxUse=",
        sudo=True,
    ):
        run("systemctl restart systemd-journald", sudo=True).assert_ok()

    if "server" in host.tags:
        provision_bmon_server(host, parent, rebuild_docker, server_wg_ip, no_restart)
    elif "bitcoind" in host.tags:
        provision_monitored_bitcoind(
            host, parent, rebuild_docker, server_wg_ip, no_restart
        )


def provision_bmon_server(
    host: Host,
    parent: fscm.remote.Parent,
    rebuild_docker: bool,
    server_wg_ip: str,
    no_restart: bool = False,
):
    assert (username := getpass.getuser()) != "root"

    os.chdir(bmon_path := Path.home() / "bmon")

    p(bmon_path / ".env").contents(prod_env(host, server_wg_ip)).chmod("600")
    run("bmon-config -t prod")
    docker_compose = VENV_PATH / "bin" / "docker-compose"
    assert docker_compose.exists()

    if rebuild_docker:
        run(f"{docker_compose} --profile server --profile prod build")

    p(sysd := Path.home() / ".config" / "systemd" / "user").mkdir()

    if (
        p(sysd / "bmon-server.service")
        .contents(
            parent.template(
                "./etc/systemd-server-unit.service",
                user=username,
                bmon_dir=bmon_path,
                docker_compose_path=docker_compose,
            )
        )
        .changes
    ):
        run("systemctl --user daemon-reload")

    systemd.enable_service("bmon-server")

    fscm.s.pkgs_install("nginx")
    p("/etc/nginx/sites-enabled/default", sudo=True).rm()
    if (
        p("/etc/nginx/sites-enabled/bmon.conf", sudo=True)
        .contents(parent.get_file("./etc/server-nginx.conf"))
        .changes
    ):
        run("systemctl restart nginx", sudo=True)

    # Files, like pruned datadirs, will be served out of here.
    p("/www/data", sudo=True).chmod("755").chown("james:james").mkdir()

    if not no_restart:
        run("systemctl --user restart bmon-server").assert_ok()


def provision_monitored_bitcoind(
    host: Host,
    parent: fscm.remote.Parent,
    rebuild_docker: bool,
    server_wg_ip: str,
    no_restart: bool = False,
):
    assert (username := getpass.getuser()) != "root"
    os.chdir(bmon_path := Path.home() / "bmon")

    p(bmon_path / ".env").contents(prod_env(host, server_wg_ip)).chmod("600")
    run(f"bmon-config -t prod --hostname {host.name}").assert_ok()
    docker_compose = VENV_PATH / "bin" / "docker-compose"
    assert docker_compose.exists()

    if rebuild_docker:
        run(f"{docker_compose} --profile bitcoind --profile prod build").assert_ok()

    p("/etc/logrotate.d/bmon-bitcoind.conf", sudo=True).contents(
        parent.template(
            "./etc/bitcoind-logrotate.conf",
            BMON_DIR=str(bmon_path),
            USER=username,
            HOME=Path.home(),
        )
    ).chown('root:root').chmod('644')

    if (
        p("/etc/systemd/system/timers.target.wants/logrotate.timer", sudo=True)
        .content(
            dedent(
                """
        [Unit]
        Description=Hourly rotation of log files
        Documentation=man:logrotate(8) man:logrotate.conf(5)

        [Timer]
        OnCalendar=hourly
        AccuracySec=1h
        Persistent=true

        [Install]
        WantedBy=timers.target
        """
            )
        )
        .changes
    ):
        run("systemctl daemon-reload", sudo=True)

    p(sysd := Path.home() / ".config" / "systemd" / "user").mkdir()

    if host.bitcoin_prune:
        DATADIR_URL = f"http://{server_wg_ip}/bitcoin-pruned-550.tar.gz"
        # Load in a prepopulated pruned datadir if necessary.
        btc_size_kb = int(
            run(f"du -s {bmon_path}/services/prod/bitcoin/data").stdout.split()[0]
        )
        gb_in_kb = 1000**2

        if btc_size_kb < gb_in_kb:
            btc_data = bmon_path / "services/prod/bitcoin/data"
            print(f"Fetching prepopulated (pruned) datadir from {DATADIR_URL}")
            run(f"curl -s {DATADIR_URL} | tar xz -C /tmp").assert_ok()
            run(f"rm -rf {btc_data}").assert_ok()
            run(f"mv /tmp/bitcoin-pruned-550 {btc_data}").assert_ok()
            # If we don't have a debug.log file, docker will make a directory out
            # of it during the mount process of bitcoind-watcher.
            run(f"touch {btc_data}/debug.log")
            print(f"Installed prepopulated pruned dir at {btc_data}")

    if (
        p(sysd / "bmon-bitcoind.service")
        .contents(
            parent.template(
                "./etc/systemd-bitcoind-unit.service",
                user=username,
                bmon_dir=bmon_path,
                docker_compose_path=docker_compose,
            )
        )
        .changes
    ):
        run("systemctl --user daemon-reload")

    if not no_restart:
        systemd.enable_service("bmon-bitcoind")
        run("systemctl --user restart bmon-bitcoind").assert_ok()


@cli.cmd
def deploy(rebuild_docker: bool = False, no_restart: bool = False):
    """Provision necessary dependencies and config files on hosts."""
    wgsmap, hostmap = get_hosts_for_cli()
    hosts = list(hostmap.values())
    includes_server = any('server' in h.tags for h in hosts)
    server_wg_ip = get_server_wireguard_ip()

    with executor(*hosts) as exec:
        exec.allow_file_access("./etc/*", "./etc/**/*")

        # Deploy to server first to run database migrations.
        if includes_server and not exec.run_on_hosts(
            lambda h: "server" in h.tags,
            main_remote,
            rebuild_docker,
            wgsmap,
            server_wg_ip,
            no_restart,
        ).ok:
            print("Deploy to server failed!")
            sys.exit(1)

        exec.run_on_hosts(
            lambda h: "server" not in h.tags,
            main_remote,
            rebuild_docker,
            wgsmap,
            server_wg_ip,
            no_restart,
        )


@cli.cmd
def status():
    """Check status on hosts."""
    runall("supervisorctl status")


@cli.cmd
def bitcoind_logs():
    """Present a brief tail of all known bitcoind logs."""
    runall("tail -n 20 /bmon/logs/bitcoind-stdout.log", host_filter=r"bmon-b\d+")


@cli.cmd
def runall(cmd: str, sudo: bool = False):
    """Run some command across all hosts."""
    _, hostmap = get_hosts_for_cli()
    hosts = list(hostmap.values())

    with executor(*hosts) as exec:
        if not (res := exec.run(_run_cmd, cmd, sudo)).ok:
            print(f"Command failed on hosts: {res.failed}")
            sys.exit(1)


def _run_cmd(cmd: str, sudo: bool):
    return fscm.run(cmd, sudo=sudo)


def main():
    os.environ.setdefault("BMON_HOSTS_FILE", "./infra/hosts_prod.yml")
    cli.run()


if __name__ == "__main__":
    # You should be using the bmon-infra entrypoint though.
    main()
