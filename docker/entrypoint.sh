#!/bin/bash
set -e

if ! [ -z "${WAIT_FOR}" ]; then
  # wait-for detects/uses the above envvar.
  /bin/wait-for 
fi

cd /src

exec "$@"
