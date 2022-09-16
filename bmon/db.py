import datetime
import enum
import uuid
import functools
from contextlib import contextmanager

from sqlalchemy import (
    create_engine,
    Column, Integer, String, DateTime, ForeignKey, Boolean, Enum, Float)
from sqlalchemy.orm import relationship, sessionmaker
from sqlalchemy.dialects.postgresql import UUID, BIT
from sqlalchemy.ext.declarative import declared_attr, as_declarative

from . import config


@as_declarative()
class Base:
    def as_dict(self):
        d = self.__dict__
        if '_sa_instance_state' in d:
            d.pop('_sa_instance_state')
        return d


class IdMixin:
    id = Column(
        UUID(as_uuid=True), primary_key=True, default=lambda: uuid.uuid4().hex)
    created_at = Column(
        DateTime, default=datetime.datetime.utcnow, nullable=False)


class IntIdMixin:
    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(
        DateTime, default=datetime.datetime.utcnow, nullable=False)


class InvType(enum.Enum):
    block = 0
    tx = 1


class Host(Base, IntIdMixin):
    """
    Their UUID is used as their authorization token.
    """
    __tablename__ = 'hosts'

    bitcoin_version = Column(String(64), nullable=False)
    hostname = Column(String(128), nullable=False)
    auth_token = Column(
        UUID(as_uuid=True), nullable=False, default=lambda: uuid.uuid4().hex)


class Peer(Base, IdMixin):
    __tablename__ = 'peers'

    ip_addr = Column(String(64), nullable=False)
    version = Column(Integer, nullable=False)
    subver = Column(String(64), nullable=False)
    services = Column(BIT(64), nullable=False)
    # service bits


class HostPeer(Base, IdMixin):
    __tablename__ = 'host_peers'

    peer_id = Column(UUID(as_uuid=True), ForeignKey('peers.id'))
    peer = relationship('Peer')
    host_id = Column(Integer, ForeignKey('hosts.id'))
    host = relationship('Host')
    inbound = Column(Boolean, nullable=False)


class GotInvEvent(Base, IdMixin):
    __tablename__ = 'got_inv_events'

    # 2019-08-08T21:18:33Z [msghand] got inv: tx 9499035d6ee50342377a3e3b12827aec8a9fd0a48f71a7459ab11434f7d2d530  new peer=4349
    host_id = Column(Integer, ForeignKey('hosts.id'))
    host = relationship('Host')
    peer_id = Column(UUID(as_uuid=True), ForeignKey('peers.id'))
    peer = relationship('Peer')
    obj_type = Column(Integer, nullable=False)
    obj_id = Column(Enum(InvType), nullable=False)


class GotBlockEvent(Base, IdMixin):
    __tablename__ = 'got_block_events'

    host_id = Column(Integer, ForeignKey('hosts.id'), nullable=False)
    host = relationship("Host")
    peer_id = Column(UUID(as_uuid=True), ForeignKey('peers.id'))
    peer = relationship("Peer")

    blockhash = Column(String(64), nullable=False)

    # May be null if node is using high-bandwidth compactblocks and receives
    # a cmpctblock message directly.
    got_headers_at = Column(DateTime, nullable=True)

    # May be null if node isn't using cmpctblocks.
    got_cmpctblock_at = Column(DateTime, nullable=True)

    # This is either when we received the whole block from our peer or
    # completed reconstruction of the cmpctblock.
    got_block_at = Column(DateTime)

    # Additional data specific to compactblock reception.
    cb_num_tx_prefilled = Column(Integer, nullable=True)
    cb_num_tx_requested = Column(Integer, nullable=True)


class GotTxEvent(Base, IdMixin):
    __tablename__ = 'got_tx_events'

    host_id = Column(Integer, ForeignKey('hosts.id'), nullable=False)
    host = relationship("Host")
    peer_id = Column(UUID(as_uuid=True), ForeignKey('peers.id'))
    peer = relationship("Peer")

    txid = Column(String(64), nullable=False)
    # 2019-08-08T21:18:34Z [msghand] AcceptToMemoryPool: peer=4349: accepted 9499035d6ee50342377a3e3b12827aec8a9fd0a48f71a7459ab11434f7d2d530 (poolsz 8704 txn, 18511 kB)
    atmp_at = Column(DateTime, nullable=True)


