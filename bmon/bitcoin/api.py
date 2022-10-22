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


def gather_rpc(rpc_call_arg: str | t.Callable) -> t.Dict[str, t.Any]:
    """
    Gather RPC resuls from all bitcoin hosts.

    Args:
        rpc_call_arg: either a string that represents the RPC call or a
            function that takes the RPC object as its only argument.
    """
    rpcmap = get_rpc(get_bitcoind_hosts())
    promises = {}
    results = {}

    with ThreadPoolExecutor(max_workers=10) as e:
        for hostname, rpc in rpcmap.items():

            if isinstance(rpc_call_arg, str):
                promises[hostname] = e.submit(rpc.call, rpc_call_arg)
            else:
                promises[hostname] = e.submit(rpc_call_arg, rpc)

        for hostname, promise in promises.items():
            try:
                results[hostname] = promise.result()
            except Exception as e:
                log.exception(
                    "host %r encountered an error running %s: %s",
                    hostname,
                    rpc_call_arg,
                    e,
                )
                results[hostname] = RPC_ERROR_RESULT

    return results
