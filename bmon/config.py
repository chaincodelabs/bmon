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

# For testing
LOCALHOST_AUTH_TOKEN = '4396049cdfe946f88ec63da115cbcfcf'
