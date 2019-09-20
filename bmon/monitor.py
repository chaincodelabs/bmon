import time
import argparse
import os
import threading
import signal
import sys
from pathlib import Path

from .bitcoind import RPCClient
from .client import EventSender, ZMQSender
from .config import ZMQ_PORT, ZMQ_SERVER_HOSTNAME
from . import logparse, db


# Globals set during runtime
class _G:
    event_sender: EventSender


shutdown_flag = threading.Event()


def service_shutdown(signum, frame):
    print("Starting shutdown")
    shutdown_flag.set()


def watch_logs(logpath: Path):
    f = open(logpath, 'rb')

    f.seek(-10000, 2)
    # Trim the earliest (probably incomplete) line in the stream.
    f.readline()

    listeners = (
        logparse.ConnectBlockListener(_G.event_sender),
    )

    while True:
        line = f.readline()
        if not line:
            time.sleep(0.2)
            continue

        for listener in listeners:
            listener.process_msg(line.decode())

        if shutdown_flag.is_set():
            print('Shutting down watch logs thread')
            return


def rpc_watcher(cmd: str, period_sec: int, failure_limit: int = 10):
    """
    Decorator for handling looped polling of an RPC endpoint. The decorated
    function is passed the deserialized JSON data and is triggered once
    every `period_sec` seconds.
    """
    def inner(func):
        def wrapper(rpc_client, *args, **kwargs):
            failed = 0
            seq = 0

            while True:
                if shutdown_flag.is_set():
                    print('Shutting down thread')
                    return

                if seq % period_sec == 0:
                    got = rpc_client.call(cmd)
                    if not got:
                        failed += 1
                        if failed % failure_limit == 0:
                            raise RuntimeError(
                                "Shutting down early, too many RPC failures")
                    func(got)
                    seq = 0  # Reset to avoid overflow

                seq += 1
                time.sleep(1)

        return wrapper
    return inner


@rpc_watcher('getmempoolinfo', 60)
def watch_mempool(data):
    _G.event_sender.receive(db.MempoolStatus(
        tx_count=int(data['size']),
        size_vB=int(data['bytes']),
        size_B=int(data['usage'])))


def _get_args():
    parser = argparse.ArgumentParser(description='Monitor bitcoind.')
    parser.add_argument(
        '--datadir', default=os.environ.get('BMON_DATADIR', ''))
    parser.add_argument(
        '--path-to-cli', default=os.environ.get('BMON_PATH_TO_CLI', ''))
    parser.add_argument(
        '--server-url',
        default=os.environ.get(
            'BMON_SERVER_URL', f'tcp://{ZMQ_SERVER_HOSTNAME}:{ZMQ_PORT}'))
    parser.add_argument(
        '--auth-token', default=os.environ.get('BMON_AUTH_TOKEN', ''))
    return parser.parse_args()


def main():
    signal.signal(signal.SIGTERM, service_shutdown)
    signal.signal(signal.SIGINT, service_shutdown)

    args = _get_args()

    if not args.auth_token:
        print('Need to specify host auth token with --auth-token')
        sys.exit(1)

    _G.event_sender = ZMQSender(args.auth_token, args.server_url)
    rpc_client = RPCClient(args.datadir, args.path_to_cli)

    t1 = threading.Thread(
        target=watch_logs, args=(Path(args.datadir) / 'debug.log',))
    t2 = threading.Thread(
        target=watch_mempool, args=(rpc_client,))

    print("Starting log watcher")
    t1.start()

    print("Starting mempool watcher")
    t2.start()

    t1.join()
    t2.join()


if __name__ == '__main__':
    main()
