"""
Tasks that are run on each bitcoind hosts. These tasks consume from a queue that is
based on the redis instance local to this host, but often push into the server's queue.
"""
import os
import datetime
import subprocess
import time
import logging
import json
import multiprocessing
import typing as t

import fastavro
import walrus
import django
import google.cloud.storage
from django.conf import settings
from django.forms.models import model_to_dict
from huey import RedisHuey, crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bmon.settings")
django.setup()

from bmon import server_tasks, logparse, models, util, bitcoin
from bmon.bitcoin.api import get_rpc


log = logging.getLogger(__name__)

redisdb = walrus.Database.from_url(settings.REDIS_LOCAL_URL, decode_responses=True)

events_q = RedisHuey(
    "bmon-bitcoind-events", url=settings.REDIS_LOCAL_URL, immediate=settings.TESTING,
    duration_warn=5,  # warn if task takes longer than 5 seconds
)

# The mempool queue is special-cased because it's so high volume, we don't want it
# starving other queues.
mempool_q = RedisHuey(
    "bmon-mempool-events", url=settings.REDIS_LOCAL_URL, immediate=settings.TESTING,
    duration_warn=5,  # warn if task takes longer than 5 seconds
)


@events_q.task()
def send_event(event: dict, linehash: str):
    print(f"Sending event to the aggregator: {event}")
    server_tasks.persist_bitcoind_event(event, linehash)


logfile_pos = logparse.LogfilePosManager(settings.HOSTNAME, redisdb)


@events_q.periodic_task(crontab(minute="*"))
def write_logfile_pos():
    """
    Periodically write out logfile position since we can't always do this synchronously,
    e.g. with mempool activity, since it would overwhelm the db.
    """
    logfile_pos.flush()


# ID to peerinfo
peerinfo_cache = redisdb.Hash("peerinfo")

# A map of bitcoind peer IDs to the current bmon Peer ID; new bmon Peer models are
# created if the underlying bitcoind peer changes substantially (e.g. subver).
peer_id_map = redisdb.Hash("peer_id_map")


def get_bmon_peer_id(bitcoind_peer_id: int) -> int:
    """
    Return the bmon.models.Peer id for a bitcoind peer.

    TODO needs tests

    Syncs the peer cache if necessary.
    """
    if bitcoind_peer_id not in peer_id_map:
        result = sync_peer_data(bitcoind_peer_id)
        result(blocking=True)

    if bitcoind_peer_id not in peer_id_map:
        raise RuntimeError(
            "can't find bmon peer ID for bitcoind peer %d", bitcoind_peer_id
        )

    return int(peer_id_map[bitcoind_peer_id])


@events_q.periodic_task(crontab(minute="*"))
@events_q.lock_task('update-peers')
def sync_peer_data(peer_id: None | int = None):
    sync_peer_data_blocking(peer_id)


@events_q.periodic_task(crontab(minute="*"))
def compute_peer_stats():
    compute_peer_stats_blocking()


def compute_peer_stats_blocking(peerinfo: list[dict] | None = None):
    try:
        peerinfo = peerinfo or get_rpc().getpeerinfo()
    except Exception:
        log.exception("failed to get peerinfo")
        return

    if not peerinfo:
        log.warning("exiting early - no peerinfo")
        return

    minping = 1000000.0
    maxping = 0.0
    pingsum = 0.0
    bytesrecv = 0
    bytessent = 0
    recv_per_msg = {}
    sent_per_msg = {}

    for p in peerinfo:
        # Pingtime is conditionally included in nodes <= v0.19.0
        if 'pingtime' not in p:
            continue
        pingtime = float(p["pingtime"])
        if pingtime < minping:
            minping = pingtime
        if pingtime > maxping:
            maxping = pingtime
        pingsum += pingtime

        bytesrecv += p["bytesrecv"]
        bytessent += p["bytessent"]

        for msg, val in p["bytesrecv_per_msg"].items():
            if msg not in recv_per_msg:
                recv_per_msg[msg] = 0
            recv_per_msg[msg] += val

        for msg, val in p["bytessent_per_msg"].items():
            if msg not in sent_per_msg:
                sent_per_msg[msg] = 0
            sent_per_msg[msg] += val

    meanping = pingsum / len(peerinfo)

    return models.PeerStats.objects.create(
        host=get_latest_host(),
        num_peers=len(peerinfo),
        ping_min=minping,
        ping_max=maxping,
        ping_mean=meanping,
        bytesrecv=bytesrecv,
        bytessent=bytessent,
        bytesrecv_per_msg=recv_per_msg,
        bytessent_per_msg=sent_per_msg,
    )


