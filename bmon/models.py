import fastavro
from django.db import models
from django.conf import settings


def _repr(instance, attrs):
    attr_strs = []
    for attr in attrs:
        attr_strs.append(f"{attr}='{getattr(instance, attr)}'")
    return f'{instance.__class__.__name__}({" ".join(attr_strs)})'


class BaseModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, blank=True)

    class Meta:
        abstract = True

    @property
    def event_type(self) -> str:
        return ''


class LogProgress(models.Model):
    """
    Records the latest hash of a log line responsible for generating an event on a
    given host.

    This is used to skip past already-processed log entries on startup.
    """
    host = models.CharField(max_length=200, unique=True)
    timestamp = models.DateTimeField()
    loghash = models.CharField(max_length=200)

    def __repr__(self):
        return _repr(self, ['host', 'timestamp', 'loghash'])

    __str__ = __repr__


PEER_UNIQUE_TOGETHER_FIELDS = (
    'host',
    'num',
    'addr',
    'connection_type',
    'inbound',
    'network',
    'services',
    'subver',
    'version',
    'relaytxes',
    'bip152_hb_from',
    'bip152_hb_to',
)

class Peer(BaseModel):
    host = models.CharField(max_length=200)
    addr = models.CharField(max_length=256)
    connection_type = models.CharField(max_length=256)
    # Called "id" in getpeerinfo()
    num = models.IntegerField()
    inbound = models.BooleanField()
    network = models.CharField(max_length=256)
    services = models.CharField(max_length=256)
    servicesnames = models.JSONField()
    subver = models.CharField(max_length=256)
    version = models.IntegerField()
    relaytxes = models.BooleanField()
    bip152_hb_to = models.BooleanField()
    bip152_hb_from = models.BooleanField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=PEER_UNIQUE_TOGETHER_FIELDS,
                name='unique_peer',
            ),
        ]

    @classmethod
    def peerinfo_data(cls, p: dict) -> tuple[dict, dict]:
        """Return the subset of getpeerinfo data that is relevant to this model."""
        out = {k: p[k] for k in PEER_UNIQUE_TOGETHER_FIELDS if k in p}
        out['num'] = p['id']
        out['host'] = settings.HOSTNAME
        defaults = {k: p[k] for k in ['servicesnames']}

        return out, defaults

    def __repr__(self):
        return _repr(self, ['host', 'addr', 'num', 'subver'])

    __str__ = __repr__


class RequestBlockEvent(BaseModel):
    """
    node0 2022-10-22T14:22:49.356891Z [msghand] [net_processing.cpp:2653] [HeadersDirectFetchBlocks] [net] Requesting block 7c06da428d44f32c0a77f585a44181d3f71fcbc55b44133d60d6941fa9165b0d from  peer=0
    """
    host = models.CharField(max_length=200)
    timestamp = models.DateTimeField()
    blockhash = models.CharField(max_length=80)

    # Sometimes we're told the height "(height)" e.g. sending getdata, other times not.
    height = models.IntegerField(null=True, blank=True)

    peer_num = models.IntegerField()
    peer = models.ForeignKey(Peer, null=True, on_delete=models.SET_NULL)

    # E.g. HeadersDirectFetchBlocks
    method = models.CharField(max_length=256)

    def __repr__(self):
        return _repr(self, ['host', 'timestamp', 'blockhash'])

    __str__ = __repr__


class BlockDisconnectedEvent(BaseModel):
    """
    2022-10-22T14:22:49.357774Z [msghand] [validationinterface.cpp:239] [BlockDisconnected] [validation] Enqueui
ng BlockDisconnected: block hash=3cfd126d960a9b87823fd94d48121f774aac448c9a6f1b48efc547c61f9b8c1f block height=1
    """
    created_at = models.DateTimeField(auto_now_add=True, blank=True)
    host = models.CharField(max_length=200)
    timestamp = models.DateTimeField()
    blockhash = models.CharField(max_length=80)
    height = models.IntegerField()

    @property
    def event_type(self) -> str:
        return 'block'

    def __repr__(self):
        return _repr(self, ['host', 'timestamp', 'blockhash', 'height'])

    __str__ = __repr__


