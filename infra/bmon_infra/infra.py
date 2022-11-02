#!/usr/bin/env python3
# vim: set sw=4 tabstop=4
import os
import time
import sys
import json
import subprocess
import getpass
import re
import typing as t
from pathlib import Path
from textwrap import dedent

import fscm
import fscm.remote
import fscm.contrib.python
from clii import App
from fscm.remote import executor
from fscm.contrib import wireguard
from fscm import run, p, lineinfile, systemd

from . import config
from .config import Host, get_hosts


cli = App()
cli.add_argument("-t", "--tag-filter")
cli.add_argument("-r", "--role-filter")
cli.add_argument("-f", "--hostname-filter")

REPO_URL = "https://github.com/chaincodelabs/bmon.git"
VENV_PATH = Path.home() / ".venv"
BMON_PATH = Path.home() / "bmon"

fscm.remote.OPTIONS.pickle_whitelist = [r"bmon_infra\..*"]
fscm.settings.run_safe = True


BMON_BITCOIND_EXPORTER_PORT = 9333
SERVER_EXPORTER_PORT = 9334

BMON_SSHPUBKEY = Path.home() / ".ssh" / "bmon-ed25519.pub"


def get_server_wireguard_ip() -> str:
    [server_host] = [h for h in get_hosts()[1].values() if "server" in h.tags]
    return str(server_host.bmon_ip)


