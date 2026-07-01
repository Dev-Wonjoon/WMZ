#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: _run_tool.sh <tool.py> [args...]" >&2
  exit 2
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ops_dir="$(cd -- "$script_dir/.." && pwd -P)"
tool="$ops_dir/tools/$1"
shift

if [[ ! -f "$tool" ]]; then
  echo "Tool was not found: $tool" >&2
  exit 1
fi

candidates=()
if [[ -n "${PYTHON:-}" ]]; then
  candidates+=("$PYTHON")
fi
candidates+=("python3" "python" "py")
candidates+=("$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python")
candidates+=("$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe")

python=""
for candidate in "${candidates[@]}"; do
  if [[ -z "$candidate" ]]; then
    continue
  fi

  if [[ -x "$candidate" ]] && "$candidate" -c "import sys" >/dev/null 2>&1; then
    python="$candidate"
    break
  fi

  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c "import sys" >/dev/null 2>&1; then
    python="$candidate"
    break
  fi
done

if [[ -z "$python" ]]; then
  echo "Python was not found. Install Python or set the PYTHON environment variable." >&2
  exit 1
fi

exec "$python" "$tool" "$@"