class ConnectBlockEvent(Base, IdMixin):
    __tablename__ = 'connect_block_events'

    host_id = Column(Integer, ForeignKey('hosts.id'))
    host = relationship("Host")
    blockhash = Column(String(64), nullable=False)
    height = Column(Integer, nullable=False)

    tx_count = Column(Integer, nullable=False)
    txin_count = Column(Integer, nullable=False)
    # i.e. nChainTx
    total_tx_count = Column(Integer, nullable=False)

    # ConnectBlock measurements
    #
    # 2019-07-29T18:34:17Z   - Load block from disk: 0.00ms [23.45s]
    # 2019-07-29T18:34:17Z     - Sanity checks: 0.01ms [17.24s (18.07ms/blk)]
    # 2019-07-29T18:34:17Z     - Fork checks: 0.04ms [0.09s (0.10ms/blk)]
    # 2019-07-29T18:34:17Z       - Connect 1982 transactions: 41.16ms (0.021ms/tx, 0.008ms/txin) [154.90s (162.37ms/blk)]
    # 2019-07-29T18:34:17Z     - Verify 4917 txins: 41.23ms (0.008ms/txin) [177.91s (186.49ms/blk)]
    # 2019-07-29T18:34:17Z     - Index writing: 13.62ms [13.08s (13.71ms/blk)]
    # 2019-07-29T18:34:17Z     - Callbacks: 0.04ms [0.05s (0.05ms/blk)]
    # 2019-07-29T18:34:17Z   - Connect total: 55.33ms [208.93s (219.00ms/blk)]
    # 2019-07-29T18:34:17Z   - Flush: 10.58ms [104.30s (109.33ms/blk)]
    # 2019-07-29T18:34:17Z   - Writing chainstate: 0.09ms [0.10s (0.10ms/blk)]
    # 2019-07-29T18:34:17Z   - Connect postprocess: 70.64ms [8.14s (8.53ms/blk)]
    # 2019-07-29T18:34:17Z - Connect block: 136.63ms [344.92s (361.55ms/blk)]
    load_block_from_disk_time_ms = Column(Float)
    sanity_checks_time_ms = Column(Float)
    fork_checks_time_ms = Column(Float)
    connect_txs_time_ms = Column(Float)
    verify_time_ms = Column(Float)
    index_writing_time_ms = Column(Float)
    callbacks_time_ms = Column(Float)
    connect_total_time_ms = Column(Float)
    flush_coins_time_ms = Column(Float)
    flush_chainstate_time_ms = Column(Float)
    connect_postprocess_time_ms = Column(Float)
    connectblock_total_time_ms = Column(Float)

    # From UpdateTip
    #
    # 2019-08-09T16:28:42Z UpdateTip: new best=00000000000000000001d80d14ee4400b6d9c851debe27e6777f3876edd4ad1e height=589349 version=0x20800000 log2_work=90.944215 tx=443429260 date='2019-08-09T16:27:43Z' progress=1.000000 cache=8.7MiB(64093txo) warning='44 of last 100 blocks have unexpected version'
    version = Column(String, nullable=False)
    date = Column(DateTime, nullable=False)
    cachesize_mib = Column(Float, nullable=False)
    cachesize_txo = Column(Integer, nullable=False)
    warning = Column(String)


class DisconnectBlockEvent(Base, IdMixin):
    __tablename__ = 'disconnect_block_events'

    host_id = Column(Integer, ForeignKey('hosts.id'))
    blockhash = Column(String(64), nullable=False)
    height = Column(Integer, nullable=False)


class MempoolStatus(Base, IdMixin):
    __tablename__ = 'mempool_statuses'

    host_id = Column(Integer, ForeignKey('hosts.id'))
    host = relationship("Host")
    tx_count = Column(Integer, nullable=False)
    # Total size of mempool in virtual bytes (witnesses discounted)
    size_vB = Column(Integer, nullable=False)
    # Total size of mempool in bytes
    size_B = Column(Integer, nullable=False)


class GotInvalidBlockEvent(Base, IdMixin):
    __tablename__ = 'got_invalid_block_events'


class GotInvalidTxEvent(Base, IdMixin):
    __tablename__ = 'got_invalid_tx_events'


class MempoolExpiryEvent(Base, IdMixin):
    __tablename__ = 'mempool_expiry_events'


