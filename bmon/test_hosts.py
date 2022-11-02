import pytest

from bmon import hosts, mempool


@pytest.mark.django_db
def test_policy_cohorts(fake_hosts):
    host_to_cohort = hosts.get_bitcoind_hosts_to_policy_cohort()

    assert {h.name: v for h, v in host_to_cohort.items()} == {
        'bitcoind': mempool.PolicyCohort.segwit,
        'bitcoind-02': mempool.PolicyCohort.taproot,
    }
