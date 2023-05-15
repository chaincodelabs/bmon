import time
import typing as t
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from functools import cache
from pathlib import Path

from .rpc import BitcoinRpc
import bmon_infra as infra

from django.conf import settings


log = logging.getLogger(__name__)


@cache
def read_raw_bitcoind_version() -> str:
    assert settings.BITCOIND_VERSION_PATH
    return Path(settings.BITCOIND_VERSION_PATH).read_text().strip()


@cache
def bitcoind_version(ver: None | str = None) -> tuple[tuple[int, ...], None | str]:
    """Returns the version tuple and the git sha, if any."""
    ver = ver or read_raw_bitcoind_version()
    ver = ver.strip('v')
    gitsha = None

    if '-' in ver:
        ver, gitsha = ver.split('-', 1)

    vertuple = tuple(int(i.split('rc')[0]) for i in ver.split('.'))
    if len(vertuple) == 2:
        vertuple += (0,)
    assert len(vertuple) == 3
    assert isinstance(vertuple, tuple)

    return vertuple, gitsha


def is_pre_taproot(ver: str | tuple[int, ...] | None = None) -> bool:
    """This this bitcoind node pre-taproot?"""
    if isinstance(ver, str):
        ver_tuple = bitcoind_version(ver)[0]
    elif isinstance(ver, tuple):
        ver_tuple = ver
    elif ver is None:
        ver_tuple = bitcoind_version()[0]
    else:
        raise ValueError("unexpected ver argument")

    return ver_tuple < (0, 21, 1)


@cache
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


@cache
def get_rpc_for_hosts(hosts: t.Tuple[infra.Host]) -> t.Dict[str, BitcoinRpc]:
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
    rpcmap = get_rpc_for_hosts(infra.get_bitcoind_hosts())
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


def wait_for_synced():
    """
    Wait until bitcoind's tip is reasonably current.

    This is helpful for bootstrapping new monited bitcoind instances without
    generating a bunch of spurious data.
    """
    tries = 12
    backoff_secs = 2
    is_synced = False
    got = {}
    i = 0
    rpc = get_rpc()

    while not is_synced:
        try:
            got = rpc.getblockchaininfo()
        except Exception as e:
            print(f"exception getting verification progress: {e}")
            tries -= 1
            time.sleep(backoff_secs)
            if backoff_secs < 120:
                backoff_secs *= 2
        else:
            is_synced = float(got["verificationprogress"]) > 0.9999
            time.sleep(1)
            tries = 12

            if i % 40 == 0:
                print(f"At height {got['blocks']} ({got['verificationprogress']})", flush=True)

            i += 1

    if not is_synced:
        print("Failed to sync!")
        sys.exit(1)

    print(f"Synced to height: {got['blocks']}")
