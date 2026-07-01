#!/bin/sh
set -eu

if [ "$#" -lt 1 ]; then
  echo "usage: _run_tool.sh <tool.py> [args...]" >&2
  exit 2
fi

script_dir="$(CDPATH= cd "$(dirname "$0")" && pwd -P)"
ops_dir="$(cd "$script_dir/.." && pwd -P)"
tool="$ops_dir/tools/$1"
shift

if [ ! -f "$tool" ]; then
  echo "Tool was not found: $tool" >&2
  exit 1
fi

python=""

try_python() {
  candidate="$1"
  if [ -z "$candidate" ]; then
    return 1
  fi

  if [ -x "$candidate" ] && "$candidate" -c "import sys" >/dev/null 2>&1; then
    python="$candidate"
    return 0
  fi

  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c "import sys" >/dev/null 2>&1; then
    python="$candidate"
    return 0
  fi

  return 1
}

if [ -n "${PYTHON:-}" ]; then
  try_python "$PYTHON" || true
fi

if [ -z "$python" ]; then try_python "python3" || true; fi
if [ -z "$python" ]; then try_python "python" || true; fi
if [ -z "$python" ]; then try_python "py" || true; fi
if [ -z "$python" ]; then try_python "$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python" || true; fi
if [ -z "$python" ]; then try_python "$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe" || true; fi

if [ -z "$python" ]; then
  echo "Python was not found. Install Python or set the PYTHON environment variable." >&2
  exit 1
fi

exec "$python" "$tool" "$@"
