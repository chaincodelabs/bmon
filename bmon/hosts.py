from django.conf import settings

import bmon_infra as infra
from . import models, mempool, bitcoin


def get_bitcoind_hosts_to_policy_cohort() -> dict[models.Host, mempool.PolicyCohort]:
    hosts = infra.get_bitcoind_hosts()
    # TODO this is an O(n) query
    host_objs = list(
        filter(
            None,
            [
                models.Host.objects.filter(name=h.name).order_by("-id").first()
                for h in hosts
            ],
        )
    )
    if not settings.TESTING:
        assert len(host_objs) == len(hosts)
    return {
        h: mempool.PolicyCohort.segwit
        if bitcoin.is_pre_taproot(h.bitcoin_version)
        else mempool.PolicyCohort.taproot
        for h in host_objs
    }
