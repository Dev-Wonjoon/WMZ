#!/usr/bin/env python3
"""Import and apply server config templates.

Templates live under ops/templates/<layer>. Text files may contain ${NAME}
placeholders. Values are loaded from ops/secrets and environment variables when
templates are applied to deploy/<name>.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from manifest_tool import (
    OPS_DIR,
    ROOT,
    fail,
    load_manifest,
    normalize_plugins,
    plugin_is_removed,
    resolve_manifest,
    strip_version_suffix,
)


TEMPLATE_DIR = OPS_DIR / "templates"
SECRETS_DIR = OPS_DIR / "secrets"
DEPLOY_DIR = ROOT / "deploy"
WORKBENCH_DIR = ROOT / "workbench"
TOOLS_DIR = Path(__file__).resolve().parent
WORKBENCH_TOOL = TOOLS_DIR / "workbench_tool.py"

TOKEN_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

TEXT_EXTENSIONS = {
    ".conf",
    ".cfg",
    ".csv",
    ".env",
    ".ini",
    ".json",
    ".lang",
    ".properties",
    ".sk",
    ".toml",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}

IMPORT_EXCLUDED_PARTS = {
    ".git",
    ".assetcache",
    ".archive-unpack",
    "backups",
    "cache",
    "crash-reports",
    "data",
    "libraries",
    "logs",
    "saves",
    "sessions",
    "tmp",
    "versions",
    "world",
    "world_nether",
    "world_the_end",
}

IMPORT_EXCLUDED_NAMES = {
    ".console_history",
    "banned-ips.json",
    "banned-players.json",
    "contexts.json",
    "eula.txt",
    "lastupdate",
    "map-color-cache.dat",
    "ops.json",
    "security.key",
    "session.lock",
    "usercache.json",
    "variables.csv",
    "version_history.json",
    "whitelist.json",
}

IMPORT_EXCLUDED_SUFFIXES = {
    ".db",
    ".gz",
    ".jar",
    ".lock",
    ".log",
    ".mv.db",
    ".sqlite",
    ".sqlite3",
    ".zip",
}


def simple_name(name: str, label: str) -> str:
    if not name or "/" in name or "\\" in name:
        fail(f"{label} must be a simple directory name without slashes")
    return name


def safe_relative_name(name: str, label: str) -> Path:
    path = Path(name)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        fail(f"{label} must be a relative path without '..': {name}")
    if not path.parts:
        fail(f"{label} must not be empty")
    return path


def server_path(name: str) -> Path:
    name = simple_name(name, "server name")
    return DEPLOY_DIR / name


def workbench_path(name: str) -> Path:
    return WORKBENCH_DIR / safe_relative_name(name, "workbench name")


def config_source_path(name: str, source: str = "auto") -> Path:
    workbench = workbench_path(name)
    if source == "workbench":
        return workbench
    server = server_path(name)
    if source == "server":
        return server
    if source != "auto":
        fail(f"unknown config source mode: {source}")
    if workbench.exists() and any(workbench.iterdir()):
        return workbench
    if server.exists():
        return server
    if workbench.exists():
        return workbench
    return workbench if name == "test" else server


def source_checked_message(name: str, source: str) -> str:
    if source == "workbench":
        return rel(workbench_path(name))
    if source == "server":
        return rel(server_path(name))
    return f"{rel(workbench_path(name))}, {rel(server_path(name))}"


def config_target_path(name: str) -> Path:
    return server_path(name)


def template_path(name: str) -> Path:
    name = simple_name(name, "template name")
    return TEMPLATE_DIR / name


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def safe_relative_input(value: str, label: str) -> Path:
    path = Path(value)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        fail(f"{label} must be a relative path inside the config source: {value}")
    if not path.parts:
        fail(f"{label} must not be empty")
    return path


def path_is_under(relative: Path, parent: Path) -> bool:
    relative_parts = tuple(part.lower() for part in relative.parts)
    parent_parts = tuple(part.lower() for part in parent.parts)
    return len(relative_parts) >= len(parent_parts) and relative_parts[: len(parent_parts)] == parent_parts


def loose_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def run_tool(args: list[str]) -> None:
    command = [sys.executable, *args]
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode:
        raise SystemExit(result.returncode)


def parse_env_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            fail(f"{rel(path)}:{line_number}: expected KEY=value")
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            fail(f"{rel(path)}:{line_number}: invalid secret key: {key}")
        values[key] = parse_env_value(value)
    return values


def secret_file_keys(path: Path) -> list[str]:
    stem = path.name
    if "." in stem:
        stem = path.stem
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", stem).strip("_").upper()
    keys = [path.name, stem]
    if normalized:
        keys.append(normalized)
    result: list[str] = []
    for key in keys:
        if key and key not in result:
            result.append(key)
    return result


def load_secret_files(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    if not path.is_dir():
        fail(f"secret path is not a directory: {rel(path)}")

    for item in sorted(path.iterdir(), key=lambda candidate: candidate.name.lower()):
        if not item.is_file() or item.name.startswith(".") or item.suffix == ".env":
            continue
        value = item.read_text(encoding="utf-8").rstrip("\r\n")
        for key in secret_file_keys(item):
            values[key] = value
    return values


def load_secrets(server: str) -> dict[str, str]:
    values = dict(os.environ)

    # Later sources override earlier ones.
    for source in (
        SECRETS_DIR / "common.env",
        SECRETS_DIR / "common",
        SECRETS_DIR / f"{server}.env",
        SECRETS_DIR / server,
    ):
        if source.suffix == ".env":
            values.update(load_env_file(source))
        else:
            values.update(load_secret_files(source))
    return {str(key): str(value) for key, value in values.items()}


def is_probably_text(path: Path) -> bool:
    if path.suffix.lower() in TEXT_EXTENSIONS:
        return True
    try:
        chunk = path.read_bytes()[:4096]
    except OSError:
        return False
    if b"\0" in chunk:
        return False
    try:
        chunk.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def read_template_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        fail(f"template file is not valid UTF-8 text: {rel(path)}")


def find_tokens(text: str) -> set[str]:
    return {match.group(1) for match in TOKEN_RE.finditer(text)}


def find_token_defaults(text: str) -> dict[str, bool]:
    tokens: dict[str, bool] = {}
    for match in TOKEN_RE.finditer(text):
        name = match.group(1)
        has_default = match.group(2) is not None
        tokens[name] = tokens.get(name, False) or has_default
    return tokens


def render_text(text: str, secrets: dict[str, str], allow_missing: bool) -> tuple[str, set[str]]:
    missing: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        default = match.group(2)
        if name in secrets:
            return secrets[name]
        if default is not None:
            return default
        missing.add(name)
        return match.group(0)

    rendered = TOKEN_RE.sub(replace, text)
    if missing and not allow_missing:
        return rendered, missing
    return rendered, set()


def resolved_template_layers(server: str) -> list[str]:
    resolved = resolve_manifest(server)
    layers = [str(layer) for layer in resolved.get("templates", [])]
    if not layers:
        layers = [server]
    return layers


def iter_template_files(layers: list[str]) -> list[tuple[str, Path, Path]]:
    files: list[tuple[str, Path, Path]] = []
    for layer in layers:
        base = template_path(layer)
        if not base.exists():
            continue
        if not base.is_dir():
            fail(f"template layer is not a directory: {rel(base)}")
        for path in sorted(base.rglob("*"), key=lambda item: str(item).lower()):
            if path.is_file():
                files.append((layer, base, path))
    return files


def filter_template_files(
    files: list[tuple[str, Path, Path]], paths: list[str]
) -> list[tuple[str, Path, Path]]:
    if not paths:
        return files

    filters = [safe_relative_input(item, "apply path") for item in paths]
    selected: list[tuple[str, Path, Path]] = []
    matched_filters: set[int] = set()

    for entry in files:
        _layer, base, path = entry
        relative = path.relative_to(base)
        for index, candidate in enumerate(filters):
            if path_is_under(relative, candidate):
                selected.append(entry)
                matched_filters.add(index)
                break

    missing = [
        str(filters[index])
        for index in range(len(filters))
        if index not in matched_filters
    ]
    if missing:
        fail("no template files matched apply path(s): " + ", ".join(missing))

    return selected


def command_vars(args: argparse.Namespace) -> None:
    layers = resolved_template_layers(args.server)
    secrets = load_secrets(args.server)
    by_token: dict[str, list[str]] = {}
    has_default_by_token: dict[str, bool] = {}

    for layer, base, path in iter_template_files(layers):
        if not is_probably_text(path):
            continue
        text = read_template_text(path)
        for token, has_default in sorted(find_token_defaults(text).items()):
            by_token.setdefault(token, []).append(f"{layer}:{path.relative_to(base)}")
            has_default_by_token[token] = has_default_by_token.get(token, False) or has_default

    if not by_token:
        print("no template variables found")
        return

    for token in sorted(by_token):
        if token in secrets:
            state = "set"
        elif has_default_by_token.get(token):
            state = "default"
        else:
            state = "missing"
        print(f"{token}: {state}")
        if args.verbose:
            for location in by_token[token]:
                print(f"  - {location}")


def command_init_layer(args: argparse.Namespace) -> None:
    layer = simple_name(args.template, "template name")
    root = template_path(layer)
    paths = [safe_relative_input(path, "template path") for path in args.paths]
    targets = [root] if not paths else [root / path for path in paths]

    action = "would create" if args.dry_run else "created"
    print(f"{action} template layer paths for {layer}")
    for target in targets:
        print(f"  {rel(target)}")
        if args.dry_run:
            continue
        target.mkdir(parents=True, exist_ok=True)


def manifest_plugin_entries(server: str, include_inherited: bool) -> list[dict[str, Any]]:
    if include_inherited:
        return [
            dict(plugin)
            for plugin in resolve_manifest(server).get("plugins", [])
            if isinstance(plugin, dict) and not plugin_is_removed(plugin)
        ]

    data = load_manifest(server)
    return [
        dict(plugin)
        for plugin in normalize_plugins(data, validate_required=False)
        if not plugin_is_removed(plugin)
    ]


def plugin_config_metadata(plugin: dict[str, Any]) -> list[Path]:
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
    return [safe_relative_input(value, "plugin config path") for value in values]


def bootstrap_plugin_dirs(source_root: Path) -> dict[str, Path]:
    plugins_root = source_root / "plugins"
    if not plugins_root.is_dir():
        return {}

    result: dict[str, Path] = {}
    for path in sorted(plugins_root.iterdir(), key=lambda item: item.name.lower()):
        if not path.is_dir():
            continue
        result.setdefault(loose_key(path.name), Path("plugins") / path.name)
    return result


def plugin_guess_config_path(plugin: dict[str, Any]) -> Path:
    file_name = str(plugin.get("file") or "").strip()
    if file_name:
        stem = strip_version_suffix(Path(file_name).stem)
        if stem:
            return Path("plugins") / stem
    return Path("plugins") / str(plugin["id"])


def plugin_config_paths(plugin: dict[str, Any], source_root: Path) -> list[Path]:
    metadata = plugin_config_metadata(plugin)
    if metadata:
        return metadata

    dirs = bootstrap_plugin_dirs(source_root)
    candidates = [str(plugin["id"])]
    file_name = str(plugin.get("file") or "").strip()
    if file_name:
        stem = Path(file_name).stem
        candidates.extend([stem, strip_version_suffix(stem)])

    for candidate in candidates:
        matched = dirs.get(loose_key(candidate))
        if matched:
            return [matched]

    return [plugin_guess_config_path(plugin)]


def importable_sources_under(source_root: Path, relative: Path, include_runtime: bool) -> list[Path]:
    source = source_root / relative
    if not source.exists():
        return []
    if source.is_file():
        return [source] if should_import(source, source_root, include_runtime) else []
    if source.is_dir():
        return [
            path
            for path in sorted(source.rglob("*"), key=lambda item: str(item).lower())
            if should_import(path, source_root, include_runtime)
        ]
    return []


def command_init(args: argparse.Namespace) -> None:
    server = simple_name(args.server, "server name")
    template = simple_name(args.template or server, "template name")
    source_name = f"bootstrap/{server}"
    source_root = workbench_path(source_name)
    destination_root = template_path(template)

    if not args.no_bootstrap:
        command = [
            str(WORKBENCH_TOOL),
            "bootstrap",
            server,
            "--manifest",
            server,
        ]
        if args.dry_run:
            command.append("--dry-run")
        print(
            f"{'would prepare' if args.dry_run else 'preparing'} {rel(source_root)} from manifest {server}",
            flush=True,
        )
        run_tool(command)

    if args.run:
        run_command = [
            str(WORKBENCH_TOOL),
            "run-local",
            "bootstrap",
            server,
            "--accept-eula",
            "--timeout-seconds",
            str(args.timeout_seconds),
            "--stop-grace-seconds",
            str(args.stop_grace_seconds),
            "--xms",
            args.xms,
            "--xmx",
            args.xmx,
        ]
        if args.dry_run:
            print(
                f"would run {rel(source_root)} to generate plugin config "
                f"for up to {args.timeout_seconds}s",
                flush=True,
            )
        else:
            run_tool(run_command)

    plugins = manifest_plugin_entries(server, include_inherited=args.all)
    if args.plugins:
        requested = {loose_key(plugin_id) for plugin_id in args.plugins}
        plugins = [
            plugin
            for plugin in plugins
            if loose_key(str(plugin["id"])) in requested
        ]
        found = {loose_key(str(plugin["id"])) for plugin in plugins}
        missing = sorted(requested - found)
        if missing:
            fail("plugin id(s) not found in selected manifest scope: " + ", ".join(missing))

    if not plugins:
        scope = "effective manifest" if args.all else "direct manifest"
        print(f"no plugins found in {scope} for {server}")
        return

    planned: list[tuple[dict[str, Any], Path]] = []
    seen: set[Path] = set()
    for plugin in plugins:
        for relative in plugin_config_paths(plugin, source_root):
            if relative in seen:
                continue
            seen.add(relative)
            planned.append((plugin, relative))

    action = "would import" if args.import_configs and args.dry_run else (
        "importing" if args.import_configs else (
            "would create" if args.dry_run else "created"
        )
    )
    print(f"{action} config template paths for {server} -> {rel(destination_root)}")

    imported_count = 0
    for plugin, relative in planned:
        destination = destination_root / relative
        print(f"  {plugin['id']}: {relative}")

        if args.import_configs:
            sources = importable_sources_under(source_root, relative, args.include_runtime)
            if not sources:
                print(f"    no importable files found under {rel(source_root / relative)}")
                if not args.dry_run:
                    destination.mkdir(parents=True, exist_ok=True)
                continue

            for source in sources:
                target = destination_root / source.relative_to(source_root)
                print(f"    {rel(source)} -> {rel(target)}")
                imported_count += 1
                if args.dry_run:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            continue

        print(f"    {rel(destination)}")
        if args.dry_run:
            continue
        destination.mkdir(parents=True, exist_ok=True)

    if args.import_configs:
        print(f"importable files: {imported_count}")


def command_apply(args: argparse.Namespace) -> None:
    layers = resolved_template_layers(args.server)
    if args.target:
        destination_root = Path(args.target)
        if not destination_root.is_absolute():
            destination_root = ROOT / destination_root
    else:
        destination_root = config_target_path(args.server)
    secrets = load_secrets(args.server)
    files = iter_template_files(layers)

    if not files:
        print(f"no template files found for layers: {', '.join(layers)}")
        return

    files = filter_template_files(files, args.paths)

    missing_by_file: dict[str, set[str]] = {}
    rendered_files: list[tuple[Path, Path, bool, bytes | str]] = []

    for layer, base, source in files:
        relative = source.relative_to(base)
        destination = destination_root / relative
        if is_probably_text(source):
            text = read_template_text(source)
            rendered, missing = render_text(text, secrets, args.allow_missing)
            if missing:
                missing_by_file[f"{layer}:{relative}"] = missing
            rendered_files.append((source, destination, True, rendered))
        else:
            rendered_files.append((source, destination, False, source.read_bytes()))

    if missing_by_file:
        for location, names in missing_by_file.items():
            print(
                f"error: missing template variables in {location}: "
                + ", ".join(sorted(names)),
                file=sys.stderr,
            )
        raise SystemExit(1)

    action = "would apply" if args.dry_run else "applied"
    print(f"{action} templates to {rel(destination_root)}")
    print("layers: " + " -> ".join(layers))

    for source, destination, is_text, content in rendered_files:
        suffix = "render" if is_text else "copy"
        print(f"  {suffix}: {rel(source)} -> {rel(destination)}")
        if args.dry_run:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        if is_text:
            destination.write_text(str(content), encoding="utf-8", newline="\n")
        else:
            destination.write_bytes(bytes(content))


def should_import(path: Path, root: Path, include_runtime: bool = False) -> bool:
    relative = path.relative_to(root)
    if not include_runtime:
        parts = {part.lower() for part in relative.parts[:-1]}
        if parts & IMPORT_EXCLUDED_PARTS:
            return False
    name = path.name.lower()
    if not include_runtime:
        if name in IMPORT_EXCLUDED_NAMES:
            return False
        for suffix in IMPORT_EXCLUDED_SUFFIXES:
            if name.endswith(suffix):
                return False
    return path.is_file() and is_probably_text(path)


def scan_candidates(
    server: str,
    source: str = "auto",
    include_runtime: bool = False,
) -> tuple[Path, list[Path]]:
    root = config_source_path(server, source)
    if not root.exists():
        checked = source_checked_message(server, source)
        fail(f"config source directory does not exist; checked: {checked}")
    return root, [
        path
        for path in sorted(root.rglob("*"), key=lambda item: str(item).lower())
        if should_import(path, root, include_runtime)
    ]


def command_scan(args: argparse.Namespace) -> None:
    root, candidates = scan_candidates(args.server, args.source, args.include_runtime)
    if not candidates:
        print("no importable config files found")
        return
    print(f"source: {rel(root)}")
    for path in candidates:
        print(path.relative_to(root))


def command_plugin_dirs(args: argparse.Namespace) -> None:
    root = config_source_path(args.server, args.source)
    plugins_root = root / "plugins"
    if not plugins_root.exists():
        checked = f"{rel(root / 'plugins')}"
        fail(f"plugins config directory does not exist: {checked}")
    if not plugins_root.is_dir():
        fail(f"plugins path is not a directory: {rel(plugins_root)}")

    dirs = [
        path
        for path in sorted(plugins_root.iterdir(), key=lambda item: item.name.lower())
        if path.is_dir()
    ]
    if not dirs:
        print("no plugin config directories found")
        return

    print(f"source: {rel(root)}")
    for path in dirs:
        relative = path.relative_to(root)
        if args.counts:
            count = sum(
                1
                for candidate in path.rglob("*")
                if should_import(candidate, root, args.include_runtime)
            )
            print(f"{relative} ({count} importable files)")
        else:
            print(relative)


def import_sources_for_paths(
    source_root: Path,
    paths: list[str],
    include_runtime: bool = False,
) -> list[Path]:
    selected: list[Path] = []
    seen: set[Path] = set()

    for item in paths:
        relative = safe_relative_input(item, "import path")
        source = source_root / relative
        if not source.exists():
            fail(f"server config path does not exist: {rel(source)}")
        if source.is_file():
            if not should_import(source, source_root, include_runtime):
                fail(f"server config path is not importable: {rel(source)}")
            candidates = [source]
        elif source.is_dir():
            candidates = [
                path
                for path in sorted(source.rglob("*"), key=lambda candidate: str(candidate).lower())
                if should_import(path, source_root, include_runtime)
            ]
            if not candidates:
                fail(f"no importable config files found under: {rel(source)}")
        else:
            fail(f"server config path is not a file or directory: {rel(source)}")

        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved not in seen:
                selected.append(candidate)
                seen.add(resolved)

    return selected


def command_import(args: argparse.Namespace) -> None:
    source_root = config_source_path(args.server, args.source)
    destination_root = template_path(args.template)
    if not source_root.exists():
        checked = source_checked_message(args.server, args.source)
        fail(f"config source directory does not exist; checked: {checked}")

    if args.paths:
        sources = import_sources_for_paths(
            source_root,
            args.paths,
            args.include_runtime,
        )
    else:
        source_root, sources = scan_candidates(
            args.server,
            args.source,
            args.include_runtime,
        )

    if not sources:
        print("no config files to import")
        return

    action = "would import" if args.dry_run else "imported"
    print(f"{action} {len(sources)} files to {rel(destination_root)}")
    for source in sources:
        relative = source.relative_to(source_root)
        destination = destination_root / relative
        print(f"  {rel(source)} -> {rel(destination)}")
        if args.dry_run:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import and apply config templates.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser(
        "init",
        help="initialize a template layer from a manifest/bootstrap config plan",
    )
    p_init.add_argument("server", help="server manifest name, e.g. survival")
    p_init.add_argument(
        "plugins",
        nargs="*",
        help="optional manifest plugin ids to initialize",
    )
    p_init.add_argument(
        "--template",
        help="destination template layer; default is the server name",
    )
    p_init.add_argument(
        "--all",
        action="store_true",
        help="include inherited plugins from the effective manifest",
    )
    p_init.add_argument(
        "--import",
        dest="import_configs",
        action="store_true",
        help="copy importable config files from workbench/bootstrap/<server>; default",
    )
    p_init.add_argument(
        "--no-import",
        dest="import_configs",
        action="store_false",
        help="only create config directories instead of copying files",
    )
    p_init.set_defaults(import_configs=True)
    p_init.add_argument(
        "--run",
        action="store_true",
        help="run workbench/bootstrap/<server> before importing config; default",
    )
    p_init.add_argument(
        "--no-run",
        dest="run",
        action="store_false",
        help="prepare bootstrap and import existing config without starting the server",
    )
    p_init.set_defaults(run=True)
    p_init.add_argument(
        "--timeout-seconds",
        type=int,
        default=240,
        help="seconds to run the bootstrap server with --run",
    )
    p_init.add_argument(
        "--stop-grace-seconds",
        type=int,
        default=60,
        help="seconds to wait after sending stop with --run",
    )
    p_init.add_argument(
        "--xms",
        default="1G",
        help="initial heap size for --run; default: 1G",
    )
    p_init.add_argument(
        "--xmx",
        default="2G",
        help="maximum heap size for --run; default: 2G",
    )
    p_init.add_argument(
        "--include-runtime",
        action="store_true",
        help="include runtime/state-like files while importing",
    )
    p_init.add_argument(
        "--no-bootstrap",
        action="store_true",
        help="do not prepare workbench/bootstrap/<server> before planning",
    )
    p_init.add_argument("--dry-run", action="store_true")
    p_init.set_defaults(func=command_init)

    p_init_layer = sub.add_parser(
        "init-layer",
        help="create a template layer and optional relative directories",
    )
    p_init_layer.add_argument("template", help="template layer, e.g. survival")
    p_init_layer.add_argument(
        "paths",
        nargs="*",
        help="optional template-relative directories, e.g. plugins/EcoItems",
    )
    p_init_layer.add_argument("--dry-run", action="store_true")
    p_init_layer.set_defaults(func=command_init_layer)

    p_scan = sub.add_parser("scan", help="list importable config files from a server")
    p_scan.add_argument("server", help="server name, e.g. test")
    p_scan.add_argument(
        "--from",
        dest="source",
        choices=["auto", "workbench", "server"],
        default="auto",
        help="source root to scan; default: auto",
    )
    p_scan.add_argument(
        "--include-runtime",
        action="store_true",
        help="include runtime/state-like files normally excluded from templates",
    )
    p_scan.set_defaults(func=command_scan)

    p_plugin_dirs = sub.add_parser(
        "plugin-dirs",
        help="list actual plugin config directories from a server source",
    )
    p_plugin_dirs.add_argument("server", help="server name, e.g. test")
    p_plugin_dirs.add_argument(
        "--from",
        dest="source",
        choices=["auto", "workbench", "server"],
        default="auto",
        help="source root to inspect; default: auto",
    )
    p_plugin_dirs.add_argument(
        "--counts",
        action="store_true",
        help="also show importable file counts under each directory",
    )
    p_plugin_dirs.add_argument(
        "--include-runtime",
        action="store_true",
        help="include runtime/state-like files in importable counts",
    )
    p_plugin_dirs.set_defaults(func=command_plugin_dirs)

    p_import = sub.add_parser("import", help="copy server config files into a template layer")
    p_import.add_argument("server", help="source server name, e.g. test")
    p_import.add_argument("template", help="destination template layer, e.g. common")
    p_import.add_argument(
        "paths",
        nargs="*",
        help="optional relative files or directories to import",
    )
    p_import.add_argument(
        "--from",
        dest="source",
        choices=["auto", "workbench", "server"],
        default="auto",
        help="source root to import from; default: auto",
    )
    p_import.add_argument(
        "--include-runtime",
        action="store_true",
        help="include runtime/state-like files normally excluded from templates",
    )
    p_import.add_argument("--dry-run", action="store_true", help="show changes without writing")
    p_import.set_defaults(func=command_import)

    p_apply = sub.add_parser("apply", help="render templates into a server directory")
    p_apply.add_argument("server", help="target server name, e.g. survival")
    p_apply.add_argument(
        "paths",
        nargs="*",
        help="optional template-relative files or directories to apply",
    )
    p_apply.add_argument(
        "--target",
        help="target root to render into; default is deploy/<server>",
    )
    p_apply.add_argument("--dry-run", action="store_true", help="show changes without writing")
    p_apply.add_argument(
        "--allow-missing",
        action="store_true",
        help="leave missing ${NAME} placeholders unchanged",
    )
    p_apply.set_defaults(func=command_apply)

    p_vars = sub.add_parser("vars", help="show variables required by resolved templates")
    p_vars.add_argument("server", help="server name, e.g. survival")
    p_vars.add_argument("--verbose", action="store_true", help="show template locations")
    p_vars.set_defaults(func=command_vars)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
