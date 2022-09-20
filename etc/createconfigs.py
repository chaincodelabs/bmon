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
    p(root / 'grafana' / 'var').mkdir()
    p(datasources := grafetc / 'provisioning' / 'datasources').mkdir()
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


if __name__ == "__main__":
    cli.run()
