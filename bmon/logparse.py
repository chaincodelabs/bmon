#!/usr/bin/env python3
import time
import logging
import re
import hashlib
import datetime
import os
import typing as t
from pathlib import Path

from django.conf import settings
from django.forms.models import model_to_dict

from bmon.models import ConnectBlockDetails, ConnectBlockEvent, LogProgress
from bmon import models
from bmon.bitcoind_tasks import send_event


log = logging.getLogger(__name__)


def watch_logs(filename: str):
    log.info(f"listening to logs at {filename}")

    listeners = [
        ConnectBlockListener(),
        MempoolListener(),
    ]

    log_progress = LogProgress.objects.filter(host=settings.HOSTNAME).first()
    start_log_cursor = log_progress.loghash if log_progress else None

    for line in read_logfile_forever(filename, start_log_cursor):
        linehash = hash_noncrypto(line)

        for listener in listeners:
            try:
                got = listener.process_line(line)
            except Exception:
                log.exception("Listener %s failed to process line %r", listener, line)
                models.ProcessLineError.objects.create(
                    host=settings.HOSTNAME,
                    listener=listener.__class.__name__,
                    line=line,
                )
                continue

            if got:
                log.debug("Got an instance %r from line (%s) %r", got, linehash, line)
                try:
                    got.full_clean()
                except Exception:
                    log.exception("model %s failed to validate!", got)
                    # TODO: stash the bad model somewhere for later processing.
                    continue

                d = model_to_dict(got)
                d['_model'] = got.__class__.__name__

                # A corresponding LogProgress entry is saved in `server_tasks`.
                send_event.delay(d, linehash)


def read_logfile_forever(
    filename: str | Path, seek_to_cursor: str | None = None
) -> t.Iterator[str]:
    """
    A generator that reads lines out of a logfile and is resilient to log rotation.

    Args:
        seek_to_cursor: if passed, seek to the line that hashes to this value. If no
            such line can be found, process all lines.

    Taken and modified from https://stackoverflow.com/a/25632664.
    """
    def openfile():
        return open(filename, "r", errors="ignore")

    current = openfile()
    curino = os.fstat(current.fileno()).st_ino
    start_pos = None

    if seek_to_cursor:
        log.info("Attempting to seek to logline cursor %s", seek_to_cursor)

        while True:
            line = current.readline()
            if not line:
                break

            # Must strip the newline off the end to match contents as yielded below.
            hashed = hash_noncrypto(line.rstrip('\n'))

            if hashed == seek_to_cursor:
                start_pos = current.tell()
                log.info("Found start of logs (per cursor %s) at %s",
                         seek_to_cursor, start_pos)
                break

        if not start_pos:
            log.warning(
                "Desired logline cursor (%s) not found in file %s - parsing all lines",
               seek_to_cursor, filename)

    current.seek(0)
    if start_pos:
        current.seek(start_pos)

    curr_line = ''

    while True:
        while True:
            got: str = current.read(1024)

            if not got:
                # Out of contents
                break
            elif '\n' in got:
                # The chunk we retrieved may have multiple lines, so yield multiple
                # if need be.
                lines = got.split('\n')
                assert len(lines) >= 2

                [end_of_current, *middle_lines, next_curr_line] = lines

                yield curr_line + end_of_current

                for complete_line in middle_lines:
                    yield complete_line

                curr_line = next_curr_line
            else:
                curr_line += got

        try:
            if os.stat(filename).st_ino != curino:
                new = openfile()
                current.close()
                current = new
                curino = os.fstat(current.fileno()).st_ino
            else:
                time.sleep(0.001)
        except IOError:
            pass



def hash_noncrypto(w: str) -> str:
    """TODO: replace this with something faster if shown to be a bottleneck."""
    return hashlib.md5(w.encode()).hexdigest()


LineHash = str


def get_time(line: str = '', timestr: str = '') -> datetime.datetime:
    """
    Return the time a log message was emitted in UTC.
    """
    if not (line or timestr):
        raise ValueError('arg required')
    if not timestr:
        timestr = line.split()[0]

    d = datetime.datetime.fromisoformat(timestr.strip())

    # Ensure any date we parse is tz-aware.
    assert (offset := d.utcoffset()) is not None

    return d + offset


_FLOAT = r'\d*\.\d+'
_HASH = r'[a-f0-9]+'
_HEX = r'0x[a-f0-9]+'
_NOT_QUOTE = '[^\'"]+'
_UPDATE_TIP_START = "UpdateTip: "


def str_or_none(s):
    return str(s) if s else None


