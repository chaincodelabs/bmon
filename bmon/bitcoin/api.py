import time
import typing as t
import logging
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

from .rpc import BitcoinRpc
from bmon_infra.infra import Host, get_bitcoind_hosts  # type: ignore

from django.conf import settings


log = logging.getLogger(__name__)


@lru_cache
def get_rpc(host: None | str = None, boot_tries: int = 5, boot_delay_secs: int = 5) -> BitcoinRpc:
    """
    Return an RPC object to bitcoind.

    Will block until successfully connected.
    """
    host = host or settings.BITCOIN_RPC_HOST  # type: ignore
    url = (
        f"http://{settings.BITCOIN_RPC_USER}:"
        f"{settings.BITCOIN_RPC_PASSWORD}@{host}"
        f":{settings.BITCOIN_RPC_PORT}"
    )
    rpc = BitcoinRpc(url)

    if boot_tries == 0:
        return rpc

    while boot_tries > 0:
        boot_tries -= 1
        try:
            rpc.getblockchaininfo()['chain']
            return rpc
        except Exception as e:
            log.info("hit excption waiting for bitcoin rpc to boot: %s",
                     e.__class__.__name__)
            log.debug("bitcoin RPC exception", exc_info=e)
            time.sleep(boot_delay_secs)
            boot_delay_secs *= 2

    raise RuntimeError(f"couldn't boot RPC {url}")


@lru_cache
def get_rpc_for_hosts(hosts: t.Tuple[Host]) -> t.Dict[str, BitcoinRpc]:
    # TODO: assumes that all hosts use same ports, credentials
    return {host.name: get_rpc(host.bmon_ip) for host in hosts}


RPC_ERROR_RESULT = object()


def gather_rpc(rpc_call_arg: str | t.Callable[[BitcoinRpc], t.Any]) -> t.Dict[str, t.Any]:
    """
    Gather RPC resuls from all bitcoin hosts.

    Args:
        rpc_call_arg: either a string that represents the RPC call or a
            function that takes the RPC object as its only argument.
    """
    rpcmap = get_rpc_for_hosts(get_bitcoind_hosts())
    promises = {}
    results: dict[str, t.Any] = {}

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
