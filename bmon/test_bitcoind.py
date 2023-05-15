
from bmon.bitcoin import api


def test_get_version():
    api.bitcoind_version('25.0rc2') == ((25, 0), None)
    api.bitcoind_version('0.14.0') == ((0, 14, 0), None)
