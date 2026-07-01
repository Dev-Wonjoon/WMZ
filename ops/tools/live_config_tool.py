#!/usr/bin/env python3
"""Apply plugin config to deploy instances and trigger plugin reload commands."""

from __future__ import annotations

import argparse
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from config_tool import ROOT, TEMPLATE_DIR, rel, resolved_template_layers
from manifest_tool import MANIFEST_DIR, plugin_is_removed, resolve_manifest, strip_version_suffix


TOOLS_DIR = Path(__file__).resolve().parent
CONFIG_TOOL = TOOLS_DIR / "config_tool.py"
DEPLOY_DIR = ROOT / "deploy"


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def simple_name(value: str, label: str) -> str:
    if not value or "/" in value or "\\" in value:
        fail(f"{label} must be a simple name without slashes")
    return value[:-4] if value.endswith(".yml") else value


def loose_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def command_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def run_tool(args: list[str]) -> None:
    result = subprocess.run([sys.executable, *args], cwd=ROOT)
    if result.returncode:
        raise SystemExit(result.returncode)


def manifest_names() -> list[str]:
    if not MANIFEST_DIR.exists():
        return []
    return sorted(path.stem for path in MANIFEST_DIR.glob("*.yml"))


def is_instance_manifest(name: str) -> bool:
    return name != "common" and not name.endswith("-common")


def plugin_entries(server: str) -> list[dict[str, Any]]:
    resolved = resolve_manifest(server)
    return [
        dict(plugin)
        for plugin in resolved.get("plugins", [])
        if isinstance(plugin, dict) and not plugin_is_removed(plugin)
    ]


def matching_plugin(server: str, plugin_name: str) -> dict[str, Any] | None:
    requested = loose_key(plugin_name)
    for plugin in plugin_entries(server):
        candidates = [str(plugin.get("id", ""))]
        file_name = str(plugin.get("file", "")).strip()
        if file_name:
            stem = Path(file_name).stem
            candidates.extend([stem, strip_version_suffix(stem)])
        if requested in {loose_key(candidate) for candidate in candidates if candidate}:
            return plugin
    return None


def safe_relative(value: str, label: str) -> Path:
    path = Path(value)
    if path.is_absolute() or any(part == ".." for part in path.parts) or not path.parts:
        fail(f"{label} must be a relative path without '..': {value}")
    return path


def metadata_paths(plugin: dict[str, Any]) -> list[Path]:
    raw = plugin.get("config")
    if raw is None:
        raw = plugin.get("configs")
    if raw is None:
        return []
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, list):
        values = [str(item) for item in raw]
    else:
        fail(f"plugin {plugin.get('id', '(unknown)')} has invalid config metadata")
    return [safe_relative(value, "plugin config path") for value in values]


def template_plugin_dirs(server: str) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for layer in resolved_template_layers(server):
        plugins_root = TEMPLATE_DIR / layer / "plugins"
        if not plugins_root.is_dir():
            continue
        for path in sorted(plugins_root.iterdir(), key=lambda item: item.name.lower()):
            if path.is_dir():
                result[loose_key(path.name)] = Path("plugins") / path.name
    return result


def config_paths_for(server: str, plugin_name: str, plugin: dict[str, Any]) -> list[Path]:
    paths = metadata_paths(plugin)
    if paths:
        return paths

    dirs = template_plugin_dirs(server)
    candidates = [plugin_name, str(plugin.get("id", ""))]
    file_name = str(plugin.get("file", "")).strip()
    if file_name:
        stem = Path(file_name).stem
        candidates.extend([stem, strip_version_suffix(stem)])

    for candidate in candidates:
        matched = dirs.get(loose_key(candidate))
        if matched:
            return [matched]

    fail(
        f"could not find template config path for {plugin_name} on {server}; "
        "add plugin config metadata to the manifest or create ops/templates/<layer>/plugins/<PluginName>"
    )


def compose_services(compose_file: Path) -> list[str]:
    if not compose_file.exists():
        return []

    services: list[str] = []
    in_services = False
    for raw in compose_file.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        stripped = raw.strip()
        if indent == 0:
            in_services = stripped == "services:"
            continue
        if in_services and indent == 2 and stripped.endswith(":"):
            name = stripped[:-1].strip().strip("'\"")
            if name:
                services.append(name)
    return services


def target_servers(args: argparse.Namespace) -> list[str]:
    if args.servers:
        return [simple_name(server, "server name") for server in args.servers]

    compose = resolve_compose(args.compose_file)
    services = [name for name in compose_services(compose) if (MANIFEST_DIR / f"{name}.yml").exists()]
    if services:
        return services

    return [name for name in manifest_names() if is_instance_manifest(name)]