"""
Grepping for "state.Invalid"


# Invalid tx

- No transaction inputs.
  state.Invalid(ValidationInvalidReason::CONSENSUS, false, REJECT_INVALID, "bad-txns-vin-empty");
  ERROR: ProcessNewBlock: AcceptBlock FAILED (bad-txns-vin-empty, Transaction check failed (tx hash {hash})  (code 16))

- No transaction outputs.
  state.Invalid(ValidationInvalidReason::CONSENSUS, false, REJECT_INVALID, "bad-txns-vout-empty");
  ERROR: ProcessNewBlock: AcceptBlock FAILED (bad-txns-vout-empty, Transaction check failed (tx hash {hash})  (code 16))

- The serialized transaction without witness is larger than the max block weight.
  state.Invalid(ValidationInvalidReason::CONSENSUS, false, REJECT_INVALID, "bad-txns-oversize");
  ERROR: ProcessNewBlock: AcceptBlock FAILED (bad-txns-vout-empty, Transaction check failed (tx hash {hash})  (code 16))

- An output's value is negative.
  state.Invalid(ValidationInvalidReason::CONSENSUS, false, REJECT_INVALID, "bad-txns-vout-negative");
  ERROR: ProcessNewBlock: AcceptBlock FAILED (bad-txns-vout-negative, Transaction check failed (tx hash {hash})

- An output's value is more than the maximum number of satoshis.
  state.Invalid(ValidationInvalidReason::CONSENSUS, false, REJECT_INVALID, "bad-txns-vout-toolarge");
  ERROR: ProcessNewBlock: AcceptBlock FAILED (bad-txns-vout-toolarge, Transaction check failed (tx hash {hash})

- Total value of outputs is negative or larger than the maximum number of satoshis.
  state.Invalid(ValidationInvalidReason::CONSENSUS, false, REJECT_INVALID, "bad-txns-txouttotal-toolarge");
  ERROR: ProcessNewBlock: AcceptBlock FAILED (bad-txns-txouttotal-toolarge, Transaction check failed (tx hash {hash})

- The transaction has duplicate inputs.
  state.Invalid(ValidationInvalidReason::CONSENSUS, false, REJECT_INVALID, "bad-txns-inputs-duplicate");
  ERROR: ProcessNewBlock: AcceptBlock FAILED (bad-txns-duplicate, duplicate transaction (code 16))

- The coinbase's scriptSig is less than 2 bytes or greater than 100 bytes.
  state.Invalid(ValidationInvalidReason::CONSENSUS, false, REJECT_INVALID, "bad-cb-length");
  ERROR: ProcessNewBlock: AcceptBlock FAILED (bad-cb-length, Transaction check failed (tx hash {hash})  (code 16))

- One of the transaction input's prevout values is null.
  state.Invalid(ValidationInvalidReason::CONSENSUS, false, REJECT_INVALID, "bad-txns-prevout-null");


- One of the transaction inputs' prevouts is not present in the UTXO set. I.e. it is spending a non-existent coin.
  state.Invalid(ValidationInvalidReason::TX_MISSING_INPUTS, false, REJECT_INVALID, "bad-txns-inputs-missingorspent",
            strprintf("%s: inputs missing/spent", __func__));
  ERROR: ConnectBlock: Consensus::CheckTxInputs: {tx_hash}, bad-txns-inputs-missingorspent, CheckTxInputs: inputs missing/spent (code 16)
  ERROR: ConnectTip: ConnectBlock {hash} failed, bad-txns-inputs-missingorspent, CheckTxInputs: inputs missing/spent (code 16)

- The transaction is attempting to spend a coinbase before it has matured.
  state.Invalid(ValidationInvalidReason::TX_PREMATURE_SPEND, false, REJECT_INVALID, "bad-txns-premature-spend-of-coinbase",
       strprintf("tried to spend coinbase at depth %d", nSpendHeight - coin.nHeight));
  ERROR: ConnectBlock: Consensus::CheckTxInputs: {tx_hash}, bad-txns-premature-spend-of-coinbase, tried to spend coinbase at depth {n} (code 16)
  ERROR: ConnectTip: ConnectBlock {block} failed, bad-txns-premature-spend-of-coinbase, tried to spend coinbase at depth {n} (code 16)

- The transaction has negative or overflow input values.
  state.Invalid(ValidationInvalidReason::CONSENSUS, false, REJECT_INVALID, "bad-txns-inputvalues-outofrange");

- The total value of the transaction inputs is less than the total value of the outputs.
  state.Invalid(ValidationInvalidReason::CONSENSUS, false, REJECT_INVALID, "bad-txns-in-belowout",
               strprintf("value in (%s) < value out (%s)", FormatMoney(nValueIn), FormatMoney(value_out)));
  ERROR: ConnectBlock: Consensus::CheckTxInputs: {tx_hash}, bad-txns-in-belowout, value in (50.00) < value out (51.00) (code 16)
  ERROR: ConnectTip: ConnectBlock {block_hash} failed, bad-txns-in-belowout, value in (50.00) < value out (51.00) (code 16)

- The transaction fee is negative or larger than the maximum number of satoshis.
  state.Invalid(ValidationInvalidReason::CONSENSUS, false, REJECT_INVALID, "bad-txns-fee-outofrange");


# Invalid block

ERROR: ProcessNewBlock: AcceptBlock FAILED (bad-txns-inputs-duplicate, Transaction check failed (tx hash {hash})
ERROR: ProcessNewBlock: AcceptBlock FAILED (bad-blk-sigops, out-of-bounds SigOpCount (code 16))
ERROR: ProcessNewBlock: AcceptBlock FAILED (bad-blk-length, size limits failed (code 16))
ERROR: ProcessNewBlock: AcceptBlock FAILED (bad-cb-length, Transaction check failed (tx hash b93ac36e40404b4cc818737df17e7520e8611702cb084fdd6a43eb2d163a0759)  (code 16))
ERROR: ConnectBlock: Consensus::CheckTxInputs: 49b89c98f0f43e10fc86d045a71f651a41ab15d27123d4e8147063cf0002784c, bad-txns-inputs-missingorspent, CheckTxInputs: inputs missing/spent (code 16)
ERROR: ConnectTip: ConnectBlock 29654861a28207bd3e5c638c35d0335148f2e0697c0d19747a6b72135d84cdd4 failed, bad-txns-in-belowout, value in (50.00) < value out (50.00000001) (code 16)
ERROR: ConnectBlock(): coinbase pays too much (actual=1326546691 vs limit=1250000000)
"""


