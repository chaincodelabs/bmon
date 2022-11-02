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

    @cache
    def cohort(self, host: str) -> set[str]:
        return self.hosts_for_cohort(self.host_to_cohort[host])

    @cached_property
    def cohorts(self) -> set[PolicyCohort]:
        return set(self.host_to_cohort.values())

    @cache
    def hosts_for_cohort(self, cohort: PolicyCohort) -> set[str]:
        return {
            h for h in self.host_to_cohort.keys() if self.host_to_cohort[h] == cohort
        }

    def get_total_txids_processed(self) -> int:
        return int(self.redis.get(self.MEMP_ACCEPT_TOTAL_SEEN_KEY) or 0)

    def get_total_txids_processed_per_host(self) -> dict[str, int]:
        keys = [f"{self.MEMP_ACCEPT_TOTAL_SEEN_KEY}:{h}" for h in self.host_to_cohort]
        vals = self.redis.mget(keys)
        kvs = {}

        for k, v in zip(keys, vals):
            try:
                assert v
                kvs[k.split(":")[-1]] = int(v)
            except Exception:
                log.error("missing total txids for host key %s", k)
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

        if host not in self.host_to_cohort:
            raise ValueError("host %s not known to mempool aggregator", host)

        if self.redis.get(f"mpa:{txid}:{host}"):
            log.error("duplicate MempoolAccept event detected: %s", txid)
            return None

        if (
            self.redis.zadd(
                self.MEMP_ACCEPT_SORTED_KEY, {txid: timezone.now().timestamp()}, nx=True
            )
            > 0
        ):
            self.redis.incr(self.MEMP_ACCEPT_TOTAL_SEEN_KEY)

        self.redis.incr(f"{self.MEMP_ACCEPT_TOTAL_SEEN_KEY}:{host}")

        ts_key = f"mpa:{txid}:{host}"
        ts_set = False
        tries = 3
        while tries > 0 and not ts_set:
            ts_set = self.redis.set(
                ts_key, seen_at.timestamp(), ex=self.KEY_LIFETIME_SECS
            )

            if not ts_set:
                log.error("failed to set key for %s:%s", txid, host)
            tries -= 1

        if not ts_set:
            raise ValueError(f"couldn't set key for {txid}:{host}", txid, host)

        assert len(self.host_to_cohort) > 0

        scan_for = [f"mpa:{txid}:{host}" for host in self.host_to_cohort]
        hosts_seen = set()

        for key, res in zip(scan_for, self.redis.mget(scan_for)):
            if res is not None:
                hosts_seen.add(key.split(":")[-1])

        if hosts_seen == self.host_names:
            return PropagationStatus.CompleteAll
        elif (self.cohort(host) - hosts_seen) == set():
            return PropagationStatus.CompleteCohort

        if not self.redis.get(ts_key):
            log.error("redis key disappeared %s", ts_key)

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
        OBSERVATION_WINDOW_SECS = 60 * 60
        now = timezone.now().timestamp()
        start_score = (
            start_score
            if start_score is not None
            else now - (min_age if min_age is not None else OBSERVATION_WINDOW_SECS)
        )
        old_enough_txids = self.redis.zrange(
            self.MEMP_ACCEPT_SORTED_KEY,
            "-inf",  # type: ignore
            start_score,  # type: ignore
            byscore=True,
        )
        log.info(
            "sending %d txids to be processed for prop. completion",
            len(old_enough_txids),
        )

        return self.process_completed_propagations(old_enough_txids, process_event)

    @cached_property
    def host_names(self) -> set[str]:
        return set(self.host_to_cohort.keys())

    def process_completed_propagations(
        self,
        txids: list[str],
        process_event: t.Callable[[TxPropagation], None] | None = None,
        assert_complete: bool = False,
    ) -> list[TxPropagation]:
        """
        Render separate redis keys into a single distince TxPropagation event.
        """
        now = timezone.now().timestamp()
        processed_events = []
        to_remove = []

        log.info("processing %d tx propagation completions", len(txids))
        if assert_complete:
            log.info("assuming %s has been seen be all nodes", txids)

        for txid in txids:
            log.info("processing complete propagation for txid %s", txid)
            keys = [f"mpa:{txid}:{host}" for host in self.host_to_cohort]
            got = self.redis.mget(keys)
            host_to_timestamp: dict[str, float] = {}
            hosts_that_saw = set()

            for tried, res in zip(keys, got):
                if not res:
                    # Expected that we may be missing some hosts.
                    continue
                host = tried.split(":")[-1]

                if host not in self.host_to_cohort:
                    log.error("unknown host %s for txid %s", host, txid)
                    continue

                host_to_timestamp[host] = float(res)
                hosts_that_saw.add(host)

            if not host_to_timestamp:
                log.error("no timestamp entries found for %s", txid)
                to_remove.append(txid)
                continue

            cohorts_complete: list[PolicyCohort] = [
                c
                for c in self.cohorts
                if len(self.hosts_for_cohort(c) - hosts_that_saw) == 0
            ]

            all_complete = hosts_that_saw == self.host_names
            if assert_complete:
                if not all_complete:
                    log.error("expected to have all host timestamps for txid %s", txid)
                    continue

            first_saw = self.redis.zscore(self.MEMP_ACCEPT_SORTED_KEY, txid)
            if not first_saw:
                log.error(
                    "missing score for %s in %s", txid, self.MEMP_ACCEPT_SORTED_KEY
                )
                continue

            event = TxPropagation(
                txid,
                host_to_timestamp,
                cohorts_complete=cohorts_complete,
                all_complete=all_complete,
                time_window=(now - float(first_saw)),
            )

            try:
                if process_event:
                    process_event(event)
            except Exception:
                log.exception("failed to process event")
            else:
                processed_events.append(event)

            prop_event_key = "mpa:prop_event:%s" % txid
            self.redis.set(
                prop_event_key,
                json.dumps(event.asdict()),
                ex=self.RESULT_LIFETIME_SECS,
            )

            # Add to the indexing set (to avoid full scans for tx prop events.
            if (
                self.redis.zadd(
                    "mpa:prop_event_set",
                    {prop_event_key: timezone.now().timestamp()},
                    nx=True,
                )
                <= 0
            ):
                log.error("duplicate tx propagation event attempt: %s", txid)

            self.redis.delete(*keys)
            to_remove.append(txid)

        if to_remove:
            self.redis.zrem(self.MEMP_ACCEPT_SORTED_KEY, *to_remove)

        return processed_events

    def get_propagation_event_keys(self) -> list[str]:
        """
        Return all the propagation events over the last hour.

        (Last hour because of how we set TTLs based on `RESULT_LIFETIME_SECS`.)
        """
        cursor = None
        keys = []

        while cursor != 0:
            cursor, res = self.redis.zscan("mpa:prop_event_set", cursor or 0)
            # omit scores
            keys.extend([i[0] for i in res])

        return keys

    def get_propagation_events(self) -> t.Iterator[TxPropagation]:
        now = timezone.now().timestamp()
        hour_ago = now - (60 * 60)
        removed = self.redis.zremrangebyscore("mpa:prop_event_set", "-inf", hour_ago)
        keys = self.get_propagation_event_keys()

        if removed > 0:
            log.info("removed %s old tx propagation events", removed)

        def chunks(lst, n):
            for i in range(0, len(lst), n):
                yield lst[i : (i + n)]

        for chunk in chunks(keys, 100):
            for key, event in zip(chunk, self.redis.mget(chunk)):
                try:
                    assert event
                    loaded = json.loads(event)
                    txprop = TxPropagation(**loaded)
                except Exception:
                    log.exception(
                        "failed to deserialize TxPropagation from redis: %s: %s",
                        key,
                        event,
                    )
                    continue

                if not txprop.host_to_timestamp:
                    log.error("txprop without timestamp data", extra={"txprop": txprop})
                    continue
                else:
                    yield txprop


def full_scan(redis: redis.Redis, query: str) -> list[str]:
    cursor = None
    results = []

    while cursor != 0:
        cursor, res = redis.scan(cursor or 0, match=query)
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
