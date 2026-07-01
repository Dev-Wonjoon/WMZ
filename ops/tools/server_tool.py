#!/usr/bin/env python3
"""Build deploy server instances from manifests, vendor jars, and templates."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from config_tool import ROOT, rel, safe_relative_input
from manifest_tool import DEPLOY_DIR, manifest_path


TOOLS_DIR = Path(__file__).resolve().parent
MANIFEST_TOOL = TOOLS_DIR / "manifest_tool.py"
CONFIG_TOOL = TOOLS_DIR / "config_tool.py"


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def run_tool(args: list[str]) -> None:
    command = [sys.executable, *args]
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode:
        raise SystemExit(result.returncode)


def simple_name(name: str, label: str) -> str:
    if not name or "/" in name or "\\" in name:
        fail(f"{label} must be a simple name without slashes")
    return name[:-4] if name.endswith(".yml") else name


def deploy_target(server: str, target: str | None = None) -> Path:
    if target:
        path = Path(target)
        if not path.is_absolute():
            path = ROOT / path
        return path.resolve()
    return DEPLOY_DIR / simple_name(server, "server name")


def manifest_exists(server: str) -> None:
    path = manifest_path(server)
    if not path.exists():
        fail(f"manifest does not exist: {rel(path)}")


def apply_server_command(server: str, target: Path, args: argparse.Namespace) -> None:
    command = [
        str(MANIFEST_TOOL),
        "apply-server",
        server,
        "--target",
        str(target),
    ]
    if args.server_jar:
        command.extend(["--server", args.server_jar])
    if args.auto_single_server:
        command.append("--auto-single")
    if args.optional_server:
        command.append("--optional")
    if args.dry_run:
        command.append("--dry-run")
    run_tool(command)


def apply_plugins_command(server: str, target: Path, args: argparse.Namespace) -> None:
    command = [
        str(MANIFEST_TOOL),
        "apply-plugins",
        server,
        "--target",
        str(target),
    ]
    if args.dry_run:
        command.append("--dry-run")
    if args.prune:
        command.append("--prune")
    if args.check_hashes:
        command.append("--check-hashes")
    if args.verbose:
        command.append("--verbose")
    run_tool(command)


def apply_config_command(server: str, target: Path, args: argparse.Namespace) -> None:
    paths = [str(safe_relative_input(path, "config path")) for path in args.paths]
    command = [
        str(CONFIG_TOOL),
        "apply",
        server,
        *paths,
        "--target",
        str(target),
    ]
    if args.dry_run:
        command.append("--dry-run")
    if args.allow_missing:
        command.append("--allow-missing")
    run_tool(command)


def command_build(args: argparse.Namespace) -> None:
    server = simple_name(args.server, "server name")
    manifest_exists(server)
    target = deploy_target(server, args.target)
    verb = "would build" if args.dry_run else "building"
    print(f"{verb} {server} -> {rel(target)}", flush=True)

    apply_server_command(server, target, args)
    apply_plugins_command(server, target, args)
    if not args.no_config:
        apply_config_command(server, target, args)


def command_check(args: argparse.Namespace) -> None:
    args.dry_run = True
    command_build(args)


def command_path(args: argparse.Namespace) -> None:
    print(rel(deploy_target(args.server, args.target)))


def command_show(args: argparse.Namespace) -> None:
    server = simple_name(args.server, "server name")
    manifest_exists(server)
    print(f"deploy target: {rel(deploy_target(server, args.target))}", flush=True)
    run_tool([str(MANIFEST_TOOL), "show", server])


def add_build_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("server", help="server manifest name, e.g. survival")
    parser.add_argument(
        "paths",
        nargs="*",
        help="optional template-relative config paths to apply",
    )
    parser.add_argument(
        "--target",
        help="deploy target root; default is deploy/<server>",
    )
    parser.add_argument(
        "--server",
        dest="server_jar",
        help="server jar file name under ops/vendor/server; overrides manifest server",
    )
    parser.add_argument(
        "--no-auto-server",
        action="store_false",
        dest="auto_single_server",
        help="do not auto-use the only jar in ops/vendor/server",
    )
    parser.set_defaults(auto_single_server=True)
    parser.add_argument(
        "--optional-server",
        action="store_true",
        help="skip server.jar instead of failing when no server jar is selected",
    )
    parser.add_argument(
        "--no-config",
        action="store_true",
        help="copy server.jar and plugin jars without applying templates",
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help="remove stale plugin jars from the target plugins directory",
    )
    parser.add_argument(
        "--check-hashes",
        action="store_true",
        help="require and verify plugin sha256 values before copying",
    )
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="leave missing ${NAME} placeholders unchanged while applying config",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show changes without writing files",
    )
    parser.add_argument("--verbose", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build deploy server instances.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser(
        "build",
        help="assemble deploy/<server> from server jar, plugin jars, and templates",
    )
    add_build_options(p_build)
    p_build.set_defaults(func=command_build)

    p_check = sub.add_parser(
        "check",
        help="dry-run build for a deploy server instance",
    )
    add_build_options(p_check)
    p_check.set_defaults(func=command_check)

    p_path = sub.add_parser("path", help="print deploy target path")
    p_path.add_argument("server", help="server manifest name, e.g. survival")
    p_path.add_argument("--target", help="deploy target override")
    p_path.set_defaults(func=command_path)

    p_show = sub.add_parser("show", help="show deploy target and manifest summary")
    p_show.add_argument("server", help="server manifest name, e.g. survival")
    p_show.add_argument("--target", help="deploy target override")
    p_show.set_defaults(func=command_show)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