def sync_peer_data_blocking(peer_id: None | int = None) -> dict[int, int]:
    """
    Periodically cache our getpeerinfo in redis.

    TODO needs tests

    Kwargs:
        peer_id: if given, only process this peer ID

    Returns:
        A map of newly cached bitcoind peer ids to bmon Peer ids.
    """
    with redisdb.lock('peers'):
        log.info("syncing peer data (peer_id=%s)", peer_id)
        try:
            peerinfo = get_rpc().getpeerinfo()
        except Exception:
            log.exception("failed to get peerinfo")
            return {}

        for peer in peerinfo:
            if "addr" not in peer or "id" not in peer:
                log.warning("malformed peer, skipping: %s", peer)
                continue

            peerinfo_cache[peer["id"]] = util.json_dumps(peer)

        if peer_id is not None:
            peerinfo = [p for p in peerinfo if p["id"] == peer_id]

        return commit_peers_db(peerinfo)


def commit_peers_db(peerinfo: list[dict]) -> dict[int, int]:
    """
    Sync getpeerinfo output with the database.
    """
    new_ids = {}
    for peer in peerinfo:
        kwargs, defaults = models.Peer.peerinfo_data(peer)
        try:
            obj, created = models.Peer.objects.get_or_create(defaults=defaults, **kwargs)
        except models.Peer.MultipleObjectsReturned:
            qs = models.Peer.objects.filter(**kwargs).order_by('-id')
            latest = qs.first()
            assert latest
            deleted = qs.exclude(id=latest.id).delete()
            log.warning("deleted %s duplicate Peers", deleted)

            obj, created = models.Peer.objects.get_or_create(defaults=defaults, **kwargs)

        if created:
            log.info("synced peer %d (num=%d) to database: %s", obj.id, obj.num, kwargs)
            peer_id_map[obj.num] = obj.id
            new_ids[obj.num] = obj.id

    return new_ids


# Coordinate mempool activity with a lock since we're writing out to a single file.
mempool_activity_lock = redisdb.lock("mempool-activity", ttl=1_000)
mempool_ship_lock = redisdb.lock("mempool-log-ship", ttl=(1_000 * 60 * 8))

LAST_SHIPPED_KEY = "mempool.last_shipped"
CURRENT_MEMPOOL_FILE = settings.MEMPOOL_ACTIVITY_CACHE_PATH / "current"


@mempool_q.task()
def mempool_activity(avro_data: dict, linehash: str):
    """
    Persist some mempool activity in the local cache; ship them off to
    some remote server periodically.
    """
    SHIP_LOGS_EVERY_MINUTES = 120

    with mempool_activity_lock:
        logfile_pos.mark(linehash)

        mode = "a+b"
        if not CURRENT_MEMPOOL_FILE.exists():
            mode = "wb"

        with open(CURRENT_MEMPOOL_FILE, mode) as out:
            fastavro.writer(out, models.mempool_activity_avro_schema, [avro_data])

        last_shipped = redisdb.get(LAST_SHIPPED_KEY)
        now = time.time()

        if not last_shipped:
            redisdb[LAST_SHIPPED_KEY] = time.time()
        elif (now - (SHIP_LOGS_EVERY_MINUTES * 60)) >= float(last_shipped):
            queue_mempool_to_ship()


