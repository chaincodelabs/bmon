from bmon.settings import *  # noqa

DEBUG = True
TESTING = True

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    },
}
