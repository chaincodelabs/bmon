from functools import lru_cache

from django.conf import settings
from walrus import Walrus


@lru_cache
def get_redis():
    return Walrus(
        host=(settings.REDIS_LOCAL_HOST or settings.REDIS_SERVER_HOST), port=6379, db=0
    )
