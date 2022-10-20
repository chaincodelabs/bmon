
from clii import App

from . import bitcoind_tasks
import fastavro
from django.conf import settings

cli = App()


@cli.cmd
def feedline(line: str):
    """Manually process a logline. Useful for testing in dev."""
    bitcoind_tasks.process_line(line)


@cli.cmd
def showmempool():
    """Show the current mempool avro data."""
    with open(settings.MEMPOOL_ACTIVITY_CACHE_PATH / 'current', 'rb') as f:
        for record in fastavro.reader(f):
            print(record)


def main():
    cli.run()
