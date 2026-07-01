#!/bin/sh
set -eu

script_dir="$(CDPATH= cd "$(dirname "$0")" && pwd -P)"
exec "$script_dir/_run_tool.sh" plugins_tool.py "$@"
