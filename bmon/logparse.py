#!/usr/bin/env python3
import time
import logging
import re
import hashlib
import datetime
import os
import typing as t
from pathlib import Path

import walrus
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from bmon.models import ConnectBlockDetails, ConnectBlockEvent
from bmon.bitcoin.api import is_pre_taproot
from bmon import models


log = logging.getLogger(__name__)


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

    def openfile() -> t.IO[str]:
        return open(filename, "r", errors="ignore")

    current = openfile()
    curino = os.fstat(current.fileno()).st_ino
    start_pos = None

    if seek_to_cursor:
        log.info("attempting to seek to logline cursor %s", seek_to_cursor)
        lineno = 0

        while True:
            line = current.readline()
            if not line:
                break

            # Must strip the newline off the end to match contents as yielded below.
            hashed = linehash(line.rstrip("\n"))

            if hashed == seek_to_cursor:
                start_pos = current.tell()
                log.info(
                    "found start of logs (per cursor %s) at %s",
                    seek_to_cursor,
                    start_pos,
                )
                break

            lineno += 1
            if lineno % 10_000 == 0:
                log.info("still seeking... %s lines seen", lineno)

        if not start_pos:
            log.warning(
                "desired logline cursor (%s) not found in file %s - parsing all lines",
                seek_to_cursor,
                filename,
            )

    current.seek(0)
    if start_pos:
        current.seek(start_pos)
        log.info("starting to parse logs from pos %s", start_pos)

    curr_line = ""
    lines_processed = 0
    LOG_AFTER = 10_000
    got_line_yet = False

    while True:
        while True:
            # I'm doing this awkward "manual scan" for newlines because I found
            # file.readline() to be flakey; there were occasional misreads that
            # would smash lines together. This manual scan method seems to work
            # and is performant enough for my needs.
            got: str = current.read(1024)

            if not got:
                # Out of contents
                break
            elif "\n" in got:
                lines = got.split("\n")
                assert len(lines) >= 2

                # The chunk we retrieved may have multiple lines, so yield multiple
                # if need be.
                [end_of_current, *middle_lines, next_curr_line] = lines

                yield (sent_line := curr_line + end_of_current)
                lines_processed += 1

                if not got_line_yet:
                    log.info("first line processed: %r", sent_line)
                    got_line_yet = True

                if lines_processed > LOG_AFTER:
                    lines_processed = 0
                    log.info(
                        "processed logs from %s up to %s", filename, get_time(sent_line)
                    )

                for complete_line in middle_lines:
                    yield complete_line
                    lines_processed += 1

                curr_line = next_curr_line
            else:
                curr_line += got

        try:
            if os.stat(filename).st_ino != curino:
                log.info("detected inode change in %s; reopening file", filename)
                new = openfile()
                current.close()
                current = new
                curino = os.fstat(current.fileno()).st_ino
            else:
                time.sleep(0.01)
        except IOError:
            pass


class LogfilePosManager:
    """
    Manage persisting a cursor into the bitcoind's logfile.

    The cursor is cached in the bitcoind-local redis to not hinder performance, and then
    periodically flushed into postgres.
    """

    REDIS_SEPARATOR = " | "

    def __init__(self, host: str, db: walrus.Database):
        self.host = host
        self.redis_key = f"logpos.{host}"
        self.db = db
        self.lock = self.db.lock(f"lock.logpos.{host}", ttl=1_000)

    def getpos(self) -> None | tuple[str, datetime.datetime]:
        with self.lock:
            if not (got := self.db.get(self.redis_key)):
                return None
            linehash, dt_str = got.split(self.REDIS_SEPARATOR)
            return (linehash, datetime.datetime.fromisoformat(dt_str))

    def mark(self, linehash: str) -> None:
        """
        Persist logfile position in redis.

        We cache in redis because some high-volume events would overwhelm the db with
        writes to maintain this state (e.g. MempoolAccept).
        """
        with self.lock:
            self.db[
                self.redis_key
            ] = f"{linehash}{self.REDIS_SEPARATOR}{timezone.now().isoformat()}"

    def flush(self) -> None:
        """
        Write the logfile pos from redis into postgres.
        """
        if not (got := self.getpos()):
            return
        linehash, dt = got

        log.info("flushing logfile pos for %s (%s @ %s)", self.host, linehash, dt)
        models.LogProgress.objects.update_or_create(
            hostname=self.host,
            defaults={"loghash": linehash, "timestamp": dt},
        )


