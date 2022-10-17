from django.db import models


class LogProgress(models.Model):
    """
    Records the latest hash of a log line responsible for generating an event on a
    given host.

    This is used to skip past already-processed log entries on startup.
    """
    host = models.CharField(max_length=200, unique=True)
    timestamp = models.DateTimeField()
    loghash = models.CharField(max_length=200)


class ConnectBlockEvent(models.Model):
    """
    From UpdateTip

    Sample:

    2019-08-09T16:28:42Z UpdateTip: new best=00000000000000000001d80d14ee4400b6d9c851debe27e6777f3876edd4ad1e height=589349 version=0x20800000 log2_work=90.944215 tx=443429260 date='2019-08-09T16:27:43Z' progress=1.000000 cache=8.7MiB(64093txo) warning='44 of last 100 blocks have unexpected version'
    """
    host = models.CharField(max_length=200)
    timestamp = models.DateTimeField()

    blockhash = models.CharField(max_length=80)
    height = models.IntegerField()
    log2_work = models.FloatField()
    total_tx_count = models.IntegerField()

    # Only offered in 0.13+
    version = models.CharField(null=True, blank=True, max_length=200)
    date = models.DateTimeField()
    # Only offered in 0.18+
    cachesize_mib = models.FloatField(null=True)
    cachesize_txo = models.IntegerField()
    warning = models.CharField(null=True, blank=True, max_length=1024)


class ConnectBlockDetails(models.Model):
    """
    ConnectBlock measurements

    Sample:

    2019-07-29T18:34:17Z   - Load block from disk: 0.00ms [23.45s]
    2019-07-29T18:34:17Z     - Sanity checks: 0.01ms [17.24s (18.07ms/blk)]
    2019-07-29T18:34:17Z     - Fork checks: 0.04ms [0.09s (0.10ms/blk)]
    2019-07-29T18:34:17Z       - Connect 1982 transactions: 41.16ms (0.021ms/tx, 0.008ms/txin) [154.90s (162.37ms/blk)]
    2019-07-29T18:34:17Z     - Verify 4917 txins: 41.23ms (0.008ms/txin) [177.91s (186.49ms/blk)]
    2019-07-29T18:34:17Z     - Index writing: 13.62ms [13.08s (13.71ms/blk)]
    2019-07-29T18:34:17Z   - Connect total: 55.33ms [208.93s (219.00ms/blk)]
    2019-07-29T18:34:17Z   - Flush: 10.58ms [104.30s (109.33ms/blk)]
    2019-07-29T18:34:17Z   - Writing chainstate: 0.09ms [0.10s (0.10ms/blk)]
    2019-07-29T18:34:17Z   - Connect postprocess: 70.64ms [8.14s (8.53ms/blk)]
    2019-07-29T18:34:17Z - Connect block: 136.63ms [344.92s (361.55ms/blk)]
    """
    host = models.CharField(max_length=200)
    # The latest logline in the connectblock measurements.
    timestamp = models.DateTimeField()

    blockhash = models.CharField(max_length=80)
    height = models.IntegerField()
    load_block_from_disk_time_ms = models.FloatField()
    sanity_checks_time_ms = models.FloatField()
    fork_checks_time_ms = models.FloatField()
    txin_count = models.IntegerField()
    # i.e. nChainTx
    tx_count = models.IntegerField()
    connect_txs_time_ms = models.FloatField()
    verify_time_ms = models.FloatField()
    index_writing_time_ms = models.FloatField()
    connect_total_time_ms = models.FloatField()
    flush_coins_time_ms = models.FloatField()
    flush_chainstate_time_ms = models.FloatField()
    connect_postprocess_time_ms = models.FloatField()
    connectblock_total_time_ms = models.FloatField()


class MempoolAccept(models.Model):
    """
    A transaction is accepted to the mempool.

    2022-10-17T17:57:43.861480Z AcceptToMemoryPool: peer=11: accepted fa4f08dfe610593b505ca5cd8b2ba061ea15a4c480a63dd75b00e2eaddf9b42b (poolsz 11848 txn, 25560 kB)
    """
    host = models.CharField(max_length=200)
    timestamp = models.DateTimeField()
    txhash = models.CharField(max_length=80)
    peer_num = models.IntegerField()
    pool_size_txns = models.IntegerField()
    pool_size_kb = models.IntegerField()


class ProcessLineError(models.Model):
    """
    Created when a listener fails to process a line.
    """
    host = models.CharField(max_length=200)
    timestamp = models.DateTimeField(auto_now_add=True, blank=True)
    listener = models.CharField(max_length=240)
    line = models.CharField(max_length=2048)