class AlertType(str, enum.Enum):

    STALE_TIP = 'stale_tip'

    MEMPOOL_LOW = 'mempool_low'

    INVALID_BLOCK = 'invalid_block'

    DIFFERING_MEMPOOL = 'differing_mempool'

    REORG = 'reorg'


class Alert(Base, IdMixin):
    __tablename__ = 'alerts'

    type = Column(Enum(AlertType), nullable=False)


class HostAlert(Base, IdMixin):
    __tablename__ = 'host_alerts'

    host_id = Column(Integer, ForeignKey('hosts.id'))
    alert_id = Column(UUID(as_uuid=True), ForeignKey('alerts.id'))


# -----------------------------------------------------------------------------
#
# Data migrations
#
# -----------------------------------------------------------------------------


def init_migrate():
    Base.metadata.create_all(get_engine())

    hosts = (
        Host(
            bitcoin_version='master-deadbeef',
            hostname='localhost',
            auth_token=config.LOCALHOST_AUTH_TOKEN,
        ),
    )

    with session_manager() as session:
        for host in hosts:
            if not Host.query.filter_by(hostname=host.hostname):
                session.add(host)
                session.commit()
                print('Created host {} with auth_token {}'.format(
                    host.hostname, host.id))


# -----------------------------------------------------------------------------
#
# Utility functions
#
# -----------------------------------------------------------------------------

@functools.lru_cache()
def get_engine():
    return create_engine(config.DATABASE_URL)


@functools.lru_cache()
def get_session_factory():
    return sessionmaker(bind=get_engine())


def get_db_session():
    return get_session_factory()()


@contextmanager
def session_manager():
    session = get_db_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_class_by_tablename(tablename):
    """
    Return class reference mapped to table.

    From https://stackoverflow.com/a/23754464.

    :param tablename: String with name of table.
    :return: Class reference or None.
    """
    for c in Base._decl_class_registry.values():
        if hasattr(c, '__tablename__') and c.__tablename__ == tablename:
            return c
