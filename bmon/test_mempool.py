from datetime import timedelta
from django.utils import timezone

import pytest

from bmon import mempool, server_tasks, server_monitor


def test_mempool_accept_processing():
    redis = server_tasks.redisdb
    hosts = {
        "a": mempool.PolicyCohort.segwit,
        "b": mempool.PolicyCohort.segwit,
        "c": mempool.PolicyCohort.taproot,
        "d": mempool.PolicyCohort.taproot,
        "e": mempool.PolicyCohort.taproot,
    }

    agg = mempool.MempoolAcceptAggregator(redis, hosts)

    assert agg.get_total_txids_processed() == 0
    assert agg.get_total_txids_processed_per_host() == {}

    now = timezone.now()
    now_ts = now.timestamp()

    most_hosts = ("a", "b", "c", "d")
    for host in most_hosts:
        retval = agg.mark_seen(host, "txid1", now)

        if host == "b":
            assert retval is mempool.PropagationStatus.CompleteCohort
        else:
            assert retval is None

    for host in most_hosts:
        assert redis.get("mpa:txid1:%s" % host)
        assert redis.get("mpa:total_txids:%s" % host) == "1"

    assert not redis.get("mpa:txid1:e")
    assert not redis.get("mpa:total_txids:e")

    assert agg.get_total_txids_processed() == 1
    assert agg.get_total_txids_processed_per_host() == {h: 1 for h in most_hosts}

    assert agg.mark_seen("e", "txid2", now) is None

    assert agg.get_total_txids_processed() == 2
    assert agg.get_total_txids_processed_per_host() == {h: 1 for h in hosts.keys()}

    assert (
        agg.mark_seen("e", "txid1", now + timedelta(seconds=1))
        == mempool.PropagationStatus.CompleteAll
    )

    new_counts = {h: 1 for h in hosts.keys()}
    new_counts["e"] = 2
    assert agg.get_total_txids_processed_per_host() == new_counts

    print("All specific txid keys should have a TTL")
    num_checked = 0

    for key in mempool.full_scan(redis, "mpa:txid*"):
        assert redis.ttl(key)
        num_checked += 1

    assert num_checked >= len(hosts)

    processed_events = []

    # Nothing's ready yet.
    assert len(agg.process_all_aged(processed_events.append)) == 0
    processed = agg.process_all_aged(processed_events.append, start_score=now_ts)
    assert len(processed) == 0

    processed = agg.process_all_aged(processed_events.append, start_score=(now_ts + 1))
    assert len(processed) == 2

    [txprop1, txprop2] = processed

    assert txprop1.host_to_timestamp == dict(
        **{h: now_ts for h in most_hosts}, **{"e": now_ts + 1}
    )
    assert set(txprop1.cohorts_complete) == {
        mempool.PolicyCohort.segwit,
        mempool.PolicyCohort.taproot,
    }
    assert txprop1.all_complete
    assert txprop1.spread == 1
    assert txprop1.time_window > 0
    assert txprop1.earliest_saw == now_ts
    assert txprop1.latest_saw == now_ts + 1

    assert txprop2.host_to_timestamp == {"e": now.timestamp()}
    assert txprop2.cohorts_complete == []
    assert not txprop2.all_complete
    assert txprop2.spread == 0
    assert txprop2.time_window > 0

    for host in most_hosts:
        assert redis.get("mpa:total_txids:%s" % host) == "1"

    assert redis.get("mpa:total_txids:e") == "2"

    all_processed = agg.get_propagation_event_keys()
    assert all_processed == ["mpa:prop_event:txid1", "mpa:prop_event:txid2"]

    [prop1, prop2] = [
        mempool.TxPropagation.from_redis(d)
        for d in sorted(redis.mget(all_processed))
    ]

    assert prop1 == txprop1
    assert prop2 == txprop2
    assert set(agg.get_propagation_events()) == {prop1, prop2}

    for key in all_processed:
        assert int(redis.ttl(key)) <= 60 * 60

    assert set(redis.keys()) == {
        'mpa:total_txids:a',
        'mpa:total_txids:b',
        'mpa:total_txids:c',
        'mpa:total_txids:d',
        'mpa:total_txids:e',
        'mpa:total_txids',
        'mpa:prop_event:txid2',
        'mpa:prop_event:txid1',
    }

    print("Smoke-test metric generation")
    server_monitor.refresh_metrics(agg)


@pytest.mark.django_db
def test_get_aggreator(fake_hosts):
    assert server_tasks.get_mempool_aggregator() == server_tasks.get_mempool_aggregator()
