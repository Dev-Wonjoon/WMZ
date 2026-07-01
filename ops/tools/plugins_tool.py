#!/usr/bin/env python3
"""Small convenience wrapper for plugin deployment commands."""

from __future__ import annotations

import sys

from manifest_tool import main as manifest_main


USAGE = """usage:
  plugins_tool.py apply <manifest> [--dry-run] [--prune] [--check-hashes] [--verbose]

This wrapper delegates to manifest_tool.py apply-plugins.
"""


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help", "help"):
        print(USAGE.rstrip())
        return 0

    command = args.pop(0)
    if command == "apply":
        return manifest_main(["apply-plugins", *args])

    print(f"error: unknown command: {command}", file=sys.stderr)
    print(USAGE.rstrip(), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