def linehash(w: str) -> str:
    """
    A fastish, non-cryptographic line hash.
    """
    return hashlib.md5(w.encode()).hexdigest()


LineHash = str


def get_time(line: str = "", timestr: str = "") -> datetime.datetime:
    """
    Return the time a log message was emitted in UTC.
    """
    if not (line or timestr):
        raise ValueError("arg required")
    if not timestr:
        timestr = line.split()[0]

    d = datetime.datetime.fromisoformat(timestr.strip())

    # Ensure any date we parse is tz-aware.
    assert (offset := d.utcoffset()) is not None

    return d + offset


_FLOAT = r"\d*\.\d+"
_HASH = r"[a-f0-9]+"
_HEX = r"0x[a-f0-9]+"
_NOT_QUOTE = "[^'\"]+"
_UPDATE_TIP_START = "UpdateTip: "

_PEER_PATT = re.compile(r"\s+peer=(?P<peer_num>\d+)")


def str_or_none(s: t.Any) -> None | str:
    return str(s) if s else None


class Listener(t.Protocol):
    def process_line(self, line: str) -> t.Any:
        pass

    def _match(self, patterns: t.Set[re.Pattern], line: str) -> dict:
        matches = {}

        for patt in patterns:
            if match := patt.search(line):
                matches.update(match.groupdict())

        return matches


class MempoolAcceptListener(Listener):

    _accept_sub_patts = {
        _PEER_PATT,
        re.compile(rf"\s+accepted (?P<txhash>{_HASH})"),
        re.compile(r"poolsz (?P<pool_size_txns>\d+) txn, (?P<pool_size_kb>\d+) kB"),
    }

    def __init__(self, ignore_older_than: t.Optional[datetime.timedelta] = None):
        self.ignore_older_than = ignore_older_than

    def process_line(self, line: str) -> None | models.MempoolAccept:
        if not (" AcceptToMemoryPool:" in line and " accepted " in line):
            return None

        timestamp = get_time(line)

        if self.ignore_older_than and \
                (timezone.now() - timestamp) > self.ignore_older_than:
            return None

        matches = self._match(self._accept_sub_patts, line)

        return models.MempoolAccept(
            timestamp=timestamp,
            peer_num=int(matches["peer_num"]),
            txhash=matches["txhash"],
            pool_size_kb=int(matches["pool_size_kb"]),
            pool_size_txns=int(matches["pool_size_txns"]),
        )


class MempoolRejectListener(Listener):
    """
    [msghand] 4b93cc953162c4d953918e60fe1b9f48aae82e049ace3c912479e0ff5c7218c3 from peer=6 was not accepted: txn-mempool-conflict
    [msghand] 91224dbc928799dfd9ca21c1364e1d9ce3168c604f743ff34a3a4e4bde8c23af from peer=3 was not accepted: insufficient fee, rejecting replacement 91224dbc928799dfd9ca21c1364e1d9ce3168c604f743ff34a3a4e4bde8c23af; new feerate 0.00005965 BTC/kvB <= old feerate 0.00008334 BTC/kvB

    5bff289c800bb1ddf4f3e82ae2964b968d3ffa718e7481f560130060102e9711 from peer=12 was not accepted: insufficient fee, rejecting replacement 5bff289c800bb1ddf4f3e82ae2964b968d3ffa718e7481f560130060102e9711, not enough additional fees to relay; 0.00 < 0.00009128
    """

    def __init__(self, ignore_older_than: t.Optional[datetime.timedelta] = None):
        self.ignore_older_than = ignore_older_than

    _accept_sub_patts = {
        _PEER_PATT,
        re.compile(
            rf"\s+(?P<txhash>{_HASH})"
            rf"(\s+\(wtxid=(?P<wtxid>{_HASH})\))?"
            rf"\s+from peer"
        ),
        re.compile(rf"new feerate\s+(?P<insufficient_feerate>{_FLOAT})\s+BTC/kvB"),
        re.compile(rf"old feerate\s+(?P<old_feerate>{_FLOAT})\s+BTC/kvB"),
        re.compile(
            rf"not enough additional fees\D+(?P<insufficient_fee>{_FLOAT})\D+(?P<old_fee>{_FLOAT})"
        ),
    }

    def process_line(self, line: str) -> None | models.MempoolReject:
        if not (" was not accepted:" in line and " from peer=" in line):
            return None

        timestamp = get_time(line)

        if self.ignore_older_than and \
                (timezone.now() - timestamp) > self.ignore_older_than:
            return None

        matches = self._match(self._accept_sub_patts, line)
        reason = line.split("was not accepted:")[-1].strip()
        assert reason
        reason_code = models.MempoolReject.get_reason_reject_code(reason)
        assert reason_code

        # Pre-taproot nodes get too many standardness mismatches to store (on the order
        # of 30,000 per day).
        if is_pre_taproot() and reason_code in [
            "scriptpubkey",
            "non-mandatory-script-verify-flag",
        ]:
            return None

        reason_data = {}
        if "insufficient_feerate" in matches:
            reason_data["insufficient_feerate_btc_kvB"] = matches[
                "insufficient_feerate"
            ]
            reason_data["old_feerate_btc_kvB"] = matches["old_feerate"]

        if "insufficient_fee" in matches:
            reason_data["insufficient_fee_btc"] = matches["insufficient_fee"]
            reason_data["old_fee_btc"] = matches["old_fee"]

        return models.MempoolReject(
            timestamp=timestamp,
            peer_num=int(matches["peer_num"]),
            # `peer` FK will be filled out in `bitcoind_tasks`, where the redis cache
            # lives.
            txhash=matches["txhash"],
            wtxid=matches.get('wtxid'),
            reason=reason,
            reason_data=reason_data,
            reason_code=reason_code,
        )


