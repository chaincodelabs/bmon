import datetime
import io

import fastavro
import pytest

from . import logparse, conftest, models


def test_mempoollistener():
    listener = logparse.MempoolListener()

    got = listener.process_line(
        "2022-10-17T17:57:43.861480Z AcceptToMemoryPool: peer=11: accepted fa4f08dfe610593b505ca5cd8b2ba061ea15a4c480a63dd75b00e2eaddf9b42b (poolsz 11848 txn, 25560 kB)")  # noqa

    assert got
    assert got.peer_num == 11
    assert got.txhash == "fa4f08dfe610593b505ca5cd8b2ba061ea15a4c480a63dd75b00e2eaddf9b42b"
    assert got.timestamp == logparse.get_time("2022-10-17T17:57:43.861480Z")
    assert got.pool_size_kb == 25560
    assert got.pool_size_txns == 11848

    # Ensure we can write to avro
    fake_avro_file = io.BytesIO()
    fastavro.writer(
        fake_avro_file, models.mempool_activity_avro_schema, [got.avro_record()])
    assert len(fake_avro_file.getvalue()) > 0


@pytest.mark.django_db
def test_reorg():
    listeners = [
        logparse.BlockConnectedListener(),
        logparse.BlockDisconnectedListener(),
        logparse.ReorgListener(),
    ]
    got = []

    logdata = conftest.read_data_file("logs_reorg_23.txt")

    for line in logdata:
        for listener in listeners:
            if (item := listener.process_line(line)):
                got.append(item)

    [bc1,
     bc2,
     bc3,

     bd1,
     bd2,

     br1,
     br2,

     reorg,

     bc4] = got

    assert isinstance(bc1, models.BlockConnectedEvent)
    assert bc1.blockhash == '0f9188f13cb7b2c71f2a335e3a4fc328bf5beb436012afca590b1a11466e2206'
    assert bc1.height == 0

    assert isinstance(bc2, models.BlockConnectedEvent)
    assert bc2.blockhash == '04fb55cec0a7e506d64b16ed26eefc2ccf66a927d6f1a3bb113df1630d59859f'
    assert bc2.height == 1

    assert isinstance(bc3, models.BlockConnectedEvent)
    assert bc3.blockhash == '3cfd126d960a9b87823fd94d48121f774aac448c9a6f1b48efc547c61f9b8c1f'
    assert bc3.height == 2

    assert isinstance(bd1, models.BlockDisconnectedEvent)
    assert bd1.blockhash == '3cfd126d960a9b87823fd94d48121f774aac448c9a6f1b48efc547c61f9b8c1f'
    assert bd1.height == 2

    assert isinstance(bd2, models.BlockDisconnectedEvent)
    assert bd2.blockhash == '04fb55cec0a7e506d64b16ed26eefc2ccf66a927d6f1a3bb113df1630d59859f'
    assert bd2.height == 1

    assert isinstance(br1, models.BlockConnectedEvent)
    assert br1.blockhash == '1397a170ca910a5689af809abf4cb25070c36e7bc023e2a23064652543b7f5eb'
    assert br1.height == 1

    assert isinstance(br2, models.BlockConnectedEvent)
    assert br2.blockhash == '54d205ef4c87ce25fddb28b0c792f44b308613e75d88b61d71906aed70c64b48'
    assert br2.height == 2

    assert isinstance(reorg, models.ReorgEvent)
    assert reorg.min_height == 1
    assert reorg.max_height == 2
    assert reorg.old_blockhashes == [
        '04fb55cec0a7e506d64b16ed26eefc2ccf66a927d6f1a3bb113df1630d59859f',
        '3cfd126d960a9b87823fd94d48121f774aac448c9a6f1b48efc547c61f9b8c1f',
    ]
    assert reorg.new_blockhashes == [
        '1397a170ca910a5689af809abf4cb25070c36e7bc023e2a23064652543b7f5eb',
        '54d205ef4c87ce25fddb28b0c792f44b308613e75d88b61d71906aed70c64b48',
    ]
    assert reorg.finished_timestamp == datetime.datetime.fromisoformat(
        '2022-10-22 14:22:49.358839+00:00')

    assert isinstance(bc4, models.BlockConnectedEvent)
    assert bc4.blockhash == '7c06da428d44f32c0a77f585a44181d3f71fcbc55b44133d60d6941fa9165b0d'
    assert bc4.height == 3

    for model in got:
        model.full_clean()

    reorg.save()


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
