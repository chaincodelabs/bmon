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


@pytest.fixture()
def fake_hosts():
    host1, _ = models.Host.objects.get_or_create(
        name='bitcoind',
        cpu_info='test',
        memory_bytes=1024,
        nproc=multiprocessing.cpu_count(),
        bitcoin_version='v0.18.0',
        bitcoin_gitref='',
        bitcoin_gitsha='',
        bitcoin_dbcache=int(settings.BITCOIN_DBCACHE),
        bitcoin_prune=int(settings.BITCOIN_PRUNE),
        bitcoin_extra={
            "flags": '-regtest',
        },
        defaults={
            "region": "",
        },
    )

    host2, _ = models.Host.objects.get_or_create(
        name='bitcoind-02',
        cpu_info='test',
        memory_bytes=1024,
        nproc=multiprocessing.cpu_count(),
        bitcoin_version='v23.0',
        bitcoin_gitref='',
        bitcoin_gitsha='',
        bitcoin_dbcache=int(settings.BITCOIN_DBCACHE),
        bitcoin_prune=int(settings.BITCOIN_PRUNE),
        bitcoin_extra={
            "flags": '-regtest',
        },
        defaults={
            "region": "",
        },
    )

    return host1, host2
