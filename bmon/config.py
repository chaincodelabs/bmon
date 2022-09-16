import os


DATABASE_URL = os.environ.get(
    'BMON_DATABASE_URL',
    'postgres://james:foobar@localhost:5432/bmon')

ZMQ_PORT = os.environ.get(
    'BMON_ZMQ_PORT',
    '5555')

ZMQ_SERVER_HOSTNAME = os.environ.get(
    'BMON_ZMQ_SERVER_HOSTNAME',
    'receiver')

TESTING = bool(os.environ.get(
    'BMON_TESTING',
    False))


# Only bitcoind-monitoring nodes have a local Redis cache.
REDIS_LOCAL_URL = os.environ.get('BMON_REDIS_LOCAL_URL')

# All installations must know about the central Redis instance.
REDIS_CENTRAL_URL = os.environ.get('BMON_REDIS_CENTRAL_URL')

# For testing
LOCALHOST_AUTH_TOKEN = '4396049cdfe946f88ec63da115cbcfcf'

BITCOIND_LOG_PATH = os.environ.get('BMON_BITCOIND_LOG_PATH')
