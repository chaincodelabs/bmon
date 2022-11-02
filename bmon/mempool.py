"""
Various routines for analyzing the mempool.
"""
import datetime
import logging
import json
import typing as t
from enum import Enum
from dataclasses import dataclass, field
from collections import defaultdict
from functools import cache, cached_property

from django.utils import timezone

from . import models
from .bitcoin import gather_rpc, RPC_ERROR_RESULT, bitcoind_version, is_pre_taproot
import redis


log = logging.getLogger(__name__)


HostName = str
Txid = str


class PolicyCohort(str, Enum):
    """
    Different deployed versions of bitcoind h
    """

    segwit = "segwit"
    taproot = "taproot"

    @classmethod
    def for_host(cls, host: models.Host) -> "PolicyCohort":
        vertuple = bitcoind_version(host.bitcoin_version)[0]
        return cls.segwit if is_pre_taproot(vertuple) else cls.taproot


class PropagationStatus(str, Enum):
    # All hosts have seen this txid.
    CompleteAll = "complete_all"

    # All hosts in the policy cohort have seen this txid.
    CompleteCohort = "complete_cohort"


@dataclass(frozen=True, eq=True)
class TxPropagation:
    """
    Various statistics around how a single tx propagated.
    """

    txid: str

    # Raw data of all observed tx receptions
    host_to_timestamp: dict[str, float]

    # Which complete policy cohorts saw this transaction?
    cohorts_complete: list[PolicyCohort]

    # Did all available hosts see this transaction?
    all_complete: bool

    # The length of the examination period
    time_window: float

    @cached_property
    def earliest_saw(self) -> float:
        return min(self.host_to_timestamp.values())

    @cached_property
    def latest_saw(self) -> float:
        return max(self.host_to_timestamp.values())

    @cached_property
    def spread(self) -> float:
        return self.latest_saw - self.earliest_saw

    def asdict(self):
        return self.__dict__

    @classmethod
    def from_redis(cls, s: str) -> "TxPropagation":
        return cls(**json.loads(s))

    def __hash__(self):
        # TODO this is a hack
        return hash(str(self.__dict__))