def queue_mempool_to_ship():
    now_str = datetime.datetime.now().isoformat()
    shipfile = settings.MEMPOOL_ACTIVITY_CACHE_PATH / f"to-ship.{now_str}.avro"
    subprocess.check_call(f"mv {CURRENT_MEMPOOL_FILE} {shipfile}", shell=True)
    log.info(
        "moved mempool activity file %s to %s for shipment",
        CURRENT_MEMPOOL_FILE,
        shipfile,
    )
    redisdb[LAST_SHIPPED_KEY] = time.time()
    ship_mempool_activity()


def construct_gcp_path_from_datetime_str(dt):
    d = datetime.datetime.fromisoformat(dt).strftime('%Y-%m-%d')
    dt_formatted = dt.replace(':', '-')
    return f"mempool_events/source=bmon/dt={d}/{settings.HOSTNAME}.{dt_formatted}.avro"


@mempool_q.task()
def ship_mempool_activity():
    """Send mempool activity file to a remote server."""
    if mempool_ship_lock.acquire(block=False):
        try:
            client = google.cloud.storage.Client.from_service_account_json(
                settings.CHAINCODE_GCP_CRED_PATH
            )
            bucket = client.get_bucket(settings.CHAINCODE_GCP_BUCKET)

            for shipfile in settings.MEMPOOL_ACTIVITY_CACHE_PATH.glob("to-ship*"):
                timestr = shipfile.name.split(".")[1]
                target = construct_gcp_path_from_datetime_str(timestr)
                d = bucket.blob(target)
                d.upload_from_filename(shipfile)

                moved = settings.MEMPOOL_ACTIVITY_CACHE_PATH / f"shipped.{timestr}.avro"
                subprocess.check_call(f"mv {shipfile} {moved}", shell=True)
                log.info("pushed mempool activity %s to Chaincode GCP", shipfile)
        except Exception:
            mempool_ship_lock.release()
            raise


def watch_bitcoind_logs():
    """
    Continuously watch a bitcoind debug.log and route events to async handlers.

    Used as an entrypoint; see `pyproject.toml`.

    Also runs more general boot-up tasks for the bitcoind host as a whole (e.g.
    logging host details).
    """
    filename = settings.BITCOIND_LOG_PATH
    assert filename
    log.info(f"listening to logs at {filename}")

    sync_peer_data()
    host = create_host_record()
    compute_peer_stats()

    log_progress = models.LogProgress.objects.filter(hostname=host.name).first()
    start_log_cursor = log_progress.loghash if log_progress else None

    for line in logparse.read_logfile_forever(filename, start_log_cursor):
        process_line(line, host)


def get_latest_host() -> models.Host:
    h = models.Host.objects.filter(name=settings.HOSTNAME).order_by("-id").first()
    assert h
    return h


def create_host_record():
    def get_lshw(classn: str) -> dict:
        return json.loads(
            subprocess.check_output(f"lshw -json -class {classn}", shell=True)
        )[0]

    bitcoin_version = bitcoin.api.read_raw_bitcoind_version()

    # TODO hack
    listen = False
    if '-listen=1' in settings.BITCOIN_FLAGS:
        listen = True

    host, created = models.Host.objects.get_or_create(
        name=settings.HOSTNAME,
        cpu_info=get_lshw("processor")["product"],
        memory_bytes=get_lshw("memory")["size"],
        nproc=multiprocessing.cpu_count(),
        bitcoin_version=bitcoin_version,
        bitcoin_gitref=settings.BITCOIN_GITREF,
        bitcoin_gitsha=settings.BITCOIN_GITSHA,
        bitcoin_dbcache=int(settings.BITCOIN_DBCACHE),
        bitcoin_prune=int(settings.BITCOIN_PRUNE),
        bitcoin_extra={
            "flags": settings.BITCOIN_FLAGS,
        },
        bitcoin_listen=listen,
        defaults={
            "region": "",
        },
    )

    if created:
        log.info(f"Created new host record: {host}")
    else:
        log.info(f"Booting with existing host record: {host}")

    return host


ListenerList = t.Sequence[logparse.Listener]

ignore_older_than = None

if not settings.DEBUG:
    ignore_older_than = datetime.timedelta(hours=6)

