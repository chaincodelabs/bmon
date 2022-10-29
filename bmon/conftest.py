import os
import json
from pathlib import Path
import typing as t

import pytest

from bmon import bitcoin


def read_data_file(dirname) -> t.List[str]:
    dir_path = Path(os.path.dirname(os.path.realpath(__file__)))
    return (dir_path / "testdata" / dirname).read_text().splitlines()


def read_json_data(filename):
    dir_path = Path(os.path.dirname(os.path.realpath(__file__)))
    return json.loads((dir_path / "testdata" / filename).read_text())


@pytest.fixture(scope="session", autouse=True)
def raw_bitcoind_version():
    bitcoin.api.read_raw_bitcoind_version = lambda: "v23.99.0-447f50e4aed9"