class PongListener(Listener):
    """
    Listen for pong messages; this is a convenient way of determining when we
    should refresh cached peer information.

    2022-10-23T13:21:28.681866Z received: pong (8 bytes) peer=3
    """
    def __init__(self, ignore_older_than: t.Optional[datetime.timedelta] = None):
        self.ignore_older_than = ignore_older_than

    def process_line(self, line):
        if " received: pong " not in line:
            return

        timestamp = get_time(line)

        if self.ignore_older_than and \
                (timezone.now() - timestamp) > self.ignore_older_than:
            return None

        if match := _PEER_PATT.search(line):
            return int(match.groups()[0])
        else:
            log.warning("malformed pong message: %s", line)


BlockEvent = models.BlockDisconnectedEvent | models.BlockConnectedEvent


class _BlockEventListener(Listener):
    """
    2022-10-22T14:22:49.357774Z [msghand] [validationinterface.cpp:239] [BlockDisconnected] [validation] Enqueuing BlockDisconnected: block hash=3cfd126d960a9b87823fd94d48121f774aac448c9a6f1b48efc547c61f9b8c1f block height=1
    """

    event_type: str
    event_class: t.Type[BlockEvent]

    _patts: set[re.Pattern[str]] = {
        re.compile(r"\s+height=(?P<height>\d+)"),
        re.compile(rf"\s+hash=(?P<blockhash>{_HASH})"),
    }

    def process_line(self, line: str) -> None | BlockEvent:
        # Ignore the duplicate "Enqueuing" lines.
        if f" {self.event_type}: " in line and " Enqueuing " not in line:
            matches = self._match(self._patts, line)
            timestamp = get_time(line)

            assert self.event_class
            return self.event_class(  # typing: ignore
                timestamp=timestamp,
                height=int(matches["height"]),
                blockhash=matches["blockhash"],
            )
        return None


class BlockDisconnectedListener(_BlockEventListener):
    event_type: str = "BlockDisconnected"
    event_class = models.BlockDisconnectedEvent


class BlockConnectedListener(_BlockEventListener):
    event_type: str = "BlockConnected"
    event_class = models.BlockConnectedEvent


