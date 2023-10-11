import fastavro
from django.db import models
from django.conf import settings

import logging

log = logging.getLogger(__name__)


def _repr(instance, attrs):
    attr_strs = []
    for attr in attrs:
        attr_strs.append(f"{attr}='{getattr(instance, attr, '')}'")
    return f'{instance.__class__.__name__}({" ".join(attr_strs)})'


class BaseModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, blank=True)

    class Meta:
        abstract = True

    @property
    def is_high_volume(self):
        """Override this for models that can't be persisted normally in postgres, and
        have to be handled specially."""
        return False


class LogProgress(models.Model):
    """
    Records the latest hash of a log line responsible for generating an event on a
    given host.

    This is used to skip past already-processed log entries on startup.
    """

    hostname = models.CharField(max_length=200, unique=True)
    timestamp = models.DateTimeField()
    loghash = models.CharField(max_length=200)

    def __repr__(self):
        return _repr(self, ["host", "timestamp", "loghash"])

    __str__ = __repr__


class Host(BaseModel):
    name = models.CharField(max_length=256)
    cpu_info = models.CharField(max_length=1024)
    memory_bytes = models.FloatField()
    nproc = models.IntegerField(help_text="The number of processors")
    region = models.CharField(max_length=256, blank=True, null=True)

    bitcoin_version = models.CharField(
        max_length=256, help_text="As reported by bitcoind -version"
    )
    bitcoin_gitref = models.CharField(max_length=256, null=True, blank=True)
    bitcoin_gitsha = models.CharField(max_length=256, null=True, blank=True)
    bitcoin_dbcache = models.IntegerField()
    bitcoin_prune = models.IntegerField()
    bitcoin_listen = models.BooleanField(default=False)
    bitcoin_extra = models.JSONField(
        help_text="Extra data about this bitcoind instance"
    )

    disabled = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "name",
                    "cpu_info",
                    "memory_bytes",
                    "nproc",
                    "bitcoin_version",
                    "bitcoin_gitref",
                    "bitcoin_gitsha",
                    "bitcoin_dbcache",
                    "bitcoin_prune",
                    "bitcoin_extra",
                    "bitcoin_listen",
                ],
                name="unique_host",
            ),
        ]

        indexes = [
            models.Index(fields=['name']),
        ]

    def __repr__(self):
        return _repr(self, ["name", "bitcoin_version", "bitcoin_gitref"])

    __str__ = __repr__


PEER_UNIQUE_TOGETHER_FIELDS = (
    "host",
    "num",
    "addr",
    "connection_type",
    "inbound",
    "network",
    "services",
    "subver",
    "version",
    "relaytxes",
    "bip152_hb_from",
    "bip152_hb_to",
)


class Peer(BaseModel):
    host = models.ForeignKey(Host, on_delete=models.CASCADE)
    addr = models.CharField(max_length=256)
    connection_type = models.CharField(max_length=256)
    # Called "id" in getpeerinfo()
    num = models.IntegerField()
    inbound = models.BooleanField()
    network = models.CharField(max_length=256)
    services = models.CharField(max_length=256)
    subver = models.CharField(max_length=256)
    version = models.IntegerField()
    relaytxes = models.BooleanField()

    # Versions <= 0.19 lack this.
    bip152_hb_to = models.BooleanField(null=True, blank=True)
    bip152_hb_from = models.BooleanField(null=True, blank=True)
    servicesnames = models.JSONField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=PEER_UNIQUE_TOGETHER_FIELDS,
                name="unique_peer",
            ),
        ]

    @classmethod
    def peerinfo_data(cls, p: dict) -> tuple[dict, dict]:
        """Return the subset of getpeerinfo data that is relevant to this model."""
        out = {k: p.get(k) for k in PEER_UNIQUE_TOGETHER_FIELDS if k in p}
        out["num"] = p["id"]
        out["host"] = (
            Host.objects.filter(name=settings.HOSTNAME).order_by("-id").first()
        )

        defaults = {}
        # Versions pre 0.19 don't have servicesnames.
        if "servicesnames" in p:
            defaults["servicesnames"] = p["servicesnames"]

        return out, defaults

    def __repr__(self):
        return _repr(self, ["host", "addr", "num", "subver"])

    __str__ = __repr__


