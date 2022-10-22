from clii import App
import pprint

import fastavro
from django.conf import settings

# If we're not on a bitcoind host, this import will fail - that's okay.
try:
    from . import bitcoind_tasks
except Exception:
    bitcoind_tasks = None

from .bitcoin.api import gather_rpc
from . import logparse

cli = App()


@cli.cmd
def feedline(line: str):
    """Manually process a logline. Useful for testing in dev."""
    assert bitcoind_tasks
    bitcoind_tasks.process_line(line)


@cli.cmd
def showmempool():
    """Show the current mempool avro data."""
    assert bitcoind_tasks
    with open(settings.MEMPOOL_ACTIVITY_CACHE_PATH / 'current', 'rb') as f:
        for record in fastavro.reader(f):
            print(record)


@cli.cmd
def run_listener(listener_name: str):
    """Rerun a listener over all bitcoind log lines."""
    assert bitcoind_tasks
    listeners = [getattr(logparse, listener_name)()]

    with open(settings.BITCOIND_LOG_PATH, 'r', errors='ignore') as f:
        for line in f:
            bitcoind_tasks.process_line(line, listeners=listeners)

@cli.cmd
def shipmempool():
    """Ship off mempool activity to GCP."""
    assert bitcoind_tasks
    bitcoind_tasks.queue_mempool_to_ship()


@cli.cmd
def rpc(*cmd):
    """Gather bitcoind RPC results from all hosts. Should be run on the bmon server."""
    pprint.pprint(gather_rpc(' '.join(cmd)))


def main():
    cli.run()