class BlockConnectedEvent(BaseModel):
    """
    [operator()] [validation] BlockConnected: block hash=1397a170ca910a5689af809abf4cb25070c36e7bc023e2a23064652543b7f5eb block height=1
    """
    host = models.CharField(max_length=200)
    timestamp = models.DateTimeField()
    blockhash = models.CharField(max_length=80)
    height = models.IntegerField()

    @property
    def event_type(self) -> str:
        return 'block'

    def __repr__(self):
        return _repr(self, ['host', 'timestamp', 'blockhash', 'height'])

    __str__ = __repr__


class ReorgEvent(BaseModel):
    """
    A series of BlockDisconnected events capped off by a ConnectBlockEvent.
    """
    finished_timestamp = models.DateTimeField()
    host = models.CharField(max_length=200)
    min_height = models.IntegerField()
    max_height = models.IntegerField()
    old_blockhashes = models.JSONField()
    new_blockhashes = models.JSONField()

    @property
    def event_type(self) -> str:
        return 'block'

    def __repr__(self):
        return _repr(
            self, ['min_height', 'max_height', 'old_blockhashes', 'new_blockhashes'])

    __str__ = __repr__


class ConnectBlockEvent(BaseModel):
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

    @property
    def event_type(self) -> str:
        return 'block'

    def __repr__(self):
        return _repr(self, ['host', 'timestamp', 'height', 'blockhash'])

    __str__ = __repr__


class ConnectBlockDetails(BaseModel):
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

    @property
    def event_type(self) -> str:
        return 'block'

    def __repr__(self):
        return _repr(self, ['host', 'timestamp', 'height', 'blockhash'])

    __str__ = __repr__


mempool_event_type_enum = {
    'type': 'enum', 'name': 'event_type', 'symbols': [
        'mempool_accept',
    ],
}

# Arvo schema for mempool activity
mempool_activity_avro_schema = fastavro.parse_schema({
    'doc': 'Bitcoind mempool activity',
    'name': 'Mempool',
    'type': 'record',
    'fields': [
        {'name': 'event_type', 'type': mempool_event_type_enum},
        {'name': 'host', 'type': 'string'},
        {'name': 'timestamp', 'type': 'string'},
        {'name': 'txhash', 'type': 'string'},
        {'name': 'peer_num', 'type': ['null', 'int']},
        {'name': 'pool_size_txns', 'type': ['null', 'int']},
        {'name': 'pool_size_kb', 'type': ['null', 'int']},
    ],
})


class MempoolAccept(models.Model):
    """
    TODO: This isn't actually persisted to the database, so we should probably
    find another way of defining this data.

    A transaction is accepted to the mempool.

    2022-10-17T17:57:43.861480Z AcceptToMemoryPool: peer=11: accepted fa4f08dfe610593b505ca5cd8b2ba061ea15a4c480a63dd75b00e2eaddf9b42b (poolsz 11848 txn, 25560 kB)
    """
    host = models.CharField(max_length=200)
    timestamp = models.DateTimeField()
    txhash = models.CharField(max_length=80)
    peer_num = models.IntegerField()
    pool_size_txns = models.IntegerField()
    pool_size_kb = models.IntegerField()

    def avro_record(self):
        return {
            'event_type': 'mempool_accept',
            'host': self.host,
            'timestamp': self.timestamp.isoformat(),
            'txhash': self.txhash,
            'pool_size_txns': self.pool_size_txns,
            'pool_size_kb': self.pool_size_kb,
            'peer_num': self.peer_num,
        }

    @property
    def event_type(self) -> str:
        return 'mempool'


class ProcessLineError(models.Model):
    """
    Created when a listener fails to process a line.
    """
    host = models.CharField(max_length=200)
    timestamp = models.DateTimeField(auto_now_add=True, blank=True)
    listener = models.CharField(max_length=240)
    line = models.CharField(max_length=2048)

    def __repr__(self):
        return _repr(self, ['host', 'timestamp', 'line', 'listener'])

    __str__ = __repr__