class PeerStats(BaseModel):
    """
    Interesting aggregates periodically pulled from each bitcoind host.
    """
    host = models.ForeignKey(Host, on_delete=models.CASCADE)
    num_peers = models.IntegerField()

    ping_mean = models.FloatField()
    ping_min = models.FloatField()
    ping_max = models.FloatField()

    bytesrecv = models.FloatField()
    bytessent = models.FloatField()

    bytesrecv_per_msg = models.JSONField()
    bytessent_per_msg = models.JSONField()

    class Meta:
        indexes = [
            models.Index(fields=['created_at']),
        ]


class RequestBlockEvent(BaseModel):
    """
    node0 2022-10-22T14:22:49.356891Z [msghand] [net_processing.cpp:2653] [HeadersDirectFetchBlocks] [net] Requesting block 7c06da428d44f32c0a77f585a44181d3f71fcbc55b44133d60d6941fa9165b0d from  peer=0
    """

    host = models.ForeignKey(Host, on_delete=models.CASCADE)
    timestamp = models.DateTimeField()
    blockhash = models.CharField(max_length=80)

    # Sometimes we're told the height "(height)" e.g. sending getdata, other times not.
    height = models.IntegerField(null=True, blank=True)

    peer_num = models.IntegerField()
    peer = models.ForeignKey(Peer, null=True, on_delete=models.SET_NULL)

    # E.g. HeadersDirectFetchBlocks
    method = models.CharField(max_length=256)

    def __repr__(self):
        return _repr(self, ["host", "timestamp", "blockhash"])

    __str__ = __repr__


class BlockDisconnectedEvent(BaseModel):
    """
        2022-10-22T14:22:49.357774Z [msghand] [validationinterface.cpp:239] [BlockDisconnected] [validation] Enqueui
    ng BlockDisconnected: block hash=3cfd126d960a9b87823fd94d48121f774aac448c9a6f1b48efc547c61f9b8c1f block height=1
    """

    created_at = models.DateTimeField(auto_now_add=True, blank=True)
    host = models.ForeignKey(Host, on_delete=models.CASCADE)
    timestamp = models.DateTimeField()
    blockhash = models.CharField(max_length=80)
    height = models.IntegerField()

    def __repr__(self):
        return _repr(self, ["host", "timestamp", "blockhash", "height"])

    __str__ = __repr__


class BlockConnectedEvent(BaseModel):
    """
    [operator()] [validation] BlockConnected: block hash=1397a170ca910a5689af809abf4cb25070c36e7bc023e2a23064652543b7f5eb block height=1
    """

    host = models.ForeignKey(Host, on_delete=models.CASCADE)
    timestamp = models.DateTimeField()
    blockhash = models.CharField(max_length=80)
    height = models.IntegerField()

    def __repr__(self):
        return _repr(self, ["host", "timestamp", "blockhash", "height"])

    __str__ = __repr__


class ReorgEvent(BaseModel):
    """
    A series of BlockDisconnected events capped off by a ConnectBlockEvent.
    """

    finished_timestamp = models.DateTimeField()
    host = models.ForeignKey(Host, on_delete=models.CASCADE)
    min_height = models.IntegerField()
    max_height = models.IntegerField()
    old_blockhashes = models.JSONField()
    new_blockhashes = models.JSONField()

    def __repr__(self):
        return _repr(
            self, ["min_height", "max_height", "old_blockhashes", "new_blockhashes"]
        )

    __str__ = __repr__


class ConnectBlockEvent(BaseModel):
    """
    From UpdateTip

    Sample:

    2019-08-09T16:28:42Z UpdateTip: new best=00000000000000000001d80d14ee4400b6d9c851debe27e6777f3876edd4ad1e height=589349 version=0x20800000 log2_work=90.944215 tx=443429260 date='2019-08-09T16:27:43Z' progress=1.000000 cache=8.7MiB(64093txo) warning='44 of last 100 blocks have unexpected version'
    """

    host = models.ForeignKey(Host, on_delete=models.CASCADE)
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

    def __repr__(self):
        return _repr(self, ["host", "timestamp", "height", "blockhash"])

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

    host = models.ForeignKey(Host, on_delete=models.CASCADE)
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

    def __repr__(self):
        return _repr(self, ["host", "timestamp", "height", "blockhash"])

    __str__ = __repr__


mempool_event_type_enum = {
    "type": "enum",
    "name": "event_type",
    "symbols": [
        "mempool_accept",
    ],
}

# Arvo schema for mempool activity
mempool_activity_avro_schema = fastavro.parse_schema(
    {
        "doc": "Bitcoind mempool activity",
        "name": "Mempool",
        "type": "record",
        "fields": [
            {"name": "event_type", "type": mempool_event_type_enum},
            {"name": "host", "type": "string"},
            {"name": "timestamp", "type": {"type": "long", "logicalType": "timestamp-micros"}},
            {"name": "txhash", "type": "string"},
            {"name": "peer_num", "type": ["null", "int"]},
            {"name": "pool_size_txns", "type": ["null", "int"]},
            {"name": "pool_size_kb", "type": ["null", "int"]},
        ],
    }
)