class ReorgListener(Listener):
    def __init__(self) -> None:
        self.disconnects: list[models.BlockDisconnectedEvent] = []
        self.replacements: list[models.BlockConnectedEvent] = []
        self.disconnect_listener = BlockDisconnectedListener()
        self.connect_listener = BlockConnectedListener()

    @property
    def max_height(self) -> None | int:
        if self.disconnects:
            return self.disconnects[-1].height
        return None

    def process_line(self, line: str) -> None | models.ReorgEvent:
        got = None
        for listener in (self.disconnect_listener, self.connect_listener):
            if got := listener.process_line(line):
                break

        if not got:
            return None

        if isinstance(got, models.BlockDisconnectedEvent):
            self.disconnects.insert(0, got)

            if len(self.disconnects) == 1:
                log.info(
                    "started to detect a reorg at height %s (%s)",
                    got.height,
                    got.blockhash,
                )
        elif isinstance(got, models.BlockConnectedEvent):
            # If we don't have any outstanding disconnects, this is just a regular
            # connection event.
            if not self.disconnects:
                return None

            max_height = self.max_height
            assert isinstance(max_height, int)

            if got.height <= max_height:
                self.replacements.append(got)

                if got.height < max_height:
                    # We haven't yet completed the reorg; we're still connecting
                    # substitute blocks.
                    return None

            # If we're here, we have completed the reorg.

            len_mismatch = len(self.replacements) != len(self.disconnects)
            d_heights = [i.height for i in self.disconnects]
            r_heights = [i.height for i in self.replacements]
            if len_mismatch or d_heights != r_heights:
                log.error(
                    "WARNING: reorg detection looks broken; "
                    "disconnects: %s vs. replacements: %s",
                    self.disconnects,
                    self.replacements,
                )

            reorg = models.ReorgEvent(
                finished_timestamp=self.replacements[-1].timestamp,
                min_height=self.disconnects[0].height,
                max_height=max_height,
                old_blockhashes=[d.blockhash for d in self.disconnects],
                new_blockhashes=[r.blockhash for r in self.replacements],
            )
            self.disconnects = []
            self.replacements = []

            log.info("Reorg finished: %s", reorg)
            return reorg
        return None


class ConnectBlockListener(Listener):
    _detail_patts = {
        re.compile(
            rf"- Load block from disk: (?P<load_block_from_disk_time_ms>{_FLOAT})ms "
        ),
        re.compile(rf"- Sanity checks: (?P<sanity_checks_time_ms>{_FLOAT})ms "),
        re.compile(rf"- Fork checks: (?P<fork_checks_time_ms>{_FLOAT})ms "),
        re.compile(
            rf"- Connect (?P<tx_count>\d+) transactions: (?P<connect_txs_time_ms>{_FLOAT})ms "
        ),
        re.compile(
            rf"- Verify (?P<txin_count>\d+) txins: (?P<verify_time_ms>{_FLOAT})ms "
        ),
        re.compile(rf"- Index writing: (?P<index_writing_time_ms>{_FLOAT})ms "),
        re.compile(rf"- Connect total: (?P<connect_total_time_ms>{_FLOAT})ms "),
        re.compile(rf"- Flush: (?P<flush_coins_time_ms>{_FLOAT})ms "),
        re.compile(rf"- Writing chainstate: (?P<flush_chainstate_time_ms>{_FLOAT})ms "),
        # UpdateTip messages are handled below.
        re.compile(
            rf"- Connect postprocess: (?P<connect_postprocess_time_ms>{_FLOAT})ms "
        ),
        re.compile(rf"- Connect block: (?P<connectblock_total_time_ms>{_FLOAT})ms "),
    }

    # 'UpdateTip: ...' subpatterns. Grab whatever of this we can - lot of
    # variation between versions.
    _update_tip_sub_patts = {
        re.compile(rf"new\s+best=(?P<blockhash>{_HASH})\s+"),
        re.compile(r"\s+height=(?P<height>\d+)\s+"),
        # version only present in 0.13+
        re.compile(rf"\s+version=(?P<version>{_HEX})\s+"),
        re.compile(r"\s+tx=(?P<total_tx_count>\d+)\s+"),
        # Early date format
        re.compile(r"\s+date='?(?P<date>[0-9-]+ [0-9:]+)'?\s+"),
        # Later date format
        re.compile(rf"\s+date='(?P<date>{_NOT_QUOTE})'\s+"),
        re.compile(
            rf"\s+cache=(?P<cachesize_mib>{_FLOAT})MiB\((?P<cachesize_txo>\d+)txo?\)"
        ),
        re.compile(rf"\s+warning='(?P<warning>{_NOT_QUOTE})'"),
        re.compile(r"\s+cache=(?P<cachesize_txo>\d+)\s*$"),
        re.compile(rf"\s+log2_work=(?P<log2_work>{_FLOAT}) "),
    }

    match_types = {
        float: (
            "load_block_from_disk_time_ms",
            "sanity_checks_time_ms",
            "fork_checks_time_ms",
            "connect_txs_time_ms",
            "verify_time_ms",
            "index_writing_time_ms",
            "connect_total_time_ms",
            "flush_coins_time_ms",
            "flush_chainstate_time_ms",
            "connect_postprocess_time_ms",
            "connectblock_total_time_ms",
        ),
        int: ("tx_count", "txin_count", "height", "cachesize_txo", "total_tx_count"),
        str: (
            "blockhash",
            "version",
            "date",
        ),
        str_or_none: ("warning",),
    }

    def __init__(self) -> None:
        self.next_details = ConnectBlockDetails()
        self.current_height: None | int = None
        self.current_blockhash: None | str = None

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
            matchgroups.update(self._match(self._update_tip_sub_patts, line))

            timestamp = get_time(line)

            # 0.12 has UpdateTip: lines that just display the warning, so skip those.
            if "height" not in matchgroups:
                return None

            self.current_height = int(matchgroups["height"])
            self.current_blockhash = matchgroups["blockhash"]

            cachesize_mib = (
                float(matchgroups["cachesize_mib"])
                if "cachesize_mib" in matchgroups
                else None
            )

            return ConnectBlockEvent(
                timestamp=timestamp,
                blockhash=self.current_blockhash,
                height=self.current_height,
                log2_work=float(matchgroups["log2_work"]),
                total_tx_count=int(matchgroups["total_tx_count"]),
                version=matchgroups.get("version"),
                date=get_time(matchgroups["date"]),
                cachesize_mib=cachesize_mib,
                cachesize_txo=float(matchgroups["cachesize_txo"]),
                warning=matchgroups.get("warning"),
            )

        # The rest of the code handles creation of ConnectBlockDetails.

        for patt in self._detail_patts:
            if match := patt.search(line):
                matchgroups.update(match.groupdict())
                break

        if not matchgroups:
            return None

        dict_onto_event(matchgroups, self.next_details, self.match_types)

        # Event is ready for persisting!
        if self.next_details.connectblock_total_time_ms is not None:
            assert self.current_blockhash
            assert isinstance(self.current_height, int)

            self.next_details.blockhash = self.current_blockhash
            self.next_details.height = self.current_height
            self.next_details.timestamp = get_time(line)

            completed = self.next_details
            self.next_details = ConnectBlockDetails()
            self.current_blockhash = None
            self.current_height = None
            return completed

        return None


