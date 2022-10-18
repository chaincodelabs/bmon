#!/bin/bash
set -e
rm -rf /build/*
yarn 
exec "$@"
