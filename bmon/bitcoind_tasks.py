"""
Tasks that are run on each bitcoind hosts. These tasks consume from a queue that is
based on the redis instance local to this host, but often push into the server's queue.
"""
import os
import datetime
import subprocess
import time
import fastavro
import logging

import walrus
import django
import google.cloud.storage
from django.conf import settings
from django.forms.models import model_to_dict
from huey import RedisHuey, crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bmon.settings")
django.setup()

from bmon import server_tasks, logparse, models

log = logging.getLogger(__name__)

redisdb = walrus.Database.from_url(
    settings.REDIS_LOCAL_URL, decode_responses=True
)

events_q = RedisHuey("bmon-bitcoind-events", url=settings.REDIS_LOCAL_URL)

# The mempool queue is special-cased because it's so high volume, we don't want it
# starving other queues.
mempool_q = RedisHuey("bmon-mempool-events", url=settings.REDIS_LOCAL_URL)


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


# Coordinate mempool activity with a lock since we're writing out to a single file.
mempool_activity_lock = redisdb.lock("mempool-activity", ttl=1_000)
mempool_ship_lock = redisdb.lock("mempool-log-ship")

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
        "moved mempool activity file %s to %s for shipment", CURRENT_MEMPOOL_FILE, shipfile
    )
    redisdb[LAST_SHIPPED_KEY] = time.time()
    ship_activity()


@mempool_q.task()
def ship_activity():
    """Send mempool activity file to a remote server."""
    with mempool_ship_lock:
        client = google.cloud.storage.Client.from_service_account_json(
            settings.CHAINCODE_GCP_CRED_PATH)
        bucket = client.get_bucket(settings.CHAINCODE_GCP_BUCKET)

        for shipfile in settings.MEMPOOL_ACTIVITY_CACHE_PATH.glob('to-ship*'):
            timestr = shipfile.name.split('.')[1].replace(':', '-')
            target = f"{settings.HOSTNAME}.{timestr}.avro"
            d = bucket.blob(target)
            d.upload_from_filename(shipfile)

            moved = settings.MEMPOOL_ACTIVITY_CACHE_PATH / f'shipped.{timestr}.avro'
            subprocess.check_call(f"mv {shipfile} {moved}", shell=True)
            log.info("pushed mempool activity %s to Chaincode GCP", shipfile)


LOG_LISTENERS = (
    logparse.ConnectBlockListener(),
    logparse.MempoolListener(),
)


def watch_bitcoind_logs():
    """
    Continuously watch a bitcoind debug.log and route events to async handlers.

    Used as an entrypoint; see `pyproject.toml`.
    """
    filename = settings.BITCOIND_LOG_PATH
    assert filename
    log.info(f"listening to logs at {filename}")

    log_progress = models.LogProgress.objects.filter(host=settings.HOSTNAME).first()
    start_log_cursor = log_progress.loghash if log_progress else None

    for line in logparse.read_logfile_forever(filename, start_log_cursor):
        process_line(line)


def process_line(line: str):
    linehash = logparse.linehash(line)

    for listener in LOG_LISTENERS:
        try:
            got = listener.process_line(line)
        except Exception:
            log.exception("Listener %s failed to process line %r", listener, line)
            models.ProcessLineError.objects.create(
                host=settings.HOSTNAME,
                listener=listener.__class__.__name__,
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

            if got.event_type == "mempool":
                mempool_activity(got.avro_record(), linehash)
            else:
                d = model_to_dict(got)
                d["_model"] = got.__class__.__name__

                send_event(d, linehash)

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
