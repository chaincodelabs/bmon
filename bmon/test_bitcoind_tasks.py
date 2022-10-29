import pytest

from . import bitcoind_tasks, conftest


@pytest.mark.django_db
def test_peerstats():
    peerdata = conftest.read_json_data("getpeerinfo.json")
    host = bitcoind_tasks.create_host_record()
    got = bitcoind_tasks.compute_peer_stats_blocking(peerdata)

    assert got.bytesrecv == 3774372281
    assert got.bytesrecv_per_msg == {
        "addrv2": 182499,
        "block": 3747147953,
        "blocktxn": 4151572,
        "cmpctblock": 514020,
        "feefilter": 320,
        "getblocktxn": 61,
        "getdata": 13008,
        "getheaders": 10530,
        "headers": 290930,
        "inv": 3589111,
        "notfound": 6973,
        "ping": 24128,
        "pong": 23808,
        "sendaddrv2": 240,
        "sendcmpct": 693,
        "sendheaders": 240,
        "tx": 18414454,
        "verack": 240,
        "version": 1261,
        "wtxidrelay": 240,
    }

    assert got.bytessent == 8264800
    assert got.bytessent_per_msg == {
        "addrv2": 50616,
        "blocktxn": 719,
        "cmpctblock": 229710,
        "feefilter": 512,
        "getaddr": 192,
        "getblocktxn": 3338,
        "getdata": 1527809,
        "getheaders": 17901,
        "headers": 57612,
        "inv": 6195104,
        "ping": 23808,
        "pong": 24128,
        "sendaddrv2": 240,
        "sendcmpct": 3663,
        "sendheaders": 240,
        "tx": 127458,
        "verack": 240,
        "version": 1270,
        "wtxidrelay": 240,
    }

    assert got.host == host
    assert got.created_at
    assert got.num_peers == 10
    assert got.ping_max == 0.216082
    assert got.ping_mean == 0.0859731
    assert got.ping_min == 0.008235
