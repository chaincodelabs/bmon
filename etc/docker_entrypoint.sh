#!/bin/bash
set -e

cd /src
pipenv install --system

exec "$@"
