#!/usr/bin/env python3
import logging
import re
import hashlib
import os
import typing as t
from dataclasses import dataclass
from pathlib import Path

from . import db

logger = logging.getLogger(__name__)


def read_logfile_forever(filename: str | Path) -> t.Iterator[str]:
    """
    A generator that reads lines out of a logfile and is resilient to log rotation.

    Taken and modified from https://stackoverflow.com/a/25632664.
    """
    def openfile():
        return open(filename, "r", errors="ignore")

    current = openfile()
    curino = os.fstat(current.fileno()).st_ino
    curr_line = ''

    while True:
        while True:
            curr_line: str = current.readline()
            if not curr_line:
                break
            yield curr_line
        try:
            if os.stat(filename).st_ino != curino:
                new = openfile()
                current.close()
                current = new
                curino = os.fstat(current.fileno()).st_ino
        except IOError:
            pass


def hash_noncrypto(w) -> str:
    """TODO: replace this with something faster if shown to be a bottleneck."""
    return hashlib.md5(w).hexdigest()


LineHash = str


@dataclass
class BitcoindLogFollower:
    filename: str

    # If specified, skip forward to a specific line.
    sync_to: LineHash | None = None

    # The line we're currently at.
    current_position: LineHash | None = None

    def monitor(self):
        pass


_FLOAT = r'\d*\.\d+'
_HASH = r'[a-f0-9]+'
_HEX = r'0x[a-f0-9]+'
_NOT_QUOTE = '[^\'"]+'
_UPDATE_TIP_START = "UpdateTip: "


class ConnectBlockListener:
    _patts = {
        re.compile(fr"- Load block from disk: (?P<load_block_from_disk_time_ms>{_FLOAT})ms "),
        re.compile(fr"- Sanity checks: (?P<sanity_checks_time_ms>{_FLOAT})ms "),
        re.compile(fr"- Fork checks: (?P<fork_checks_time_ms>{_FLOAT})ms "),
        re.compile(fr"- Connect (?P<tx_count>\d+) transactions: (?P<connect_txs_time_ms>{_FLOAT})ms "),
        re.compile(fr"- Verify (?P<txin_count>\d+) txins: (?P<verify_time_ms>{_FLOAT})ms "),
        re.compile(fr"- Index writing: (?P<index_writing_time_ms>{_FLOAT})ms "),
        re.compile(fr"- Callbacks: (?P<callbacks_time_ms>{_FLOAT})ms "),
        re.compile(fr"- Connect total: (?P<connect_total_time_ms>{_FLOAT})ms "),
        re.compile(fr"- Flush: (?P<flush_coins_time_ms>{_FLOAT})ms "),
        re.compile(fr"- Writing chainstate: (?P<flush_chainstate_time_ms>{_FLOAT})ms "),
        # UpdateTip messages are handled below.
        re.compile(fr"- Connect postprocess: (?P<connect_postprocess_time_ms>{_FLOAT})ms "),
        re.compile(fr"- Connect block: (?P<connectblock_total_time_ms>{_FLOAT})ms "),
    }

    # 'UpdateTip: ...' subpatterns. Grab whatever of this we can - lot of
    # variation between versions.
    _update_tip_sub_patts = {
        re.compile(fr"new\s+best=(?P<blockhash>{_HASH})\s+"),
        re.compile(r"\s+height=(?P<height>\d+)\s+"),
        # version only present in 0.13+
        re.compile(fr"\s+version=(?P<version>{_HEX})\s+"),
        re.compile(r"\s+tx=(?P<total_tx_count>\d+)\s+"),
        # Early date format
        re.compile(r"\s+date='?(?P<date>[0-9-]+ [0-9:]+)'?\s+"),
        # Later date format
        re.compile(fr"\s+date='(?P<date>{_NOT_QUOTE})'\s+"),
        re.compile(fr"\s+cache=(?P<cachesize_mib>{_FLOAT})MiB\((?P<cachesize_txo>\d+)txo?\)"),
        re.compile(fr"\s+warning='(?P<warning>{_NOT_QUOTE})'"),
        re.compile(r"\s+cache=(?P<cachesize_txo>\d+)\s*$"),
    }

    def __init__(self):
        self.next_event = db.ConnectBlockEvent()

    def process_line(self, msg: str):
        matchgroups = {}

        # Special-case UpdateTip since there are so many variations.
        if msg.find(_UPDATE_TIP_START) != -1:
            for patt in self._update_tip_sub_patts:
                match = patt.search(msg)
                if match:
                    matchgroups.update(match.groupdict())
        else:
            for patt in self._patts:
                match = patt.search(msg)
                if match:
                    matchgroups.update(match.groupdict())
                    break

        if not matchgroups:
            return

        def str_or_none(s):
            return str(s) if s else None

        dict_onto_event(matchgroups, self.next_event, {
            int: ('tx_count', 'txin_count', 'height', 'cachesize_txo',
                  'total_tx_count'),
            str: ('blockhash', 'version', 'date'),
            str_or_none: ('warning',),
        })

        # Event is ready for persisting!
        if self.next_event.connectblock_total_time_ms is not None:
            completed = self.next_event
            self.next_event = db.ConnectBlockEvent()
            return completed


def dict_onto_event(d: dict, event, type_map: dict):
    """
    Take the entries in a dictionary and map them onto a database event.

    Apply type conversions to the raw strings using type_map.
    """
    type_map = type_map or {}
    name_to_type = {n: t for t, names in type_map.items() for n in names}

    for k, v in d.items():
        if hasattr(event, k):
            conversion_fnc = name_to_type.get(k, float)
            v = conversion_fnc(v)
            setattr(event, k, v)
        else:
            logger.warning("[%s] matched attribute not recognized: %s",
                           event.__class__.__name__, k)


def parse_log_line():

    # got inv: tx {txid}  new peer={peer_id}

    # Expired {count} transactions from the memory pool

    # Regular block reception (GotBlockEvent)
    # Compact block reception (GotBlockEvent)

    # Valid fork found
    # -------------------
    # Warning: Large valid fork found
    #   forking the chain at height %d ({blockhash})
    #   lasting to height %d ({blockhash}).
    # Chain state database corruption likely.

    # Invalid chain found
    # -------------------
    # Warning: Found invalid chain at least ~6 blocks longer than our best chain.

    # Invalid block found
    # -------------------
    # {funcname}: invalid block={blockhash}  height={height}  log2_work={work}  date={block_datetime}

    # Compact blocks
    # -------------------
    # Successfully reconstructed block {blockhash} with {n} txn prefilled, {n} txn from mempool (incl at least {n} from extra pool) and {n} txn requested

    ########
    # TODO
    ########

    # replacing tx %s with %s for %s BTC additional fees, %d delta bytes
    pass


# def main():
#     import sys
#     import pathlib
#     recv = EventReceiver()
#     listen = ConnectBlockListener(recv)

#     contents = pathlib.Path(sys.argv[1]).read_text().splitlines()

#     for line in contents:
#         listen.process_msg(line)

#     print("done")

def main():
    try:
        monitor_bitcoind_log('/home/james/.bitcoin/debug.log')
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
