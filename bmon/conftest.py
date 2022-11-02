import os
import json
import multiprocessing
from pathlib import Path
import typing as t

import redis
import pytest
from django.conf import settings

from bmon import bitcoin, models


def read_data_file(dirname) -> t.List[str]:
    dir_path = Path(os.path.dirname(os.path.realpath(__file__)))
    return (dir_path / "testdata" / dirname).read_text().splitlines()


def read_json_data(filename):
    dir_path = Path(os.path.dirname(os.path.realpath(__file__)))
    return json.loads((dir_path / "testdata" / filename).read_text())


@pytest.fixture(scope="session", autouse=True)
def raw_bitcoind_version():
    bitcoin.api.read_raw_bitcoind_version = lambda: "v23.99.0-447f50e4aed9"


@pytest.fixture(autouse=True)
def clear_redis():
    for url in (settings.REDIS_SERVER_URL, settings.REDIS_LOCAL_URL):
        redis.Redis.from_url(url).flushall()


def make_host(name: str, bitcoin_version: str = "v23.0"):
    return models.Host.objects.get_or_create(
        name=name,
        cpu_info="test",
        memory_bytes=1024,
        nproc=multiprocessing.cpu_count(),
        bitcoin_version=bitcoin_version,
        bitcoin_gitref="",
        bitcoin_gitsha="",
        bitcoin_dbcache=int(settings.BITCOIN_DBCACHE),
        bitcoin_prune=int(settings.BITCOIN_PRUNE),
        bitcoin_extra={
            "flags": "-regtest",
        },
        defaults={
            "region": "",
        },
    )[0]


@pytest.fixture()
def fake_hosts():
    host1 = make_host('bitcoind', 'v0.18.0')
    host2 = make_host('bitcoind-02')
    return host1, host2