# class MempoolExpiryListener:
#     _patt = re.compile(
#         r"Expired (?P<expired_num>\d+) transactions from the memory pool"
#     )
#     def process_line(self, line: str):
#         pass


def dict_onto_event(d: dict[str, str], event: t.Any, type_map: t.Any) -> None:
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
            log.warning(
                "[%s] matched attribute not recognized: %s", event.__class__.__name__, k
            )


class BlockDownloadTimeoutListener(Listener):

    _timeout_patts = {
        re.compile(rf"block (?P<blockhash>{_HASH})"),
        _PEER_PATT,
    }

    def __init__(self, ignore_older_than: t.Optional[datetime.timedelta] = None):
        self.ignore_older_than = ignore_older_than

    def process_line(self, line: str) -> None | models.BlockDownloadTimeout:
        if "Timeout downloading block " not in line:
            return None

        matches = self._match(self._timeout_patts, line)
        timestamp = get_time(line)

        return models.BlockDownloadTimeout(
            timestamp=timestamp,
            peer_num=int(matches["peer_num"]),
            blockhash=matches["blockhash"],
        )


class HeaderToTipListener(Listener):
    """
    Cue on "Saw new header" message, then record the amount of time to get to tip.

    Saw new header hash= height=
    Saw new cmpctblock header hash= peer=12
    Successfully reconstructed block <hash> with 1 txn prefilled, 3313 txn from mempool (incl at least 0 from extra pool) and 1 txn requested
    """
    _header_patts = {
        re.compile(rf"hash=(?P<blockhash>{_HASH})"),
        re.compile(r"height=(?P<height>\d+)"),
    }

    _reconstruct_patts = {
        re.compile(fr"block (?P<blockhash>{_HASH})"),
        re.compile(r"(?P<num_prefilled>\d+) txn prefilled"),
        re.compile(r"(?P<num_from_mempool>\d+) txn from mempool"),
        re.compile(r"(?P<num_requested>\d+) txn requested"),
    }

    _tip_patts = {
        re.compile(fr"best=(?P<blockhash>{_HASH}) "),
        re.compile(r"date='(?P<blocktime>\S+)'"),
    }

    def __init__(self) -> None:
        self.next_event = None

    def process_line(self, line: str) -> t.Optional[models.HeaderToTipEvent]:
        if "Saw new header" in line:
            matches = self._match(self._header_patts, line)
            timestamp = get_time(line)

            if self.next_event:
                log.error("Interrupting header-to-tip measurement",
                          extra={"old": self.next_event})

            self.next_event = models.HeaderToTipEvent()
            self.next_event.blockhash = matches["blockhash"]
            self.next_event.height = int(matches["height"])
            self.next_event.saw_header_at = timestamp

        if not self.next_event:
            return

        if "Successfully reconstructed block" in line:
            if not self.next_event:
                return
            matches = self._match(self._reconstruct_patts, line)
            timestamp = get_time(line)
            if not self.next_event.blockhash == matches.get('blockhash'):
                log.error("reconstruction blockhash mismatch",
                          extra={'event': self.next_event, 'matches': matches})
                return

            matches.pop('blockhash')
            self.next_event.reconstruct_block_at = timestamp
            self.next_event.header_to_block_secs = (
                self.next_event.reconstruct_block_at - self.next_event.saw_header_at
            ).total_seconds()
            self.next_event.reconstruction_data = matches

        elif "UpdateTip: " in line:
            if not self.next_event:
                return
            matches = self._match(self._tip_patts, line)
            timestamp = get_time(line)
            if not self.next_event.blockhash == matches.get('blockhash'):
                log.error("reconstruction blockhash mismatch",
                          extra={'event': self.next_event, 'matches': matches})
                return

            matches.pop('blockhash')
            self.next_event.tip_at = timestamp
            self.next_event.header_to_tip_secs = (
                timestamp - self.next_event.saw_header_at).total_seconds()

            if self.next_event.reconstruct_block_at:
                self.next_event.block_to_tip_secs = (
                    timestamp - self.next_event.reconstruct_block_at).total_seconds()

            block_timestamp = parse_datetime(matches['blocktime'])
            self.next_event.blocktime_minus_header_secs = (
                block_timestamp - self.next_event.saw_header_at).total_seconds()

            this_event = self.next_event
            self.next_event = None
            return this_event

        return None


