from . import logparse, conftest


class MockReceiver:
    def __init__(self):
        self.got = []

    def receive(self, event):
        self.got.append(event)


def test_connectblockevent():
    recv = MockReceiver()
    logdata = conftest.read_data_file("logs_connectblock_basic.txt")
    listener = logparse.ConnectBlockListener(recv)

    for line in logdata:
        listener.process_msg(line)

    assert len(recv.got) == 2

    for i, e in enumerate(recv.got):
        assert e.height == 589349
        assert e.blockhash == (
            '00000000000000000001d80d14ee4400b6d9c851debe27e6777f3876edd4ad1e')

        assert e.load_block_from_disk_time_ms == 0.00
        assert e.sanity_checks_time_ms == 0.01
        assert e.fork_checks_time_ms == 0.04
        assert e.index_writing_time_ms == 13.62
        assert e.callbacks_time_ms == 0.04
        assert e.connect_total_time_ms == 55.33
        assert e.flush_coins_time_ms == 10.58
        assert e.flush_chainstate_time_ms == 0.09
        assert e.connect_postprocess_time_ms == 70.64
        assert e.connectblock_total_time_ms == 136.63

        assert e.tx_count == 1982
        assert e.txin_count == 4917

        assert e.cachesize_mib == 8.7
        assert e.cachesize_txo == 64093
        assert e.date == '2019-08-09T16:27:43Z'

        if i == 0:
            assert e.warning == '44 of last 100 blocks have unexpected version'
        else:
            assert not e.warning

    recv.got = []
    listener.reset()
    logdata = conftest.read_data_file("logs_connectblock_010.txt")

    for line in logdata:
        listener.process_msg(line)

    assert len(recv.got) == 1
    [e] = recv.got
    print(e.__dict__)
    assert e.height == 589733
    assert e.blockhash == (
        '00000000000000000010e1543aa317eb5e34148afda9b9da10edbdd9cb8a1c8d')

    assert e.load_block_from_disk_time_ms == 0.00
    assert e.sanity_checks_time_ms is None
    assert e.fork_checks_time_ms is None
    assert e.index_writing_time_ms == 4.71
    assert e.callbacks_time_ms == 0.03
    assert e.connect_total_time_ms == 1235.09
    assert e.flush_coins_time_ms == 10.92
    assert e.flush_chainstate_time_ms == 0.43
    assert e.connect_postprocess_time_ms == 10.45
    assert e.connectblock_total_time_ms == 1256.89

    assert e.tx_count == 1996
    assert e.txin_count == 4055

    assert e.cachesize_mib is None
    assert e.cachesize_txo == 23091
    # 2019-08-12 04:01:32
    assert e.date == '2019-08-12 04:01:32'

    # Do basic test across many versions.

    for version in (0.12, 0.13, 0.18):
        print(f"Testing version {version}")
        recv.got = []
        logdata = conftest.read_data_file(
            "logs_gotblock_0{}.txt".format(str(version)[-2:]))

        for line in logdata:
            listener.process_msg(line)

        assert len(recv.got) == 1
        [e] = recv.got
        assert e.height == 589733
        assert e.blockhash == (
            '00000000000000000010e1543aa317eb5e34148afda9b9da10edbdd9cb8a1c8d')

        assert e.load_block_from_disk_time_ms is not None
        assert e.index_writing_time_ms is not None
        assert e.callbacks_time_ms is not None
        assert e.connect_total_time_ms is not None
        assert e.flush_coins_time_ms is not None
        assert e.flush_chainstate_time_ms is not None
        assert e.connect_postprocess_time_ms is not None
        assert e.connectblock_total_time_ms is not None

        if version >= 0.18:
            assert e.sanity_checks_time_ms is not None
            assert e.fork_checks_time_ms is not None

        assert e.tx_count == 1996
        assert e.txin_count == 4055
        assert '2019-08-12' in e.date
        assert '04:01:32' in e.date

        assert e.cachesize_txo is not None

        if version >= 0.18:
            assert e.cachesize_mib is not None