def resolve_compose(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


def apply_config(server: str, paths: list[Path], args: argparse.Namespace) -> None:
    command = [
        str(CONFIG_TOOL),
        "apply",
        server,
        *[path.as_posix() for path in paths],
        "--target",
        str(DEPLOY_DIR / server),
    ]
    if args.allow_missing:
        command.append("--allow-missing")
    if args.dry_run:
        command.append("--dry-run")
    run_tool(command)


def reload_command(plugin_name: str, args: argparse.Namespace) -> list[str]:
    if args.command:
        return args.command
    return [f"{command_name(plugin_name)} reload"]


def docker_stdin_command(compose_file: Path, service: str, command: str) -> list[str]:
    script = f"printf '%s\\n' {shlex.quote(command)} > /proc/1/fd/0"
    return [
        "docker",
        "compose",
        "-f",
        str(compose_file),
        "exec",
        "-T",
        service,
        "sh",
        "-lc",
        script,
    ]


def send_reload(server: str, commands: list[str], args: argparse.Namespace) -> None:
    compose = resolve_compose(args.compose_file)
    service = args.service or server
    for command in commands:
        docker_command = docker_stdin_command(compose, service, command)
        if args.dry_run or args.print_only:
            print(f"would reload {server}/{service}: {command}")
            print("  " + " ".join(shlex.quote(part) for part in docker_command))
            continue
        print(f"reloading {server}/{service}: {command}", flush=True)
        result = subprocess.run(docker_command, cwd=ROOT)
        if result.returncode:
            raise SystemExit(result.returncode)


def command_push(args: argparse.Namespace) -> None:
    plugin_name = args.plugin
    servers = target_servers(args)
    if not servers:
        fail("no target servers found")

    planned: list[tuple[str, dict[str, Any], list[Path]]] = []
    skipped: list[str] = []
    for server in servers:
        if not (MANIFEST_DIR / f"{server}.yml").exists():
            fail(f"manifest does not exist for server: {server}")
        plugin = matching_plugin(server, plugin_name)
        if not plugin:
            if args.servers:
                fail(f"plugin {plugin_name} is not present in effective manifest {server}")
            skipped.append(server)
            continue
        paths = config_paths_for(server, plugin_name, plugin)
        planned.append((server, plugin, paths))

    if not planned:
        fail(f"plugin {plugin_name} is not present in any target server")

    if skipped and args.verbose:
        print("skipped servers without plugin: " + ", ".join(skipped))

    action = "would push" if args.dry_run else "pushing"
    print(
        f"{action} {plugin_name} to {', '.join(server for server, _plugin, _paths in planned)}",
        flush=True,
    )

    commands = reload_command(plugin_name, args)
    for server, _plugin, paths in planned:
        if not args.reload_only:
            apply_config(server, paths, args)
        if not args.apply_only:
            send_reload(server, commands, args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Push plugin config to deploy servers and trigger hot reload."
    )
    sub = parser.add_subparsers(dest="command_name", required=True)

    p_push = sub.add_parser("push", help="push one plugin config to one or more servers")
    p_push.add_argument("plugin", help="plugin name or manifest plugin id, e.g. EcoItems")
    p_push.add_argument(
        "servers",
        nargs="*",
        help="optional server names; when omitted, use docker-compose services or all instance manifests",
    )
    p_push.add_argument(
        "--compose-file",
        default="docker-compose.yml",
        help="docker compose file; default: docker-compose.yml",
    )
    p_push.add_argument(
        "--service",
        help="compose service name override; default is the server name",
    )
    p_push.add_argument(
        "--command",
        action="append",
        help="reload command to send; may be repeated; default: '<plugin> reload'",
    )
    p_push.add_argument(
        "--apply-only",
        action="store_true",
        help="apply config without sending reload commands",
    )
    p_push.add_argument(
        "--reload-only",
        action="store_true",
        help="send reload commands without applying config",
    )
    p_push.add_argument(
        "--print-only",
        action="store_true",
        help="print docker reload commands instead of executing them",
    )
    p_push.add_argument(
        "--allow-missing",
        action="store_true",
        help="leave missing template variables unchanged while applying config",
    )
    p_push.add_argument("--dry-run", action="store_true", help="show changes only")
    p_push.add_argument("--verbose", action="store_true")
    p_push.set_defaults(func=command_push)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.apply_only and args.reload_only:
        fail("--apply-only and --reload-only cannot be used together")
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
