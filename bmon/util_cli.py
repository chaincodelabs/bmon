import pprint
import logging
from functools import cache
from collections import defaultdict

from clii import App
import fastavro
from django.conf import settings
from django import db
from django.core.exceptions import ValidationError

# If we're not on a bitcoind host, this import will fail - that's okay.
try:
    from . import bitcoind_tasks
except Exception:
    bitcoind_tasks = None  # type: ignore

from .bitcoin.api import gather_rpc, RPC_ERROR_RESULT, wait_for_synced
from . import logparse, models, util, server_tasks


log = logging.getLogger(__name__)
cli = App()


@cli.cmd
def feedline(line: str) -> None:
    """Manually process a logline. Useful for testing in dev."""
    assert bitcoind_tasks
    host = models.Host.objects.filter(name=settings.HOSTNAME).order_by('-id').first()
    assert host
    bitcoind_tasks.process_line(line, host)


@cli.cmd
def showmempool() -> None:
    """Show the current mempool avro data."""
    assert bitcoind_tasks
    with open(settings.MEMPOOL_ACTIVITY_CACHE_PATH / "current", "rb") as f:
        for record in fastavro.reader(f):
            print(record)


@cli.cmd
def run_listener(listener_name: str) -> None:
    """Rerun a listener over all bitcoind log lines."""
    assert bitcoind_tasks
    listeners = [getattr(logparse, listener_name)()]
    host = models.Host.objects.filter(name=settings.HOSTNAME).order_by('-id').first()
    assert host

    assert settings.BITCOIND_LOG_PATH
    with open(settings.BITCOIND_LOG_PATH, "r", errors="ignore") as f:
        for line in f:
            try:
                bitcoind_tasks.process_line(
                    line, host, listeners=listeners, modify_log_pos=False
                )
            except db.IntegrityError:
                pass
            except ValidationError as e:
                if 'already exists' not in str(e):
                    raise


@cli.cmd
def shipmempool() -> None:
    """Ship off mempool activity to GCP."""
    assert bitcoind_tasks
    bitcoind_tasks.mempool_q.immediate = True
    bitcoind_tasks.queue_mempool_to_ship()
    bitcoind_tasks.ship_mempool_activity()


@cli.cmd
def wipe_mempool_backlog() -> None:
    if not bitcoind_tasks:
        # Server
        util.remove_mempool_events(server_tasks.server_q)
        server_tasks.mempool_q.flush_queue()
        server_tasks.mempool_q.flush_schedule()
        server_tasks.mempool_q.flush_results()
    else:
        util.remove_mempool_events(bitcoind_tasks.events_q)


@cli.cmd
def rpc(*cmd) -> None:
    """Gather bitcoind RPC results from all hosts. Should be run on the bmon server."""
    pprint.pprint(gather_rpc(" ".join(cmd)))


@cli.cmd
def wait_for_bitcoind_sync() -> None:
    wait_for_synced()


@cli.cmd
def compare_mempools() -> None:
    mempools = gather_rpc("getrawmempool")
    host_to_set = {}

    for host, res in mempools.items():
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
            results['unique'][hosts[0]].append(tx)
        elif len(hosts) >= over_half:
            for host in (all_hosts - set(hosts)):
                results['missing'][host].append(tx)
        elif len(hosts) < over_half:
            for host in hosts:
                results['have_uncommon'][host].append(tx)

    def default_to_regular(d):
        if isinstance(d, defaultdict):
            d = {k: default_to_regular(v) for k, v in d.items()}
        return d

    pprint.pprint(default_to_regular(results))


def main() -> None:
    cli.run()
