#!/bin/bash
set -e

if ! [ -z "${WAIT_FOR}" ]; then
  # wait-for detects/uses the above envvar.
  /bin/wait-for 
fi

if ! [ -z "${RUN_DB_MIGRATIONS}" ]; then
  python manage.py migrate
  python manage.py collectstatic --noinput
fi

cd /src

exec "$@"
