
import pytest


from . import conftest, bitcoind_tasks, server_tasks, mempool
from .hosts import get_bitcoind_hosts_to_policy_cohort


@pytest.mark.django_db
def test_process_mempool_accepts(fake_hosts):
    logdata = conftest.read_data_file("mempool-accepts-log.txt")

    for host in fake_hosts:
        for line in logdata:
            bitcoind_tasks.process_line(line, host)

    hosts_to_policy = {
        h.name: v for h, v in get_bitcoind_hosts_to_policy_cohort().items()}

    agg = mempool.MempoolAcceptAggregator(server_tasks.redisdb, hosts_to_policy)

    events = list(agg.get_propagation_events())
    assert len(events) == 50
    assert agg.get_total_txids_processed() == 50
    assert agg.get_total_txids_processed_per_host() == {
        'bitcoind': 50,
        'bitcoind-02': 50,
    }
