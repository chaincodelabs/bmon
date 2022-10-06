import datetime

from . import logparse, conftest


def test_connectblockevent():
    logdata = conftest.read_data_file("logs_connectblock_basic.txt")
    listener = logparse.ConnectBlockListener()
    got = []

    for line in logdata:
        if (item := listener.process_line(line)):
            got.append(item)

    assert len(got) == 4

    [cb1, cbd1, cb2, cbd2] = got

    for e in got:
        assert e.height == 589349
        assert e.blockhash == (
            '00000000000000000001d80d14ee4400b6d9c851debe27e6777f3876edd4ad1e')

    for i, e in enumerate([cb1, cb2]):
        assert e.timestamp == logparse.get_time('2019-08-09T16:28:42+00:00')

        assert e.cachesize_mib == 8.7
        assert e.cachesize_txo == 64093
        assert e.date == datetime.datetime.fromisoformat('2019-08-09T16:27:43Z')

        if i == 0:
            assert e.warning == '44 of last 100 blocks have unexpected version'
        else:
            assert not e.warning

    for e in [cbd1, cbd2]:
        assert e.timestamp == logparse.get_time("2019-07-29T18:34:40Z")

        assert e.load_block_from_disk_time_ms == 0.00
        assert e.sanity_checks_time_ms == 0.01
        assert e.fork_checks_time_ms == 0.04
        assert e.index_writing_time_ms == 13.62
        assert e.connect_total_time_ms == 55.33
        assert e.flush_coins_time_ms == 10.58
        assert e.flush_chainstate_time_ms == 0.09
        assert e.connect_postprocess_time_ms == 70.64
        assert e.connectblock_total_time_ms == 136.63

        assert e.tx_count == 1982
        assert e.txin_count == 4917

    # Do basic test across many versions.

    for version in (
            # 0.12,
            # 0.13,
            0.18,
    ):
        print(f"Testing version {version}")
        got = []
        logdata = conftest.read_data_file(
            "logs_gotblock_0{}.txt".format(str(version)[-2:]))

        for line in logdata:
            if (item := listener.process_line(line)):
                got.append(item)

        assert len(got) == 2
        [cb, cbd] = got

        for e in got:
            assert e.height == 589733
            assert e.blockhash == (
                '00000000000000000010e1543aa317eb5e34148afda9b9da10edbdd9cb8a1c8d')

        assert cbd.load_block_from_disk_time_ms is not None
        assert cbd.index_writing_time_ms is not None
        assert cbd.connect_total_time_ms is not None
        assert cbd.flush_coins_time_ms is not None
        assert cbd.flush_chainstate_time_ms is not None
        assert cbd.connect_postprocess_time_ms is not None
        assert cbd.connectblock_total_time_ms is not None

        if version >= 0.18:
            assert cbd.sanity_checks_time_ms is not None
            assert cbd.fork_checks_time_ms is not None

        assert cbd.tx_count == 1996
        assert cbd.txin_count == 4055

        assert cb.date.year == 2019
        assert cb.date.hour == 4
        assert cb.cachesize_txo is not None

        if version >= 0.18:
            assert cb.cachesize_mib is not None