class MempoolAcceptAggregator:
    """
    Manages the deluge of mempoolaccept events that we can't really persist individually, since
    they're on the order of 250,000 events per day. So we use this to watch the streams
    and persist anything particularly interesting.

    In keys, "mpa" is short for "mempool accept"
    """

    KEY_LIFETIME_SECS = 3 * 60 * 60  # 3 hours
    RESULT_LIFETIME_SECS = 1 * 60 * 60  # 1 hour

    MEMP_ACCEPT_SORTED_KEY = "mpa:txids"
    MEMP_ACCEPT_TOTAL_SEEN_KEY = "mpa:total_txids"

    def __init__(self, redis: redis.Redis, host_to_cohort: dict[str, PolicyCohort]):
        self.redis = redis
        self.host_to_cohort = host_to_cohort

    def cohort(self, host: str) -> set[str]:
        return self.hosts_for_cohort(self.host_to_cohort[host])

    @cached_property
    def cohorts(self) -> set[PolicyCohort]:
        return set(self.host_to_cohort.values())

    def hosts_for_cohort(self, cohort: PolicyCohort) -> set[str]:
        return {
            h for h in self.host_to_cohort.keys() if self.host_to_cohort[h] == cohort
        }

    def txkey_scan(self, txid: str) -> str:
        return f"mpa:{txid}:*"

    def get_total_txids_processed(self) -> int:
        return int(self.redis.get(self.MEMP_ACCEPT_TOTAL_SEEN_KEY) or 0)

    def get_total_txids_processed_per_host(self) -> dict[str, int]:
        keys = full_scan(self.redis, "%s:*" % self.MEMP_ACCEPT_TOTAL_SEEN_KEY)
        vals = self.redis.mget(keys)
        kvs = {}

        for k, v in zip(keys, vals):
            try:
                assert v
                kvs[k.split(":")[-1]] = int(v)
            except Exception:
                log.error("got invalid value for %s", k)
                continue

        return kvs

    def mark_seen(
        self, host: str, txid: str, seen_at: datetime.datetime
    ) -> None | PropagationStatus:
        """
        Mark a txid as seen, optionally returning propagation statuses if it has
        been seen by some threshold of hosts.
        """
        # Maintain a sorted set of txids to times first seen; this allows us to quickly
        # index into txids that have been hanging around for awhile.
        assert seen_at.tzinfo == datetime.timezone.utc
        if (
            self.redis.zadd(
                self.MEMP_ACCEPT_SORTED_KEY, {txid: timezone.now().timestamp()}, nx=True
            )
            > 0
        ):
            self.redis.incr(self.MEMP_ACCEPT_TOTAL_SEEN_KEY)

        self.redis.incr(f"{self.MEMP_ACCEPT_TOTAL_SEEN_KEY}:{host}")
        self.redis.set(
            f"mpa:{txid}:{host}", seen_at.timestamp(), ex=self.KEY_LIFETIME_SECS
        )

        scan_res = full_scan(self.redis, self.txkey_scan(txid))
        hosts_seen = {row.split(":")[-1] for row in scan_res}

        if len(scan_res) == len(self.host_to_cohort):
            return PropagationStatus.CompleteAll
        elif (self.cohort(host) - hosts_seen) == set():
            return PropagationStatus.CompleteCohort

        return None

    def process_all_aged(
        self,
        process_event: t.Callable[[TxPropagation], None] | None = None,
        min_age: None | int | float = None,
        start_score: None | int | float = None,
    ) -> list[TxPropagation]:
        """
        After some period of waiting for nodes to see a given tx in their mempool,
        take account of who has seen what by calling `process_completed_propagations`.
        """
        now = timezone.now().timestamp()
        min_age = min_age if min_age is not None else self.KEY_LIFETIME_SECS
        assert min_age is not None
        start_score = start_score if start_score is not None else now - min_age
        old_enough_txids = self.redis.zrange(
            self.MEMP_ACCEPT_SORTED_KEY,
            "-inf",  # type: ignore
            start_score,  # type: ignore
            byscore=True,
        )

        return self.process_completed_propagations(old_enough_txids, process_event)

    def process_completed_propagations(
        self,
        txids: list[str],
        process_event: t.Callable[[TxPropagation], None] | None = None,
    ) -> list[TxPropagation]:
        """
        Render separate redis keys into a single distince TxPropagation event.
        """
        now = timezone.now().timestamp()
        processed_events = []

        for txid in txids:
            keys = full_scan(self.redis, self.txkey_scan(txid))
            got = self.redis.mget(keys)
            host_to_timestamp: dict[str, float] = {}

            for tried, res in zip(keys, got):
                if not res:
                    log.error("got empty value for %s", tried)
                    continue
                try:
                    host = tried.split(":")[-1]
                except Exception:
                    log.exception("bad mempool accept key")
                    continue
                else:
                    host_to_timestamp[host] = float(res)

            all_hosts: set[str] = set(i for i in host_to_timestamp)
            cohorts_complete: list[PolicyCohort] = [
                c
                for c in self.cohorts
                if len(self.hosts_for_cohort(c) - all_hosts) == 0
            ]

            first_saw = self.redis.zscore(self.MEMP_ACCEPT_SORTED_KEY, txid)
            assert first_saw

            event = TxPropagation(
                txid,
                host_to_timestamp,
                cohorts_complete=cohorts_complete,
                all_complete=(len(all_hosts) == len(self.host_to_cohort)),
                time_window=(now - float(first_saw)),
            )

            try:
                if process_event:
                    process_event(event)
            except Exception:
                log.exception("failed to process event")
            else:
                processed_events.append(event)

            self.redis.set(
                "mpa:prop_event:%s" % txid,
                json.dumps(event.asdict()),
                ex=self.RESULT_LIFETIME_SECS,
            )
            self.redis.delete(*keys)
            self.redis.zrem(self.MEMP_ACCEPT_SORTED_KEY, txid)

        return processed_events

    def get_propagation_event_keys(self) -> list[str]:
        """
        Return all the propagation events over the last hour.

        (Last hour because of how we set TTLs based on `RESULT_LIFETIME_SECS`.)
        """
        return full_scan(self.redis, "mpa:prop_event:*")

    def get_propagation_events(self) -> t.Iterator[TxPropagation]:
        keys = self.get_propagation_event_keys()

        def chunks(lst, n):
            for i in range(0, len(lst), n):
                yield lst[i : (i + n)]

        for chunk in chunks(keys, 100):
            for key, event in zip(chunk, self.redis.mget(chunk)):
                try:
                    assert event
                    yield TxPropagation(**json.loads(event))
                except Exception:
                    log.exception(
                        "failed to deserialize TxPropagation from redis: %s: %s",
                        key,
                        event,
                    )
                    continue


def full_scan(redis, query) -> list[str]:
    cursor = None
    results = []

    while cursor != 0:
        cursor, res = redis.scan(cursor or 0, query)
        results.extend(res)

    return results


@dataclass
class CompareResult:
    # Txids which are only seen by one host.
    unique: dict[HostName, list[Txid]] = field(
        default_factory=lambda: defaultdict(list)
    )

    # Txids which are seen by all hosts but one.
    missing: dict[HostName, list[Txid]] = field(
        default_factory=lambda: defaultdict(list)
    )


def compare_mempools(
    host_to_pool: dict[str, list[str]] | None = None
) -> dict[str, dict]:
    host_to_pool = host_to_pool or gather_rpc("getrawmempool")
    host_to_set = {}

    for host, res in host_to_pool.items():
        if res == RPC_ERROR_RESULT:
            log.warning("unable to retrieve mempool for %s; skipping", host)
            continue
        host_to_set[host] = set(res)

    all_hosts = set(host_to_set.keys())
    num_hosts = len(host_to_set)
    over_half = (num_hosts // 2) + 1

    @cache
    def hosts_with_txid(txid: str) -> tuple[str, ...]:
        return tuple(h for h, pool in host_to_set.items() if txid in pool)

    all_tx = set()

    for pool in host_to_set.values():
        all_tx.update(pool)

    results: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))

    for tx in all_tx:
        hosts = hosts_with_txid(tx)

        if len(hosts) == 1:
            results["unique"][hosts[0]].append(tx)
        elif len(hosts) >= over_half:
            for host in all_hosts - set(hosts):
                results["missing"][host].append(tx)
        elif len(hosts) < over_half:
            for host in hosts:
                results["have_uncommon"][host].append(tx)

    def default_to_regular(d):
        if isinstance(d, defaultdict):
            d = {k: default_to_regular(v) for k, v in d.items()}
        return d

    return default_to_regular(results)