class MempoolListener:

    _accept_sub_patts = {
        re.compile(r"\s+peer=(?P<peer_num>\d+)"),
        re.compile(fr"\s+accepted (?P<txhash>{_HASH})"),
        re.compile(r"poolsz (?P<pool_size_txns>\d+) txn, (?P<pool_size_kb>\d+) kB"),
    }

    def process_line(self, line):
        if ' AcceptToMemoryPool:' in line and ' accepted ' in line:
            matches = {}
            timestamp = get_time(line)
            for patt in self._accept_sub_patts:
                if (match := patt.search(line)):
                    matches.update(match.groupdict())

            return models.MempoolAccept(
                host=settings.HOSTNAME,
                timestamp=timestamp,
                peer_num=int(matches['peer_num']),
                txhash=matches['txhash'],
                pool_size_kb=int(matches['pool_size_kb']),
                pool_size_txns=int(matches['pool_size_txns']),
            )


class ConnectBlockListener:
    _detail_patts = {
        re.compile(fr"- Load block from disk: (?P<load_block_from_disk_time_ms>{_FLOAT})ms "),
        re.compile(fr"- Sanity checks: (?P<sanity_checks_time_ms>{_FLOAT})ms "),
        re.compile(fr"- Fork checks: (?P<fork_checks_time_ms>{_FLOAT})ms "),
        re.compile(fr"- Connect (?P<tx_count>\d+) transactions: (?P<connect_txs_time_ms>{_FLOAT})ms "),
        re.compile(fr"- Verify (?P<txin_count>\d+) txins: (?P<verify_time_ms>{_FLOAT})ms "),
        re.compile(fr"- Index writing: (?P<index_writing_time_ms>{_FLOAT})ms "),
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
        re.compile(fr"\s+log2_work=(?P<log2_work>{_FLOAT}) "),
    }

    match_types = {
        float: (
            'load_block_from_disk_time_ms',
            'sanity_checks_time_ms',
            'fork_checks_time_ms',
            'connect_txs_time_ms',
            'verify_time_ms',
            'index_writing_time_ms',
            'connect_total_time_ms',
            'flush_coins_time_ms',
            'flush_chainstate_time_ms',
            'connect_postprocess_time_ms',
            'connectblock_total_time_ms',
        ),
        int: (
            'tx_count',
            'txin_count',
            'height',
            'cachesize_txo',
            'total_tx_count'
        ),
        str: (
            'blockhash',
            'version',
            'date',
        ),
        str_or_none: (
            'warning',
        ),
    }

    def __init__(self):
        self.next_details = ConnectBlockDetails()
        self.current_height = None
        self.current_blockhash = None

    def process_line(
        self, line: str
    ) -> t.Optional[t.Union[ConnectBlockEvent, ConnectBlockDetails]]:
        """
        Aggregates two kind of connectblock-related events. This is because the events
        both share log data in terms of blockhash and height.

        We want to make sure that we, at the very least, persist the ConnectBlock events
        but we also want the fine-grained timing details if we can get them.
        """
        matchgroups = {}

        # Special-case UpdateTip since we can return the db event in one shot
        # (based on a single log line).
        if line.find(_UPDATE_TIP_START) != -1:
            for patt in self._update_tip_sub_patts:
                if (match := patt.search(line)):
                    matchgroups.update(match.groupdict())

            timestamp = get_time(line)

            # 0.12 has UpdateTip: lines that just display the warning, so skip those.
            if 'height' not in matchgroups:
                return

            self.current_height = int(matchgroups['height'])
            self.current_blockhash = matchgroups['blockhash']

            cachesize_mib = (
                float(matchgroups['cachesize_mib'])
                if 'cachesize_mib' in matchgroups else None)

            return ConnectBlockEvent(
                host=settings.HOSTNAME,
                timestamp=timestamp,
                blockhash=self.current_blockhash,
                height=self.current_height,
                log2_work=float(matchgroups['log2_work']),
                total_tx_count=int(matchgroups['total_tx_count']),
                version=matchgroups.get('version'),
                date=get_time(matchgroups['date']),
                cachesize_mib=cachesize_mib,
                cachesize_txo=float(matchgroups['cachesize_txo']),
                warning=matchgroups.get('warning'),
            )

        # The rest of the code handles creation of ConnectBlockDetails.

        for patt in self._detail_patts:
            if (match := patt.search(line)):
                matchgroups.update(match.groupdict())
                break

        if not matchgroups:
            return

        dict_onto_event(matchgroups, self.next_details, self.match_types)

        # Event is ready for persisting!
        if self.next_details.connectblock_total_time_ms is not None:
            self.next_details.host = settings.HOSTNAME
            self.next_details.blockhash = self.current_blockhash
            self.next_details.height = self.current_height
            self.next_details.timestamp = get_time(line)

            completed = self.next_details
            self.next_details = ConnectBlockDetails()
            self.current_blockhash = None
            self.current_height = None
            return completed

        return None


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
            log.warning("[%s] matched attribute not recognized: %s",
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
