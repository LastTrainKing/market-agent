#!/usr/bin/env bash
cd "$(dirname "$0")"
exec python server.py "$@"
