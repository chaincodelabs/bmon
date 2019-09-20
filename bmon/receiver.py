"""
Recieves events over a ZMQ socket and writes them into Redis.

A separate process drains Redis -> postgresql.
"""

import time
import logging
import os

import zmq
from redis import Redis

logger = logging.getLogger(__name__)


ZMQ_PORT = os.environ.get('BMON_ZMQ_PORT', '5555')
REDIS_HOST = os.environ.get('BMON_REDIS_HOST', 'redis')
REDIS_PORT = os.environ.get('BMON_REDIS_PORT', '6379')
REDIS_DB = int(os.environ.get('BMON_REDIS_DB', '0'))
REDIS_LIST_KEY = b'incoming_events'


def zmq_to_redis():
    context = zmq.Context()
    socket = context.socket(zmq.REP)
    socket.bind(f"tcp://0.0.0.0:{ZMQ_PORT}")

    redis = Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB)

    while True:
        msg = socket.recv()
        redis.rpush(REDIS_LIST_KEY, msg)
        logger.debug("pushed msg %r to redis", msg)


def redis_to_postgres():
    redis = Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB)

    while True:
        next_to_write = redis.lpop(REDIS_LIST_KEY)
        bytes_to_event_db(next_to_write)


def bytes_to_event_db(json_bytes: bytes):
    try:
        deser = json.loads(json_bytes.decode('utf8'))
    except Exception:
        logger.exception("couldn't deserialize from redis: %r", json_bytes)
        return

    if '_event_name' not in keys:
        logger.warning(f"message lacks event type: '{deser}'")
    if '_auth_token' not in keys:
        logger.warning(f"message lacks auth token: '{deser}'")

    tablename = deser.pop(