"""
# TODO


## searching for "invalid" logs

LogPrint(BCLog::MEMPOOL, "   invalid orphan tx %s from peer=%d. %s\n",
    orphanHash.ToString(),
    from_peer,
    state.ToString());


LogPrint(BCLog::NET, "peer %d requested invalid block hash: %s\n",
         node.GetId(), stop_hash.ToString());
node.fDisconnect = true;

LogPrint(BCLog::NET, "peer %d sent invalid getcfilters/getcfheaders with " /* Continued */
         "start height %d and stop height %d\n",
         node.GetId(), start_height, stop_height);
node.fDisconnect = true;


LogPrint(BCLog::NET, "peer %d requested too many cfilters/cfheaders: %d / %d\n",
         node.GetId(), stop_height - start_height + 1, max_height_diff);
node.fDisconnect = true;


LogPrintf("%s: Warning: Found invalid chain at least ~6 blocks longer than our best chain.\nChain state d       atabase corruption likely.\n", __func__);


LogPrintf("%s: invalid block=%s  height=%d  log2_work=%f  date=%s\n", __func__,
  pindexNew->GetBlockHash().ToString(), pindexNew->nHeight,
  log(pindexNew->nChainWork.getdouble())/log(2.0), FormatISO8601DateTime(pindexNew->GetBlockTime()));

LogPrint(BCLog::VALIDATION, "%s: %s prev block not found\n", __func__, hash.ToString());
return state.Invalid(BlockValidationResult::BLOCK_MISSING_PREV, "prev-blk-not-found");


LogPrint(BCLog::VALIDATION, "%s: %s prev block invalid\n", __func__, hash.ToString());

LogPrint(BCLog::VALIDATION, "%s: not adding new block header %s, missing anti-dos proof-of-work validation", __func__, hash.ToString());



## TODO searching for "warning" logs

### Operational error?

LogPrintf("Warning: Could not open blocks file %s\n", fs::PathToString(path));



---

Expired {count} transactions from the memory pool

Regular block reception (GotBlockEvent)
Compact block reception (GotBlockEvent)

Valid fork found
-------------------
Warning: Large valid fork found
  forking the chain at height %d ({blockhash})
  lasting to height %d ({blockhash}).
Chain state database corruption likely.

Invalid chain found
-------------------
Warning: Found invalid chain at least ~6 blocks longer than our best chain.

Invalid block found
-------------------
{funcname}: invalid block={blockhash}  height={height}  log2_work={work}  date={block_datetime}

Compact blocks
-------------------
# Successfully reconstructed block {blockhash} with {n} txn prefilled, {n} txn from mempool (incl at least {n} from extra pool) and {n} txn requested


# replacing tx %s with %s for %s BTC additional fees, %d delta bytes
"""