class MempoolReject(BaseModel):
    """
    [msghand] 4b93cc953162c4d953918e60fe1b9f48aae82e049ace3c912479e0ff5c7218c3 from peer=6 was not accepted: txn-mempool-conflict
    """

    host = models.ForeignKey(Host, on_delete=models.CASCADE)
    timestamp = models.DateTimeField(db_index=True)
    txhash = models.CharField(max_length=80)
    wtxid = models.CharField(max_length=80, null=True, blank=True)
    peer_num = models.IntegerField()
    peer = models.ForeignKey(Peer, on_delete=models.CASCADE)
    reason_code = models.CharField(
        max_length=256,
        help_text="A code indicating the rejection reason",
        default="",
    )
    reason = models.CharField(max_length=1024, help_text="The full reason string")
    reason_data = models.JSONField(
        default=dict, blank=True, help_text="Extra data associated with the reason"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["host", "timestamp", "txhash", "peer_num"],
                name="unique_reject",
            ),
        ]

    def __repr__(self):
        try:
            return _repr(self, ["host", "timestamp", "txhash", "reason_code", "peer_num"])
        except Exception:
            log.exception("mempoolreject repr")
            return _repr(self, ["timestamp", "txhash", "reason_code", "peer_num"])

    __str__ = __repr__

    @classmethod
    def get_reason_reject_code(cls, reason: str) -> str:
        reason_code = reason.split()[0].strip(",")

        if reason.startswith("insufficient fee"):
            if " new feerate " in reason:
                reason_code = "insufficient-feerate"
            elif "not enough additional fees" in reason:
                reason_code = "insufficient-fee"

        return reason_code


class BlockDownloadTimeout(BaseModel):
    """
    Timeout downloading block 000000000000000000086779ecf494b0595a9b779f501c7e25fb2be0b69907a2 from peer=24, disconnecting
    """

    host = models.ForeignKey(Host, on_delete=models.CASCADE)
    timestamp = models.DateTimeField(db_index=True)
    blockhash = models.CharField(max_length=80)
    peer_num = models.IntegerField()
    peer = models.ForeignKey(Peer, on_delete=models.CASCADE)

    def __repr__(self):
        return _repr(self, ["host", "timestamp", "blockhash", "peer_num"])

    __str__ = __repr__


class HeaderToTipEvent(BaseModel):
    """
    Measures times between seeing a new block header and adding it as our tip.
    """
    host = models.ForeignKey(Host, on_delete=models.CASCADE)
    blockhash = models.CharField(max_length=80)
    height = models.IntegerField()
    saw_header_at = models.DateTimeField(
        help_text="When we first saw the Saw new header message")
    reconstruct_block_at = models.DateTimeField(
        help_text="When the (compact)block was reconstructed")
    tip_at = models.DateTimeField(
        help_text="When the block became tip")

    header_to_tip_secs = models.FloatField(
        help_text="Time between header seen and new tip appended")
    header_to_block_secs = models.FloatField(
        help_text="Time between header seen and full block obtained")
    block_to_tip_secs = models.FloatField(
        help_text="Time between full block obtained and tip updated")
    blocktime_minus_header_secs = models.FloatField(
        help_text="Difference between blocktime and header seen")

    reconstruction_data = models.JSONField(
        default=dict, blank=True,
        help_text="Extra data associated with the block reconstruction")

    def __repr__(self):
        return _repr(self, ["host", "blockhash", "saw_header_at", "header_to_tip_secs"])

    __str__ = __repr__


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
            "event_type": "mempool_accept",
            "host": self.host,
            "timestamp": self.timestamp,
            "txhash": self.txhash,
            "pool_size_txns": self.pool_size_txns,
            "pool_size_kb": self.pool_size_kb,
            "peer_num": self.peer_num,
        }

    @property
    def is_high_volume(self):
        return True


class ProcessLineError(models.Model):
    """
    Created when a listener fails to process a line.
    """

    hostname = models.CharField(max_length=200)
    timestamp = models.DateTimeField(auto_now_add=True, blank=True)
    listener = models.CharField(max_length=240)
    line = models.CharField(max_length=2048)

    def __repr__(self):
        return _repr(self, ["hostname", "timestamp", "line", "listener"])

    __str__ = __repr__