def get_hosts_for_cli(
    need_secrets=True, hostname_filter=None
) -> t.Tuple[t.Dict[str, wireguard.Server], t.Dict[str, Host]]:
    wg_servers, hosts = get_hosts()

    hostname_filter = hostname_filter or cli.args.hostname_filter

    if cli.args.tag_filter:
        hosts = {name: h for name, h in hosts.items() if cli.args.tag_filter in h.tags}
    if hostname_filter:
        hosts = {
            name: h for name, h in hosts.items() if re.search(hostname_filter, name)
        }

    if need_secrets:
        secrets = fscm.get_secrets(["*"], "fscm/bmon")
        host_secrets = secrets.pop("_hosts")

        for host in hosts.values():
            host.secrets.update(secrets).update(
                getattr(host_secrets, host.name, fscm.Secrets())
            )

            if host.outbound_wireguard:
                print(
                    f"loading outbound wireguard {host.outbound_wireguard!r} for {host}"
                )
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
    wgmap: t.Dict[str, wireguard.Server],
    server_wg_ip: str,
    restart_spec: str = "",
    ssh_pubkey: str = "",
    sync_to_tip: bool = False,
):
    assert (user := getpass.getuser()) != "root"

    fscm.s.pkgs_install(
        "git docker.io curl python3-venv python3-pip tcpdump nmap ntp "
        "ripgrep libpq5 netcat-traditional jq"
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

    if ssh_pubkey:
        lineinfile(
            Path.home() / ".ssh" / "authorized_keys", ssh_pubkey, regex=ssh_pubkey[:40]
        )

    if run(f"loginctl show-user {user} | grep 'Linger=no'", check=False, quiet=True).ok:
        run(f"loginctl enable-linger {user}", sudo=True)

    if not VENV_PATH.exists():
        run(f"python3 -m venv {VENV_PATH}")

    lineinfile(
        f"/home/{user}/.bashrc",
        "source ${HOME}/.venv/bin/activate",
        regex="venv/bin/activate",
    )

    lineinfile(f"/home/{user}/.bashrc", "alias dc=docker-compose", regex="alias dc=")

    if not BMON_PATH.exists():
        run(f"git clone {REPO_URL} {BMON_PATH}")

    if ".venv/bin/" not in os.environ["PATH"]:
        os.environ["PATH"] = f"{VENV_PATH / 'bin'}:{os.environ['PATH']}"

    if not run("which bmon-config", check=False, quiet=True).ok:
        run(f"cd {BMON_PATH} && pip install -e ./infra")

    if not run("which docker-compose", check=False, quiet=True).ok:
        run("pip install docker-compose")

    run(f"cd {BMON_PATH} && git pull --ff-only origin master")

    if (
        p("/etc/docker/daemon.json", sudo=True)
        .contents('{ "log-driver": "journald" }')
        .changes
    ):
        run("systemctl restart docker", sudo=True)

    if lineinfile(
        "/etc/systemd/journald.conf",
        "SystemMaxUse=300M",
        "SystemMaxUse=",
        sudo=True,
    ):
        run("systemctl restart systemd-journald", sudo=True)

    if "server" in host.tags:
        provision_bmon_server(host, parent, server_wg_ip, restart_spec)
    elif "bitcoind" in host.tags:
        provision_monitored_bitcoind(
            host, parent, server_wg_ip, restart_spec, sync_to_tip
        )


def provision_bmon_server(
    host: Host,
    parent: fscm.remote.Parent,
    server_wg_ip: str,
    restart_spec: str,
):
    assert (username := getpass.getuser()) != "root"
    docker_compose = VENV_PATH / "bin" / "docker-compose"
    assert docker_compose.exists()
    pip = VENV_PATH / "bin" / "pip"
    assert pip.exists()

    os.chdir(BMON_PATH)
    p(BMON_PATH / ".env").contents(config.prod_env(host, server_wg_ip)).chmod("600")
    run("bmon-config -t prod")

    if not (VENV_PATH / "bin" / "pgcli").exists():
        run(f"{pip} install pgcli")

    p(sysd := Path.home() / ".config" / "systemd" / "user").mkdir()

    if (
        p(sysd / "bmon-server.service")
        .contents(
            parent.template(
                "./etc/systemd-server-unit.service",
                user=username,
                bmon_dir=BMON_PATH,
                docker_compose_path=docker_compose,
            )
        )
        .changes
    ):
        run("systemctl --user daemon-reload")

    systemd.enable_service("bmon-server")

    # Optional Sentry installation
    sentry_dir = Path.home() / "sentry"
    if sentry_dir.exists():
        if (
            p(sysd / "bmon-sentry.service")
            .contents(
                parent.template(
                    "./etc/systemd-server-sentry-unit.service",
                    user=username,
                    sentry_dir=sentry_dir,
                    docker_compose_path=docker_compose,
                )
            )
            .changes
        ):
            run("systemctl --user daemon-reload")

        systemd.enable_service("bmon-sentry")

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

    # Update the docker image.
    run(f"{docker_compose} pull web")
    run(f"{docker_compose} run --rm web ./manage.py migrate")

    def cycle(services):
        run(f"{docker_compose} stop {services}")
        run(f"{docker_compose} rm -f {services}")
        run(f"{docker_compose} up -d {services}")

    match restart_spec:
        case "":
            cycle("web server-task-worker server-monitor")
        case "none":
            pass
        case "all":
            run("systemctl --user restart bmon-server")
        case _:
            run(f"{docker_compose} pull {restart_spec}")
            cycle(f"web server-task-worker {restart_spec}")


def provision_monitored_bitcoind(
    host: Host,
    parent: fscm.remote.Parent,
    server_wg_ip: str,
    restart_spec: str,
    sync_to_tip: bool = False,
):
    assert (username := getpass.getuser()) != "root"
    docker_compose = VENV_PATH / "bin" / "docker-compose"
    python = VENV_PATH / "bin" / "python"
    assert docker_compose.exists()

    # We can't use docker-compose yet because the .env file may not necessarily exist
    # yet, or it may be out of date in terms of the desired bitcoind version.
    run(f"docker pull {host.bitcoin_docker_tag}")

    os.chdir(BMON_PATH)
    p(BMON_PATH / ".env").contents(config.prod_env(host, server_wg_ip)).chmod("600")
    run("bmon-config -t prod")

    p("/etc/logrotate.d/bmon-bitcoind.conf", sudo=True).contents(
        parent.template(
            "./etc/bitcoind-logrotate.conf",
            BMON_DIR=str(BMON_PATH),
            USER=username,
            HOME=Path.home(),
        )
    ).chown("root:root").chmod("644")

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

    services_path = BMON_PATH / "services" / "prod"

    p(sysd := Path.home() / ".config" / "systemd" / "user").mkdir()

    if host.bitcoin_prune:
        DATADIR_URL = f"http://{server_wg_ip}/bitcoin-pruned-550.tar.gz"
        # Load in a prepopulated pruned datadir if necessary.
        btc_size_kb = int(
            run(f"du -s {services_path}/bitcoin/data", q=True).stdout.split()[0]
        )
        gb_in_kb = 1000**2

        if btc_size_kb < gb_in_kb:
            btc_data = services_path / "bitcoin/data"
            print(f"Fetching prepopulated (pruned) datadir from {DATADIR_URL}")
            run(f"curl -s {DATADIR_URL} | tar xz -C /tmp")
            run(f"rm -rf {btc_data}")
            run(f"mv /tmp/bitcoin-pruned-550 {btc_data}")
            # If we don't have a debug.log file, docker will make a directory out
            # of it during the mount process of bitcoind-watcher.
            run(f"touch {btc_data}/debug.log")
            print(f"Installed prepopulated pruned dir at {btc_data}")
            sync_to_tip = True

    if sync_to_tip:
        # Sync to tip so that we don't generate a bunch of spurious events
        run(f"{docker_compose} pull bitcoind")
        run(f"{docker_compose} up -d bitcoind")
        got = run(f"{python} dev bitcoind-wait-for-synced").stdout
        assert "Synced to height" in got
        run(f"{docker_compose} stop bitcoind")
        run(f"rm {btc_data}/debug.log")
        run(f"touch {btc_data}/debug.log")

    p(services_path / "bmon" / "credentials" / "chaincode-gcp.json").contents(
        json.dumps(host.secrets.chaincode_gcp_service_account.__dict__)  # type: ignore
    ).chmod("600")

    if (
        p(sysd / "bmon-bitcoind.service")
        .contents(
            parent.template(
                "./etc/systemd-bitcoind-unit.service",
                user=username,
                bmon_dir=BMON_PATH,
                docker_compose_path=docker_compose,
            )
        )
        .changes
    ):
        run("systemctl --user daemon-reload")

    # Update the docker image.
    run(f"{docker_compose} pull bitcoind-watcher bitcoind")

    env = config.get_env_object()
    p(env.BITCOIND_VERSION_PATH).contents(get_bitcoind_version(docker_compose))

    systemd.enable_service("bmon-bitcoind")

    def cycle(services):
        run(f"{docker_compose} stop {services}")
        run(f"{docker_compose} rm -f {services}")
        run(f"{docker_compose} up -d {services}")

    alwaysrestart = (
        "bitcoind-task-worker bitcoind-mempool-worker bitcoind-watcher "
        "bitcoind-monitor"
    )

    match restart_spec:
        case "":
            cycle(alwaysrestart)
        case "none":
            pass
        case "all":
            run("systemctl --user restart bmon-bitcoind")
        case _:
            run(f"{docker_compose} pull {restart_spec}")
            cycle(f"{alwaysrestart} {restart_spec}")


def get_bitcoind_version(docker_compose_path: str | Path = "docker-compose") -> str:
    [ver_line] = [
        i
        for i in (
            run(f"{docker_compose_path} run --rm bitcoind bitcoind -version", q=True)
            .stdout.strip()
            .splitlines()
        )
        if i.startswith("Bitcoin") and " version " in i
    ]
    bitcoind_version = ver_line.split(" version ")[-1].strip()
    return bitcoind_version.lstrip("v")


@cli.cmd
def deploy(
    rebuild_docker: bool = False,
    restart: str = "",
    sync_to_tip: bool = False,
):
    """
    Provision necessary dependencies and config files on hosts.

    Kwargs:
        restart: 'all', 'none', or specify services to restart. By default only
            restart "app" services.
    """
    if rebuild_docker:
        run("docker-compose build && docker-compose push")

    if restart == "all" and (
        cli.args.tag_filter == "server" or cli.args.hostname_filter == "bmon"
    ):
        print(
            "If you're restarting all processes, you have to deploy to the whole "
            "architecture - otherwise database connections will be stale."
        )
        sys.exit(1)

    if sync_to_tip:
        print("WARNING: sync_to_tip will potentially blow away debug.log data!")
        print("WARNING: it should only be called on new nodes.")
        if input("Are you sure you want to proceed? [y/N] ") != "y":
            sys.exit(0)

    wgsmap, hostmap = get_hosts_for_cli()
    hosts = list(hostmap.values())
    includes_server = any("server" in h.tags for h in hosts)
    server_wg_ip = get_server_wireguard_ip()

    ssh_pubkey = ""
    if BMON_SSHPUBKEY.exists():
        ssh_pubkey = BMON_SSHPUBKEY.read_text()

    with executor(*hosts) as exec:
        exec.allow_file_access("./etc/*", "./etc/**/*", str(BMON_SSHPUBKEY))

        # Deploy to server first to run database migrations.
        if (
            includes_server
            and not (
                server_result := exec.run_on_hosts(
                    lambda h: "server" in h.tags,
                    main_remote,
                    wgsmap,
                    server_wg_ip,
                    restart,
                    ssh_pubkey=ssh_pubkey,
                )
            ).ok
        ):
            print("Deploy to server failed!")
            print(server_result.failed)
            print(server_result.succeeded)
            sys.exit(1)

        bitcoind_deploy = exec.run_on_hosts(
            lambda h: "server" not in h.tags,
            main_remote,
            wgsmap,
            server_wg_ip,
            restart,
            ssh_pubkey=ssh_pubkey,
            sync_to_tip=sync_to_tip,
        )

        if not bitcoind_deploy.ok:
            print(f"bitcoind deploys failed: {bitcoind_deploy.failed}")
            sys.exit(2)
        else:
            time.sleep(2)
            docker_status_cmd = (
                'docker ps -a --filter "network=bmon_default" '
                r'--format "{{.State}}\t\t{{.RunningFor}}\t\t{{.Names}}" | '
                'sort | sed -e "s/bmon_//" | sed -Ee "s/_[0-9]+//"'
            )
            exec.run(_run_cmd, docker_status_cmd)


def bootstrap_bitcoind(regular_user: str, bmon_pubkey: str = ""):
    assert fscm.s.is_debian() or fscm.s.is_ubuntu()
    home = Path(f"/home/{regular_user}")

    fscm.contrib.python.install_python3()
    fscm.s.pkgs_install(
        "sudo vim git docker.io curl tcpdump nmap netcat ntp ripgrep libpq5 "
        "wireguard-tools"
    )
    fscm.s.group_member(regular_user, "docker")
    p(docker := home / ".docker").mkdir()
    p(docker / "config.json").contents('{ "detachKeys": "ctrl-z,z" }')

    if bmon_pubkey:
        auth_keys = home / ".ssh/authorized_keys"
        fscm.lineinfile(auth_keys, bmon_pubkey, bmon_pubkey[:30])
        p(auth_keys).chown(f"{regular_user}:{regular_user}")

    wgkey = Path("/etc/wireguard/wg-bmon-privkey")
    pubkey = ""
    if not wgkey.exists():
        pubkey = run(f"wg genkey | tee {wgkey} | wg pubkey").stdout.strip()
        run(f"cat {wgkey}")
    else:
        pubkey = run(f"cat {wgkey} | wg pubkey", q=True).stdout.strip()

    fscm.s.group_member(regular_user, "sudo")

    return pubkey


@cli.cmd
def bootstrap(host: str, sudo_pass: str, regular_user: str):
    """Bootstrap a bitcoind node."""
    host = host
    username = getpass.getuser()
    if "@" in host:
        username, host = host.split("@")
    bmon_pubkey = BMON_SSHPUBKEY.read_text() if BMON_SSHPUBKEY.exists() else ""

    with fscm.remote.mitogen_context(
        hostname=host, username=username, password=sudo_pass
    ) as (
        router,
        context,
    ):
        s = router.su(via=context, password=sudo_pass)
        got = s.call(bootstrap_bitcoind, regular_user, bmon_pubkey)

        print(f"Wireguard pubkey for {host}: {got}")


@cli.cmd
def bitcoind_logs():
    """Present a brief tail of all known bitcoind logs."""
    runall("tail -n 20 /bmon/logs/bitcoind-stdout.log", host_filter=r"bmon-b\d+")


@cli.cmd
def runall(cmd: str, sudo: bool = False):
    """Run some command across all hosts."""
    _, hostmap = get_hosts_for_cli(need_secrets=sudo)
    hosts = list(hostmap.values())

    with executor(*hosts) as exec:
        if not (res := exec.run(_run_cmd, cmd, sudo)).ok:
            print(f"Command failed on hosts: {res.failed}")
            sys.exit(1)


@cli.cmd
def rg_bitcoind(search: str):
    cli.args.hostname_filter = "(bitcoin|b-)"
    runall(f'rg "{search}" services/prod/bitcoin/data/debug.log')


@cli.cmd
def rpc(cmd: str):
    """Run a bitcoind RPC command across all bitcoind hosts."""
    _, hostmap = get_hosts_for_cli(need_secrets=False, hostname_filter="bmon")
    [server] = list(hostmap.values())

    with executor(server) as exec:
        exec.run(_run_cmd, f"docker-compose run --rm shell bmon-util rpc {cmd}")


def get_wireguard_peer_template(hostname: str):
    wg_servers, hosts = get_hosts()
    wgs = wg_servers["wg-bmon"]
    [host] = [h for h in hosts.values() if h.name == hostname]
    wg = host.wireguards["wg-bmon"]
    return wireguard.peer_config(wgs, wg)


@cli.cmd
def wireguard_peer_template(hostname: str):
    print(get_wireguard_peer_template(hostname))


def _run_rg(query: str, tail_limit: int, context: int):
    os.chdir('./bmon/services/prod/bitcoin/data')
    context_str = '' if context == -1 else f'-C {context}'
    cmd = f"rg --color=always -z {context_str} '{query}' debug.log*"
    if tail_limit != -1:
        cmd += f"| tail -n {tail_limit}"

    return subprocess.run(cmd, shell=True, capture_output=True).stdout


@cli.cmd
@cli.arg('tail_limit', '-n')
@cli.arg('context', '-C')
def rg(search_query: str, tail_limit: int = -1, context: int = -1):
    """Ripgrep through the bitcoind logs."""
    cli.args.tag_filter = 'bitcoind'

    _, hostmap = get_hosts_for_cli(need_secrets=False)
    hosts = list(hostmap.values())
    with executor(*hosts) as exec:
        got = exec.run(_run_rg, search_query, tail_limit, context)

        for host, result in got.all_results.items():
            for res in result.splitlines():
                print(f"{host.name:<12} |  ", end="")
                print(res.strip().decode())


def _run_cmd(cmd: str, sudo: bool = False):
    os.chdir(Path.home() / "bmon")
    path = os.environ["PATH"]
    os.environ["PATH"] = f"{Path.home() / '.venv/bin'}:{path}"
    return fscm.run(f"bash -c '{cmd}'", sudo=sudo, env=os.environ)


def main():
    os.environ.setdefault("BMON_HOSTS_FILE", "./infra/hosts_prod.yml")
    cli.run()


if __name__ == "__main__":
    # You should be using the bmon-infra entrypoint though.
    main()
