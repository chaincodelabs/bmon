import logging

import redis

log = logging.getLogger(__name__)


def try_set_key(
    redisdb: redis.Redis, keyname: str, content: str | float | int, **kwargs
) -> bool:
    """
    Ensure we set a key in Redis, retrying if necessary.
    """
    tries = 3
    while tries > 0:
        if redisdb.set(keyname, content, **kwargs):
            return True

        log.error("failed to set key %s; retrying", keyname)
        tries -= 1

    log.error("failed to set key %s", keyname)
    return False
