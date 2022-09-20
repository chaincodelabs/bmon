#!/usr/bin/env python3

import sys
from string import Template
from pathlib import Path
from types import SimpleNamespace

from fscm import p
from clii import App


cli = App()
ENV = SimpleNamespace()


def grafana():
    return Path('./etc/grafana-template.ini').read_text()


def grafana_datasources():
    return Template(Path('./etc/grafana-datasources-template.yml').read_text()).safe_substitute(
        PROM_ADDRESS=ENV.PROM_ADDRESS,
        LOKI_ADDRESS=ENV.LOKI_ADDRESS,
        ALERTMAN_ADDRESS=ENV.ALERTMAN_ADDRESS,
    )


def prom():
    return Template(Path('./etc/prom-template.yml').read_text()).safe_substitute(
        ALERTMAN_ADDRESS=ENV.ALERTMAN_ADDRESS,
        PROM_SCRAPE_SD_URL=ENV.PROM_SCRAPE_SD_URL,
    )


def loki():
    return Template(Path('./etc/loki-template.yml').read_text()).safe_substitute(
        LOKI_PORT=ENV.LOKI_PORT,
        ALERTMAN_ADDRESS=ENV.ALERTMAN_ADDRESS,
    )


def alertman():
    return Path('./etc/alertmanager-template.yml').read_text()


def promtail():
    return Template(Path('./etc/promtail-template.yml').read_text()).safe_substitute(
        PROMTAIL_PORT=ENV.PROMTAIL_PORT,
        LOKI_ADDRESS=ENV.LOKI_ADDRESS,
        HOSTNAME='?',
        BITCOIN_GIT_SHA='?',
        BITCOIN_VERSION='?',
    )


def bitcoind():
    auth_line = get_bitcoind_auth_line(ENV.BITCOIN_RPC_USER, ENV.BITCOIN_RPC_PASSWORD)
    return Template(Path('./etc/bitcoin/bitcoin-template.conf').read_text()).safe_substitute(
        RPC_AUTH_LINE=auth_line,
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


@cli.main
@cli.arg('env', '-e')
def make_env(env: str ='.env'):
    global ENV
    ENV = SimpleNamespace(
        **dict(
            i.split('=', 1) for i in
            filter(None, Path(env).read_text().splitlines())))

    p(root := Path(ENV.ENV_ROOT)).mkdir()

    p(grafetc := root / 'grafana' / 'etc').mkdir()
    p(grafetc / 'grafana.ini').contents(grafana())
    p(var := root / 'grafana' / 'var').mkdir()
    p(dashboards := var / 'dashboards').mkdir()
    p(dashboards / 'bitcoind.json').contents(
        Path('./etc/grafana/dashboards/bitcoind.json').read_text())
    p(prov := grafetc / 'provisioning').mkdir()
    p(datasources := prov / 'datasources').mkdir()
    p(dashprov := prov / 'dashboards').mkdir()
    p(dashprov / 'default.yml').contents(
        Path('./etc/grafana-dashboards-template.yml').read_text())
    p(datasources / 'datasource.yml').contents(grafana_datasources())

    p(lokipath := root / 'loki').mkdir()
    p(lokietc := lokipath / 'etc').mkdir()
    p(lokietc / 'local-config.yaml').contents(loki())

    p(prometc := root / 'prom' / 'etc').mkdir()
    p(root / 'prom' / 'data').mkdir()
    p(prometc / 'prometheus.yml').contents(prom())

    p(am := root / 'alertman').mkdir()
    p(am / 'config.yml').contents(alertman())

    p(btc := root / 'bitcoin').mkdir()
    p(btcdata := root / 'bitcoin' / 'data').mkdir()

    p(promtailp := root / 'promtail').mkdir()
    p(promtailp / 'config.yml').contents(promtail())

    p(btc := root / 'bitcoin' / 'data').mkdir()
    p(btc / 'bitcoin.conf').contents(bitcoind())


if __name__ == "__main__":
    cli.run()
