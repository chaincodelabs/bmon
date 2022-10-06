import typing as t
import logging
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

from .rpc import BitcoinRpc
from bmon_infra.infra import Host, get_bitcoind_hosts

from django.conf import settings


log = logging.getLogger(__name__)


@lru_cache
def get_rpc(hosts: t.Tuple[Host]) -> t.Dict[str, BitcoinRpc]:
    rpcs = {}
    for host in hosts:
        # TODO: assumes that all hosts use same ports, credentials
        url = (
            f"http://{settings.BITCOIN_RPC_USER}:"
            f"{settings.BITCOIN_RPC_PASSWORD}@{host.bmon_ip}"
            f":{settings.BITCOIN_RPC_PORT}"
        )
        rpcs[host.name] = BitcoinRpc(url)
    return rpcs


RPC_ERROR_RESULT = object()


def run_rpc(rpc_call_func: t.Callable) -> t.Dict[str, t.Any]:
    rpcmap = get_rpc(get_bitcoind_hosts())
    promises = {}
    results = {}

    with ThreadPoolExecutor(max_workers=10) as e:
        for hostname, rpc in rpcmap.items():
            promises[hostname] = e.submit(rpc_call_func, rpc)

        for hostname, promise in promises.items():
            try:
                results[hostname] = promise.result()
            except Exception as e:
                log.exception(
                    "host %r encountered an error running %s: %s",
                    hostname,
                    rpc_call_func,
                    e,
                )
                results[hostname] = RPC_ERROR_RESULT

    return results
