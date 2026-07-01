#!/usr/bin/env python3
"""Create bootstrap and build-test workbench directories."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from config_tool import ROOT, WORKBENCH_DIR, rel, simple_name


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


def bootstrap_path(layer: str) -> Path:
    return WORKBENCH_DIR / "bootstrap" / simple_name(layer, "template layer")


def build_path(name: str) -> Path:
    return WORKBENCH_DIR / "build" / simple_name(name, "build workbench name")


def local_instance_path(scope: str, name: str) -> Path:
    safe_name = simple_name(name, f"{scope} workbench name")
    if scope == "bootstrap":
        return bootstrap_path(safe_name)
    if scope == "build":
        return build_path(safe_name)
    fail(f"unknown local run scope: {scope}")


def command_bootstrap_path(args: argparse.Namespace) -> None:
    print(rel(bootstrap_path(args.layer)))


def command_build_path(args: argparse.Namespace) -> None:
    name = args.name or args.manifest
    print(rel(build_path(name)))


def command_import_bootstrap(args: argparse.Namespace) -> None:
    layer = simple_name(args.layer, "template layer")
    source_name = f"bootstrap/{layer}"
    command = [
        str(CONFIG_TOOL),
        "import",
        source_name,
        layer,
        *args.paths,
        "--from",
        "workbench",
    ]
    if args.dry_run:
        command.append("--dry-run")
    if args.include_runtime:
        command.append("--include-runtime")
    run_tool(command)


def apply_server_command(
    manifest: str,
    target: Path,
    args: argparse.Namespace,
) -> None:
    command = [
        str(MANIFEST_TOOL),
        "apply-server",
        manifest,
        "--target",
        str(target),
    ]
    if getattr(args, "server", None):
        command.extend(["--server", args.server])
    if getattr(args, "auto_single_server", False):
        command.append("--auto-single")
    if args.dry_run:
        command.append("--dry-run")
    if not getattr(args, "require_server", False):
        command.append("--optional")
    run_tool(command)


def apply_plugins_command(
    manifest: str,
    target: Path,
    args: argparse.Namespace,
) -> None:
    command = [
        str(MANIFEST_TOOL),
        "apply-plugins",
        manifest,
        "--target",
        str(target),
    ]
    if args.dry_run:
        command.append("--dry-run")
    if getattr(args, "prune", False):
        command.append("--prune")
    if getattr(args, "check_hashes", False):
        command.append("--check-hashes")
    if getattr(args, "verbose", False):
        command.append("--verbose")
    run_tool(command)


def command_bootstrap(args: argparse.Namespace) -> None:
    layer = simple_name(args.layer, "template layer")
    manifest = simple_name(args.manifest or layer, "manifest name")
    target = bootstrap_path(layer)
    print(
        f"{'would bootstrap' if args.dry_run else 'bootstrapping'} {layer} "
        f"from {manifest} -> {rel(target)}",
        flush=True,
    )

    apply_server_command(manifest, target, args)
    apply_plugins_command(manifest, target, args)


def command_assemble(args: argparse.Namespace) -> None:
    manifest = simple_name(args.manifest, "manifest name")
    target = build_path(args.name or manifest)
    print(
        f"{'would assemble' if args.dry_run else 'assembling'} {manifest} -> {rel(target)}",
        flush=True,
    )

    apply_server_command(manifest, target, args)
    apply_plugins_command(manifest, target, args)

    config_command = [
        str(CONFIG_TOOL),
        "apply",
        manifest,
        "--target",
        str(target),
    ]
    if args.dry_run:
        config_command.append("--dry-run")
    if args.allow_missing:
        config_command.append("--allow-missing")
    run_tool(config_command)


def stream_process_output(process: subprocess.Popen[str]) -> None:
    assert process.stdout is not None
    for line in process.stdout:
        encoding = sys.stdout.encoding or "utf-8"
        safe_line = line.encode(encoding, errors="replace").decode(
            encoding,
            errors="replace",
        )
        print(safe_line, end="", flush=True)


def command_run_local(args: argparse.Namespace) -> None:
    target = local_instance_path(args.scope, args.name)
    server_jar = target / args.jar

    if not target.exists():
        fail(f"instance directory does not exist: {rel(target)}")
    if not server_jar.is_file():
        fail(f"server jar does not exist: {rel(server_jar)}")

    if args.accept_eula:
        (target / "eula.txt").write_text("eula=true\n", encoding="utf-8")

    java_args = [
        args.java,
        f"-Xms{args.xms}",
        f"-Xmx{args.xmx}",
        "-jar",
        args.jar,
        "nogui",
    ]
    print(f"running {' '.join(java_args)} in {rel(target)}", flush=True)

    process = subprocess.Popen(
        java_args,
        cwd=target,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=os.environ.copy(),
    )
    reader = threading.Thread(
        target=stream_process_output,
        args=(process,),
        daemon=True,
    )
    reader.start()

    try:
        if args.timeout_seconds <= 0:
            return_code = process.wait()
        else:
            deadline = time.monotonic() + args.timeout_seconds
            while process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.25)

            if process.poll() is None:
                print(
                    f"\ntimeout reached after {args.timeout_seconds}s; sending stop",
                    flush=True,
                )
                assert process.stdin is not None
                try:
                    process.stdin.write("stop\n")
                    process.stdin.flush()
                except OSError:
                    pass

                try:
                    return_code = process.wait(timeout=args.stop_grace_seconds)
                except subprocess.TimeoutExpired:
                    print("server did not stop in time; terminating", flush=True)
                    process.terminate()
                    return_code = process.wait(timeout=10)
            else:
                return_code = process.returncode
    finally:
        reader.join(timeout=2)

    if return_code:
        raise SystemExit(return_code)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage config workbenches.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_bootstrap_path = sub.add_parser(
        "bootstrap-path",
        help="print workbench/bootstrap/<layer>",
    )
    p_bootstrap_path.add_argument("layer", help="template layer, e.g. common")
    p_bootstrap_path.set_defaults(func=command_bootstrap_path)

    p_build_path = sub.add_parser("build-path", help="print workbench/build/<name>")
    p_build_path.add_argument("manifest", help="manifest name, e.g. survival")
    p_build_path.add_argument("--name", help="workbench build directory name")
    p_build_path.set_defaults(func=command_build_path)

    p_import_bootstrap = sub.add_parser(
        "import-bootstrap",
        help="import config from workbench/bootstrap/<layer> into ops/templates/<layer>",
    )
    p_import_bootstrap.add_argument("layer", help="template layer, e.g. common")
    p_import_bootstrap.add_argument(
        "paths",
        nargs="*",
        help="optional relative files or directories to import",
    )
    p_import_bootstrap.add_argument("--dry-run", action="store_true")
    p_import_bootstrap.add_argument(
        "--include-runtime",
        action="store_true",
        help="include runtime/state-like files normally excluded from templates",
    )
    p_import_bootstrap.set_defaults(func=command_import_bootstrap)

    p_bootstrap = sub.add_parser(
        "bootstrap",
        help="prepare workbench/bootstrap/<layer> with server.jar and plugins",
    )
    p_bootstrap.add_argument("layer", help="template layer, e.g. common")
    p_bootstrap.add_argument(
        "--manifest",
        help="manifest to use for server jar and plugins; default: layer",
    )
    p_bootstrap.add_argument(
        "--server",
        help="server jar file name under ops/vendor/server; overrides manifest server",
    )
    p_bootstrap.add_argument(
        "--no-auto-server",
        action="store_false",
        dest="auto_single_server",
        help="do not auto-use the only jar in ops/vendor/server",
    )
    p_bootstrap.set_defaults(auto_single_server=True)
    p_bootstrap.add_argument("--dry-run", action="store_true")
    p_bootstrap.add_argument("--prune", action="store_true")
    p_bootstrap.add_argument("--check-hashes", action="store_true")
    p_bootstrap.add_argument(
        "--require-server",
        action="store_true",
        help="fail when the manifest has no server jar selected",
    )
    p_bootstrap.add_argument("--verbose", action="store_true")
    p_bootstrap.set_defaults(func=command_bootstrap)

    p_assemble = sub.add_parser(
        "assemble",
        help=(
            "assemble manifest server.jar, plugins, and templates into "
            "workbench/build/<name>"
        ),
    )
    p_assemble.add_argument("manifest", help="manifest name, e.g. survival")
    p_assemble.add_argument("--name", help="workbench build directory name")
    p_assemble.add_argument("--dry-run", action="store_true")
    p_assemble.add_argument("--prune", action="store_true")
    p_assemble.add_argument("--check-hashes", action="store_true")
    p_assemble.add_argument("--allow-missing", action="store_true")
    p_assemble.add_argument(
        "--require-server",
        action="store_true",
        help="fail when the manifest has no server jar selected",
    )
    p_assemble.add_argument("--verbose", action="store_true")
    p_assemble.set_defaults(func=command_assemble)

    p_run_local = sub.add_parser(
        "run-local",
        help="run a local bootstrap/build instance with java -jar server.jar nogui",
    )
    p_run_local.add_argument(
        "scope",
        choices=("bootstrap", "build"),
        help="local instance group to run",
    )
    p_run_local.add_argument("name", help="instance name, e.g. common or survival")
    p_run_local.add_argument(
        "--jar",
        default="server.jar",
        help="server jar name inside the instance directory",
    )
    p_run_local.add_argument("--java", default="java", help="java executable")
    p_run_local.add_argument("--xms", default="1G", help="initial heap size")
    p_run_local.add_argument("--xmx", default="2G", help="maximum heap size")
    p_run_local.add_argument(
        "--timeout-seconds",
        type=int,
        default=90,
        help="seconds to run before sending stop; use 0 to run until exit",
    )
    p_run_local.add_argument(
        "--stop-grace-seconds",
        type=int,
        default=30,
        help="seconds to wait after stop before terminating",
    )
    p_run_local.add_argument(
        "--accept-eula",
        action="store_true",
        help="write eula.txt with eula=true before starting",
    )
    p_run_local.set_defaults(func=command_run_local)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
