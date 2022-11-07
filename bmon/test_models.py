import pytest

from .bitcoind_tasks import create_host_record


@pytest.mark.django_db
def test_create_host():
    h = create_host_record()
    assert h.bitcoin_dbcache == 450
    assert h.bitcoin_extra == {'flags': '-regtest'}
    assert h.bitcoin_prune == 0
    assert not h.bitcoin_listen
