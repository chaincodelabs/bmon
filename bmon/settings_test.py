from bmon.settings import *  # noqa
from bmon import settings

DEBUG = True
TESTING = True

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    },
}

REDIS_LOCAL_URL = "redis://redis:6379/11"
REDIS_SERVER_URL = "redis://redis:6379/10"
REDIS_HOST = "redis"
REDIS_LOCAL_HOST = "redis"

HOSTNAME = "test"

BITCOIN_DBCACHE = settings.BITCOIN_DBCACHE or '1000'
BITCOIN_PRUNE = settings.BITCOIN_PRUNE or '550'