LOG_LISTENERS: ListenerList = (
    logparse.HeaderToTipListener(),
    logparse.BlockDownloadTimeoutListener(),
    logparse.ConnectBlockListener(),
    logparse.MempoolAcceptListener(ignore_older_than=ignore_older_than),
    logparse.MempoolRejectListener(ignore_older_than=ignore_older_than),
    logparse.BlockConnectedListener(),
    logparse.BlockDisconnectedListener(),
    logparse.ReorgListener(),
    logparse.PongListener(ignore_older_than=datetime.timedelta(minutes=10)),
)


def process_line(
    line: str,
    host: models.Host,
    listeners: None | ListenerList = None,
    modify_log_pos: bool = True,
):
    """
    Process a single bitcoind log line, prompting async tasks when necessary.
    """
    linehash = logparse.linehash(line)
    ls: ListenerList = listeners or LOG_LISTENERS
    assert host

    for listener in ls:
        got = None
        try:
            got = listener.process_line(line)
        except Exception:
            log.exception("Listener %s failed to process line %r", listener, line)
            models.ProcessLineError.objects.create(
                hostname=settings.HOSTNAME,
                listener=listener.__class__.__name__,
                line=line,
            )
            continue

        if got is None:
            continue

        # TODO make this less special casey
        if isinstance(got, models.MempoolAccept):
            got.host = host.name
            mempool_activity(got.avro_record(), linehash)  # type: ignore
            server_tasks.process_mempool_accept(got.txhash, got.timestamp, host.name)
            got = None
            continue

        if isinstance(listener, logparse.PongListener):
            # We got a peer ID, not a model instance.
            assert isinstance(got, int)
            sync_peer_data(got)
            got = None
            continue

        got.host = host
        is_high_volume = got.is_high_volume

        # MempoolAccept too noisy for this log statement
        if not is_high_volume:
            log.info("Got an instance %r from line (%s) %r", got, linehash, line)

        if hasattr(got, 'peer_id') and not is_high_volume:
            # Need to fill out the `peer` foreign key
            peer_id = peer_id_map.get(got.peer_num)
            if not peer_id:
                log.error("peer cache miss: %s", got.peer_num)
                peer_id = sync_peer_data_blocking(got.peer_num).get(got.peer_num)

                if not peer_id:
                    log.error(
                        "unable to find bmon Peer for bitcoind peer %d", got.peer_num
                    )

            if not peer_id:
                # TODO handle this differently? It's conservative to not to allow
                # dangling Peer references, but maybe not the right approach?
                log.error("discarding logline due to lack of peer data: %r", line)
                return None

            got.peer_id = int(peer_id)  # type: ignore

        if isinstance(got, models.ReorgEvent):
            log.error("saw reorg! %s", got)
            util.pushover_notification(
                f"[{host.name}] reorg: height={got.min_height} "
                f"depth={len(got.old_blockhashes)}")

        elif isinstance(got, models.BlockDownloadTimeout):
            util.pushover_notification(
                f"[{host.name}] saw block download timeout for {got.blockhash}")

        elif isinstance(got, models.HeaderToTipEvent):
            if got.header_to_tip_secs > 2:
                util.pushover_notification(
                    f"[{host.name}] slow header-to-tip "
                    f"({got.header_to_tip_secs}) for {got.blockhash}")

        try:
            got.full_clean()
        except Exception:
            log.exception("model %s failed to validate!", got)
            # TODO: stash the bad model somewhere for later processing.
            continue

        d = model_to_dict(got)
        d["_model"] = got.__class__.__name__

        send_event(d, linehash)

        if modify_log_pos:
            # This isn't totally correct because we don't know for a fact that
            # the server actually persisted the event we sent it, but it's
            # okay as a rough approximation.
            #
            # We can't have the server task
            # do this because then we have to store logfile pos redis data
            # in the central server, which would make actually maintaining
            # that redis state slow for bitcoind servers on slow network links.
            #
            # TODO somehow make this truly synchronous with the server.
            logfile_pos.mark(linehash)
            write_logfile_pos()
