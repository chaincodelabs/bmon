from pathlib import Path

from .. import config
from ..logparse import read_logfile_forever, ConnectBlockListener
from .tasks import send_event


def monitor_bitcoind_log(filename: str | Path):
    cb_listener = ConnectBlockListener()

    print(f"listening to logs at {filename}")
    for line in read_logfile_forever(filename):
        got = cb_listener.process_line(line)
        if got:
            print("Finally created an event")
            send_event.delay(got.as_dict())


def main():
    assert config.BITCOIND_LOG_PATH
    monitor_bitcoind_log(config.BITCOIND_LOG_PATH)
