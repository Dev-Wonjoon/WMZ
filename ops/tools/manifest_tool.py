#!/usr/bin/env python3
"""Create and maintain shallow instance manifests.

This tool intentionally keeps the manifest model small:

    common -> server
    proxy-common -> proxy

It writes simple YAML files without requiring third-party dependencies.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
OPS_DIR = Path(__file__).resolve().parents[1]
MANIFEST_DIR = OPS_DIR / "manifests"
VENDOR_PLUGIN_DIR = OPS_DIR / "vendor" / "plugins"
VENDOR_SERVER_DIR = OPS_DIR / "vendor" / "server"
DEPLOY_DIR = ROOT / "deploy"
WORKBENCH_DIR = ROOT / "workbench"


SCALAR_KEYS = {
    "type",
    "image",
    "server",
    "java",
    "notes",
}
LIST_KEYS = {
    "extends",
    "templates",
    "plugins",
}


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def manifest_path(name: str) -> Path:
    if not name or "/" in name or "\\" in name:
        fail("manifest name must be a simple file name without slashes")
    if name.endswith(".yml"):
        name = name[:-4]
    return MANIFEST_DIR / f"{name}.yml"


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        fail(f"manifest does not exist: {path}")


def parse_scalar(value: str) -> Any:
    value = value.strip()
    if value == "[]":
        return []
    if value in ("true", "True"):
        return True
    if value in ("false", "False"):
        return False
    if value in ("null", "Null", "~"):
        return None
    if (
        len(value) >= 2
        and value[0] == value[-1]
        and value[0] in ("'", '"')
    ):
        return value[1:-1]
    return value


def parse_manifest_text(text: str, source: str) -> dict[str, Any]:
    """Parse the small YAML subset used by ops/manifests.

    The tool intentionally avoids a PyYAML dependency. It supports top-level
    scalar fields, top-level lists, and plugin lists made of simple mappings:

        plugins:
          - id: CMI
            file: CMI.jar
    """
    data: dict[str, Any] = {}
    lines = text.splitlines()
    index = 0

    while index < len(lines):
        raw = lines[index]
        stripped = raw.strip()
        index += 1

        if not stripped or stripped.startswith("#"):
            continue
        if raw[: len(raw) - len(raw.lstrip())]:
            fail(f"{source}: unexpected indentation at line {index}")
        if ":" not in stripped:
            fail(f"{source}: expected key/value at line {index}")

        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()

        if value:
            data[key] = parse_scalar(value)
            continue

        items: list[Any] = []
        while index < len(lines):
            child_raw = lines[index]
            child_stripped = child_raw.strip()
            if not child_stripped or child_stripped.startswith("#"):
                index += 1
                continue
            indent = len(child_raw) - len(child_raw.lstrip())
            if indent == 0:
                break
            if indent != 2 or not child_stripped.startswith("- "):
                fail(f"{source}: unsupported list syntax at line {index + 1}")

            item_text = child_stripped[2:].strip()
            index += 1

            if not item_text:
                fail(f"{source}: empty list item at line {index}")

            if ":" not in item_text:
                items.append(parse_scalar(item_text))
                continue

            item_key, item_value = item_text.split(":", 1)
            item: dict[str, Any] = {item_key.strip(): parse_scalar(item_value)}

            while index < len(lines):
                prop_raw = lines[index]
                prop_stripped = prop_raw.strip()
                if not prop_stripped or prop_stripped.startswith("#"):
                    index += 1
                    continue
                prop_indent = len(prop_raw) - len(prop_raw.lstrip())
                if prop_indent <= 2:
                    break
                if prop_indent != 4 or ":" not in prop_stripped:
                    fail(f"{source}: unsupported mapping syntax at line {index + 1}")
                prop_key, prop_value = prop_stripped.split(":", 1)
                item[prop_key.strip()] = parse_scalar(prop_value)
                index += 1

            items.append(item)

        data[key] = items

    for key in data:
        if key not in SCALAR_KEYS and key not in LIST_KEYS:
            fail(f"{source}: unsupported top-level key: {key}")

    if "plugins" not in data:
        data["plugins"] = []
    if data["plugins"] is None:
        data["plugins"] = []
    if not isinstance(data["plugins"], list):
        fail(f"{source}: plugins must be a list")

    if "extends" in data and isinstance(data["extends"], str):
        data["extends"] = [data["extends"]]
    if "extends" in data and not isinstance(data["extends"], list):
        fail(f"{source}: extends must be a list or string")

    return data


def load_manifest(name: str) -> dict[str, Any]:
    path = manifest_path(name)
    data = parse_manifest_text(read_text(path), str(path.relative_to(ROOT)))
    data["_name"] = path.stem
    data["_path"] = path
    return data


def write_new(path: Path, content: str, force: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force:
        fail(f"already exists: {path} (use --force to overwrite)")
    path.write_text(content, encoding="utf-8", newline="\n")
    print(f"wrote {path.relative_to(ROOT)}")


def plugin_ids(text: str) -> list[str]:
    ids: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- id:"):
            ids.append(stripped.split(":", 1)[1].strip().strip("\"'"))
    return ids


def plugin_id(entry: Any) -> str | None:
    if not isinstance(entry, dict):
        return None
    value = entry.get("id")
    if value is None:
        return None
    return str(value)


def plugin_is_removed(entry: dict[str, Any]) -> bool:
    return entry.get("remove") is True or entry.get("enabled") is False


def is_root_manifest(name: str) -> bool:
    return name == "common" or name.endswith("-common")


def direct_plugin_ids(data: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for entry in data.get("plugins", []):
        pid = plugin_id(entry)
        if pid:
            ids.append(pid)
    return ids


def merge_plugins(
    base: list[dict[str, Any]], overlay: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    merged = [dict(item) for item in base]
    index_by_id = {str(item["id"]): i for i, item in enumerate(merged)}

    for entry in overlay:
        pid = str(entry["id"])
        if plugin_is_removed(entry):
            existing = index_by_id.pop(pid, None)
            if existing is not None:
                merged.pop(existing)
                index_by_id = {str(item["id"]): i for i, item in enumerate(merged)}
            continue

        item = dict(entry)
        if pid in index_by_id:
            merged[index_by_id[pid]] = item
        else:
            index_by_id[pid] = len(merged)
            merged.append(item)

    return merged


def resolve_manifest(name: str, stack: list[str] | None = None) -> dict[str, Any]:
    stack = stack or []
    clean_name = manifest_path(name).stem
    if clean_name in stack:
        fail("manifest inheritance cycle: " + " -> ".join(stack + [clean_name]))

    current = load_manifest(clean_name)
    parents = [str(parent) for parent in current.get("extends", [])]

    resolved: dict[str, Any] = {
        "name": clean_name,
        "extends": parents,
        "plugins": [],
        "templates": [],
        "sources": [],
    }

    for parent in parents:
        parent_data = resolve_manifest(parent, stack + [clean_name])
        for key, value in parent_data.items():
            if key in ("name", "extends", "plugins", "templates", "sources"):
                continue
            resolved[key] = value
        resolved["plugins"] = merge_plugins(resolved["plugins"], parent_data["plugins"])
        resolved["templates"].extend(parent_data.get("templates", []))
        resolved["sources"].extend(parent_data.get("sources", []))

    for key, value in current.items():
        if key.startswith("_") or key in ("extends", "plugins", "templates"):
            continue
        resolved[key] = value

    local_templates = current.get("templates", [])
    if local_templates:
        if not isinstance(local_templates, list):
            fail(f"{clean_name}.yml: templates must be a list")
        resolved["templates"].extend(str(item) for item in local_templates)
    elif "templates" not in current:
        resolved["templates"].append(clean_name)

    local_plugins = normalize_plugins(current, validate_required=False)
    resolved["plugins"] = merge_plugins(resolved["plugins"], local_plugins)
    resolved["sources"].append(clean_name)

    return resolved


def normalize_plugins(
    data: dict[str, Any], validate_required: bool = True
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    manifest_name = data.get("_name", "manifest")

    for entry in data.get("plugins", []):
        if not isinstance(entry, dict):
            fail(f"{manifest_name}.yml: plugin entries must be mappings")
        pid = plugin_id(entry)
        if not pid:
            fail(f"{manifest_name}.yml: plugin entry missing id")
        if pid in seen:
            fail(f"{manifest_name}.yml: duplicate plugin id: {pid}")
        seen.add(pid)

        item = dict(entry)
        item["id"] = pid
        if validate_required and not plugin_is_removed(item) and not item.get("file"):
            fail(f"{manifest_name}.yml: plugin {pid} is missing file")
        normalized.append(item)

    return normalized


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def plugin_target_root(manifest_name: str) -> Path:
    clean_name = manifest_path(manifest_name).stem
    return DEPLOY_DIR / clean_name


def resolve_target_root(value: str | None, manifest_name: str) -> Path:
    if not value:
        return plugin_target_root(manifest_name)
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


def safe_plugin_file_name(value: Any, plugin_id_value: str) -> str:
    if value is None:
        fail(f"plugin {plugin_id_value} is missing file")
    file_name = str(value).strip()
    path = Path(file_name)
    if (
        not file_name
        or path.is_absolute()
        or path.name != file_name
        or file_name in (".", "..")
    ):
        fail(f"plugin {plugin_id_value} has unsafe file name: {file_name!r}")
    if path.suffix.lower() != ".jar":
        fail(f"plugin {plugin_id_value} file must be a .jar: {file_name}")
    return file_name


def safe_server_file_name(value: Any, required: bool = True) -> str | None:
    if value is None:
        if required:
            fail("manifest is missing server jar file; set the top-level server field")
        return None
    file_name = str(value).strip()
    path = Path(file_name)
    if (
        not file_name
        or path.is_absolute()
        or path.name != file_name
        or file_name in (".", "..")
    ):
        fail(f"server jar has unsafe file name: {file_name!r}")
    if path.suffix.lower() != ".jar":
        fail(f"server file must be a .jar: {file_name}")
    return file_name


def auto_single_server_file(required: bool) -> str | None:
    jars = sorted(VENDOR_SERVER_DIR.glob("*.jar"), key=lambda item: item.name.lower())
    if len(jars) == 1:
        return jars[0].name
    if not jars and required:
        fail(f"no server jars found in {display_path(VENDOR_SERVER_DIR)}")
    if len(jars) > 1 and required:
        fail(
            "multiple server jars found; set manifest server, use --server, "
            "or remove ambiguity from ops/vendor/server"
        )
    return None


def selected_plugin_files(
    resolved: dict[str, Any], require_hashes: bool
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    by_file: dict[str, str] = {}

    for plugin in resolved.get("plugins", []):
        plugin_id_value = str(plugin["id"])
        file_name = safe_plugin_file_name(plugin.get("file"), plugin_id_value)

        existing_plugin = by_file.get(file_name)
        if existing_plugin:
            fail(
                f"plugins {existing_plugin} and {plugin_id_value} both target "
                f"{file_name}"
            )
        by_file[file_name] = plugin_id_value

        source = VENDOR_PLUGIN_DIR / file_name
        if not source.exists():
            fail(f"missing vendor plugin for {plugin_id_value}: {display_path(source)}")

        expected_hash = plugin.get("sha256")
        if require_hashes and not expected_hash:
            fail(f"plugin {plugin_id_value} is missing sha256")
        if expected_hash:
            actual_hash = sha256_file(source)
            if str(expected_hash).lower() != actual_hash.lower():
                fail(
                    f"sha256 mismatch for {plugin_id_value} "
                    f"({display_path(source)})"
                )

        selected.append(
            {
                "id": plugin_id_value,
                "file": file_name,
                "source": source,
            }
        )

    return selected


def files_match(left: Path, right: Path) -> bool:
    if not left.exists() or not right.exists():
        return False
    if left.stat().st_size != right.stat().st_size:
        return False
    return sha256_file(left) == sha256_file(right)


def leading_comments(text: str) -> list[str]:
    comments: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            comments.append(line)
            continue
        break
    while comments and comments[-1] == "":
        comments.pop()
    return comments


def manifest_to_text(data: dict[str, Any], plugins: list[dict[str, Any]], header: list[str]) -> str:
    lines: list[str] = list(header)
    if lines:
        lines.append("")

    scalar_keys = ["type", "image", "server", "java", "notes"]
    for key in scalar_keys:
        if key in data:
            lines.extend(emit_yaml_value(key, data[key]))

    for key in ("extends", "templates"):
        if key in data:
            lines.extend(emit_yaml_value(key, data[key]))

    other_keys = sorted(
        key
        for key in data
        if not key.startswith("_")
        and key not in set(scalar_keys)
        and key not in {"extends", "templates", "plugins"}
    )
    for key in other_keys:
        lines.extend(emit_yaml_value(key, data[key]))

    if lines and lines[-1] != "":
        lines.append("")
    lines.extend(emit_yaml_value("plugins", plugins))
    return "\n".join(lines).rstrip() + "\n"


def ordered_plugin(entry: dict[str, Any]) -> dict[str, Any]:
    ordered: dict[str, Any] = {}
    for key in ("id", "file", "sha256", "status", "note", "enabled", "remove"):
        if key in entry:
            ordered[key] = entry[key]
    for key in entry:
        if key not in ordered:
            ordered[key] = entry[key]
    return ordered


def normalize_plugin_name(value: str) -> str:
    value = value.strip()
    value = re.sub(r"[^A-Za-z0-9]+", "-", value)
    value = value.strip("-").lower()
    return value or "plugin"


def strip_version_suffix(stem: str) -> str:
    patterns = [
        r"[-_ ]?v?\d+(?:\.\d+)*(?:[-+_][A-Za-z0-9.]+)*$",
        r"[-_ ]?mc\.\d+(?:\.\d+)*$",
    ]
    result = stem
    for pattern in patterns:
        result = re.sub(pattern, "", result, flags=re.IGNORECASE)
    return result.strip("-_ .") or stem


def plugin_name_from_jar(path: Path) -> str | None:
    try:
        with zipfile.ZipFile(path) as jar:
            for candidate in ("plugin.yml", "paper-plugin.yml"):
                try:
                    raw = jar.read(candidate).decode("utf-8", errors="replace")
                except KeyError:
                    continue
                for line in raw.splitlines():
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#"):
                        continue
                    if stripped.lower().startswith("name:"):
                        return str(parse_scalar(stripped.split(":", 1)[1]))
    except zipfile.BadZipFile:
        return None
    return None


def plugin_id_from_vendor_jar(path: Path) -> str:
    plugin_name = plugin_name_from_jar(path)
    if plugin_name:
        return normalize_plugin_name(plugin_name)
    return normalize_plugin_name(strip_version_suffix(path.stem))


def command_init(args: argparse.Namespace) -> None:
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    VENDOR_PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
    VENDOR_SERVER_DIR.mkdir(parents=True, exist_ok=True)

    content = """# Common plugins shared by every server.
type: purpur
plugins: []
"""
    write_new(manifest_path("common"), content, args.force)


def command_create(args: argparse.Namespace) -> None:
    if args.no_extends and args.extends:
        fail("--no-extends cannot be used with --extends")
    extends = [] if args.no_extends else (args.extends or ["common"])
    lines = [
        f"# Manifest for the {args.name} server.",
        f"type: {args.type}",
    ]

    if extends:
        lines.append("extends:")
        for parent in extends:
            lines.append(f"  - {parent}")

    lines.extend(
        [
            "",
            "plugins: []",
            "",
        ]
    )

    write_new(manifest_path(args.name), "\n".join(lines), args.force)


def command_add_plugin(args: argparse.Namespace) -> None:
    path = manifest_path(args.manifest)
    text = read_text(path)

    if args.id in set(plugin_ids(text)) and not args.force:
        fail(f"plugin id already exists in {path.name}: {args.id}")

    sha256 = args.sha256
    if args.hash:
        plugin_file = VENDOR_PLUGIN_DIR / args.file
        if not plugin_file.exists():
            fail(f"cannot hash missing vendor plugin: {plugin_file}")
        sha256 = sha256_file(plugin_file)

    block = [
        f"  - id: {args.id}",
        f"    file: {args.file}",
    ]
    if sha256:
        block.append(f"    sha256: \"{sha256}\"")
    if args.status:
        block.append(f"    status: {args.status}")
    if args.note:
        block.append(f"    note: \"{args.note}\"")

    block_text = "\n".join(block)
    if "plugins: []" in text:
        text = text.replace("plugins: []", f"plugins:\n{block_text}", 1)
    elif text.rstrip().endswith("plugins:"):
        text = text.rstrip() + "\n" + block_text + "\n"
    else:
        text = text.rstrip() + "\n" + block_text + "\n"

    path.write_text(text, encoding="utf-8", newline="\n")
    print(f"added {args.id} to {path.relative_to(ROOT)}")


def command_add_vendor_plugins(args: argparse.Namespace) -> None:
    path = manifest_path(args.manifest)
    text = read_text(path)
    data = load_manifest(args.manifest)
    plugins = [dict(plugin) for plugin in normalize_plugins(data, validate_required=False)]

    jars = sorted(VENDOR_PLUGIN_DIR.glob("*.jar"), key=lambda item: item.name.lower())
    if not jars:
        fail(f"no jar files found in {VENDOR_PLUGIN_DIR.relative_to(ROOT)}")

    by_id = {str(plugin["id"]): plugin for plugin in plugins}
    by_file = {
        str(plugin.get("file")): plugin
        for plugin in plugins
        if plugin.get("file")
    }

    added: list[str] = []
    updated: list[str] = []
    unchanged: list[str] = []
    replaced: list[str] = []

    for jar in jars:
        file_name = jar.name
        sha256 = sha256_file(jar)

        if file_name in by_file:
            plugin = by_file[file_name]
            if plugin.get("sha256") == sha256:
                unchanged.append(str(plugin["id"]))
                continue
            plugin["sha256"] = sha256
            updated.append(str(plugin["id"]))
            continue

        plugin_id_value = plugin_id_from_vendor_jar(jar)
        if plugin_id_value in by_id:
            plugin = by_id[plugin_id_value]
            if not args.force:
                fail(
                    f"derived plugin id {plugin_id_value!r} from {file_name} "
                    f"already exists for {plugin.get('file')} "
                    "(use --force to replace that entry)"
                )
            old_file = plugin.get("file")
            plugin["file"] = file_name
            plugin["sha256"] = sha256
            by_file.pop(str(old_file), None)
            by_file[file_name] = plugin
            replaced.append(plugin_id_value)
            continue

        plugin = {
            "id": plugin_id_value,
            "file": file_name,
            "sha256": sha256,
        }
        plugins.append(plugin)
        by_id[plugin_id_value] = plugin
        by_file[file_name] = plugin
        added.append(plugin_id_value)

    plugins = [ordered_plugin(plugin) for plugin in plugins]
    output = manifest_to_text(data, plugins, leading_comments(text))

    if not args.dry_run:
        path.write_text(output, encoding="utf-8", newline="\n")

    action = "would update" if args.dry_run else "updated"
    print(f"{action} {path.relative_to(ROOT)}")
    print(f"  added: {len(added)}")
    print(f"  hashed/updated: {len(updated)}")
    print(f"  replaced: {len(replaced)}")
    print(f"  unchanged: {len(unchanged)}")
    if args.verbose:
        for label, values in (
            ("added", added),
            ("hashed/updated", updated),
            ("replaced", replaced),
            ("unchanged", unchanged),
        ):
            if values:
                print(f"{label}:")
                for value in values:
                    print(f"  - {value}")


def command_remove_plugin(args: argparse.Namespace) -> None:
    if args.local and args.override:
        fail("--local and --override cannot be used together")

    path = manifest_path(args.manifest)
    text = read_text(path)
    data = load_manifest(args.manifest)
    plugins = [dict(plugin) for plugin in normalize_plugins(data, validate_required=False)]

    direct_index = next(
        (index for index, plugin in enumerate(plugins) if str(plugin["id"]) == args.id),
        None,
    )
    direct_plugin = plugins[direct_index] if direct_index is not None else None

    action: str
    if args.local:
        if direct_index is None:
            fail(f"plugin id does not exist in {path.name}: {args.id}")
        plugins.pop(direct_index)
        action = f"removed local plugin {args.id}"
    elif args.override:
        remove_entry = ordered_plugin({"id": args.id, "remove": True})
        if direct_index is None:
            plugins.append(remove_entry)
            action = f"added remove override for {args.id}"
        elif plugin_is_removed(direct_plugin):
            action = f"remove override already exists for {args.id}"
        else:
            plugins[direct_index] = remove_entry
            action = f"replaced local plugin {args.id} with remove override"
    elif direct_index is not None:
        if plugin_is_removed(direct_plugin):
            action = f"remove override already exists for {args.id}"
        else:
            plugins.pop(direct_index)
            action = f"removed local plugin {args.id}"
    else:
        resolved_ids = {
            str(plugin["id"])
            for plugin in resolve_manifest(args.manifest).get("plugins", [])
        }
        if args.id not in resolved_ids:
            fail(f"plugin id is not present in effective manifest {path.name}: {args.id}")
        plugins.append(ordered_plugin({"id": args.id, "remove": True}))
        action = f"added remove override for inherited plugin {args.id}"

    output = manifest_to_text(data, [ordered_plugin(plugin) for plugin in plugins], leading_comments(text))

    verb = "would update" if args.dry_run else "updated"
    print(f"{verb} {path.relative_to(ROOT)}")
    print(f"  {action}")

    if not args.dry_run:
        path.write_text(output, encoding="utf-8", newline="\n")


def command_list(args: argparse.Namespace) -> None:
    if not MANIFEST_DIR.exists():
        print("no manifests directory")
        return

    for path in sorted(MANIFEST_DIR.glob("*.yml")):
        if args.resolved:
            resolved = resolve_manifest(path.stem)
            ids = [str(plugin["id"]) for plugin in resolved["plugins"]]
        else:
            data = load_manifest(path.stem)
            ids = direct_plugin_ids(data)
        suffix = f" ({len(ids)} plugins)" if ids else " (no plugins)"
        print(f"{path.stem}{suffix}")
        if args.plugins:
            for plugin_id in ids:
                print(f"  - {plugin_id}")


def command_validate(args: argparse.Namespace) -> None:
    if not MANIFEST_DIR.exists():
        fail(f"{MANIFEST_DIR.relative_to(ROOT)} does not exist")

    errors: list[str] = []
    for path in sorted(MANIFEST_DIR.glob("*.yml")):
        try:
            data = load_manifest(path.stem)
            plugins = normalize_plugins(data)
            resolve_manifest(path.stem)
        except SystemExit:
            raise
        except Exception as exc:
            errors.append(f"{path.name}: {exc}")
            continue

        if not is_root_manifest(path.stem):
            parents = data.get("extends", [])
            if not parents:
                errors.append(
                    f"{path.name}: instance manifest should extend a common layer"
                )

        for parent in data.get("extends", []):
            if not manifest_path(str(parent)).exists():
                errors.append(f"{path.name}: missing parent manifest {parent}")

        for plugin in plugins:
            if plugin_is_removed(plugin):
                continue
            file_name = str(plugin.get("file", ""))
            vendor_file = VENDOR_PLUGIN_DIR / file_name
            if args.check_files and not vendor_file.exists():
                errors.append(f"{path.name}: missing vendor plugin {file_name}")
                continue
            if args.check_hashes:
                if not plugin.get("sha256"):
                    errors.append(f"{path.name}: plugin {plugin['id']} missing sha256")
                    continue
                if not vendor_file.exists():
                    errors.append(f"{path.name}: missing vendor plugin {file_name}")
                    continue
                actual = sha256_file(vendor_file)
                if str(plugin["sha256"]).lower() != actual.lower():
                    errors.append(
                        f"{path.name}: sha256 mismatch for {plugin['id']} "
                        f"({file_name})"
                    )

        resolved = resolve_manifest(path.stem)
        server_file = safe_server_file_name(resolved.get("server"), required=False)
        if args.check_files and server_file:
            vendor_server = VENDOR_SERVER_DIR / server_file
            if not vendor_server.exists():
                errors.append(f"{path.name}: missing vendor server jar {server_file}")

    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)

    print("manifest validation passed")


def command_apply_server(args: argparse.Namespace) -> None:
    resolved = resolve_manifest(args.manifest)
    server_value = args.server if args.server else resolved.get("server")
    server_file = safe_server_file_name(server_value, required=False)
    if not server_file and args.auto_single:
        server_file = auto_single_server_file(required=not args.optional)
    if not server_file and not args.optional:
        fail("manifest is missing server jar file; set server, use --server, or use --auto-single")
    if not server_file:
        print(f"no server jar selected for {resolved['name']}; skipped")
        return

    source = VENDOR_SERVER_DIR / server_file
    if not source.exists():
        fail(f"missing vendor server jar: {display_path(source)}")

    target_root = resolve_target_root(args.target, str(resolved["name"]))
    destination = target_root / args.output

    unchanged = files_match(source, destination)
    action = "would apply" if args.dry_run else "applied"
    state = "unchanged" if unchanged else "copied/updated"
    print(
        f"{action} server jar for {resolved['name']} -> {display_path(destination)}"
    )
    print(f"  source: {display_path(source)}")
    print(f"  state: {state}")

    if args.dry_run or unchanged:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def command_apply_plugins(args: argparse.Namespace) -> None:
    resolved = resolve_manifest(args.manifest)
    target_root = resolve_target_root(args.target, str(resolved["name"]))
    plugins_dir = target_root / "plugins"
    selected = selected_plugin_files(resolved, require_hashes=args.check_hashes)

    desired_names = {str(plugin["file"]) for plugin in selected}
    existing_jars = (
        {path.name: path for path in plugins_dir.glob("*.jar")}
        if plugins_dir.exists()
        else {}
    )

    copied: list[str] = []
    unchanged: list[str] = []
    stale = sorted(set(existing_jars) - desired_names, key=str.lower)
    pruned: list[str] = []

    for plugin in selected:
        source = Path(plugin["source"])
        destination = plugins_dir / str(plugin["file"])
        if files_match(source, destination):
            unchanged.append(str(plugin["file"]))
            continue
        copied.append(str(plugin["file"]))
        if not args.dry_run:
            plugins_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

    if args.prune:
        for file_name in stale:
            pruned.append(file_name)
            if not args.dry_run:
                existing_jars[file_name].unlink()

    verb = "would apply" if args.dry_run else "applied"
    print(f"{verb} plugins for {resolved['name']} -> {display_path(plugins_dir)}")
    print(f"  selected: {len(selected)}")
    print(f"  copied/updated: {len(copied)}")
    print(f"  unchanged: {len(unchanged)}")
    if args.prune:
        print(f"  pruned: {len(pruned)}")
    else:
        print(f"  stale jars left in place: {len(stale)}")

    if args.verbose:
        for label, values in (
            ("copied/updated", copied),
            ("unchanged", unchanged),
            ("pruned", pruned),
            ("stale", stale if not args.prune else []),
        ):
            if values:
                print(f"{label}:")
                for value in values:
                    print(f"  - {value}")


def emit_yaml_value(key: str, value: Any) -> list[str]:
    if isinstance(value, list):
        if not value:
            return [f"{key}: []"]
        lines = [f"{key}:"]
        for item in value:
            if isinstance(item, dict):
                first = True
                for item_key, item_value in item.items():
                    prefix = "  - " if first else "    "
                    lines.append(f"{prefix}{item_key}: {format_scalar(item_value)}")
                    first = False
            else:
                lines.append(f"  - {format_scalar(item)}")
        return lines
    return [f"{key}: {format_scalar(value)}"]


def format_scalar(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    text = str(value)
    if not text or any(ch in text for ch in ":#[]{}\"'") or text.strip() != text:
        return json.dumps(text, ensure_ascii=False)
    return text


def command_resolve(args: argparse.Namespace) -> None:
    resolved = resolve_manifest(args.manifest)
    if args.format == "json":
        print(json.dumps(resolved, indent=2, ensure_ascii=False))
        return

    ordered_keys = [
        "name",
        "type",
        "image",
        "server",
        "java",
        "extends",
        "sources",
        "templates",
        "plugins",
    ]
    printed: set[str] = set()
    for key in ordered_keys:
        if key in resolved:
            print("\n".join(emit_yaml_value(key, resolved[key])))
            printed.add(key)
    for key in sorted(set(resolved) - printed):
        print("\n".join(emit_yaml_value(key, resolved[key])))


def command_show(args: argparse.Namespace) -> None:
    resolved = resolve_manifest(args.manifest)
    print(f"manifest: {resolved['name']}")
    print(f"type: {resolved.get('type', '(unset)')}")
    if resolved.get("server"):
        print(f"server: {resolved['server']}")
    if resolved.get("image"):
        print(f"image: {resolved['image']}")
    print("inheritance: " + " -> ".join(resolved.get("sources", [])))
    print("templates:")
    for template in resolved.get("templates", []):
        print(f"  - {template}")

    plugins = resolved.get("plugins", [])
    print(f"plugins: {len(plugins)}")
    for plugin in plugins:
        file_name = plugin.get("file", "(no file)")
        status = f" [{plugin['status']}]" if plugin.get("status") else ""
        print(f"  - {plugin['id']}: {file_name}{status}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create and maintain manifest files.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="create manifests/common.yml")
    p_init.add_argument("--force", action="store_true", help="overwrite existing files")
    p_init.set_defaults(func=command_init)

    p_create = sub.add_parser("create", help="create a server manifest")
    p_create.add_argument("name", help="server manifest name, e.g. survival")
    p_create.add_argument("--type", default="purpur", help="server type, default: purpur")
    p_create.add_argument(
        "--extends",
        action="append",
        help="parent manifest name; default: common when omitted",
    )
    p_create.add_argument(
        "--no-extends",
        action="store_true",
        help="create a root/common manifest with no parent",
    )
    p_create.add_argument("--force", action="store_true", help="overwrite existing file")
    p_create.set_defaults(func=command_create)

    p_add = sub.add_parser("add-plugin", help="add a plugin entry to a manifest")
    p_add.add_argument("manifest", help="manifest name, e.g. survival")
    p_add.add_argument("id", help="plugin id, e.g. ecoitems")
    p_add.add_argument("file", help="jar file name under vendor/plugins")
    p_add.add_argument("--sha256", help="known sha256 hash")
    p_add.add_argument("--hash", action="store_true", help="compute sha256 from vendor file")
    p_add.add_argument("--status", help="optional status, e.g. testing")
    p_add.add_argument("--note", help="optional short note")
    p_add.add_argument("--force", action="store_true", help="allow duplicate id append")
    p_add.set_defaults(func=command_add_plugin)

    p_add_vendor = sub.add_parser(
        "add-vendor-plugins",
        help="add every jar under vendor/plugins to a manifest with sha256 hashes",
    )
    p_add_vendor.add_argument("manifest", help="manifest name, e.g. common or survival")
    p_add_vendor.add_argument(
        "--force",
        action="store_true",
        help="replace an existing entry when a newly derived id already exists",
    )
    p_add_vendor.add_argument(
        "--dry-run",
        action="store_true",
        help="show what would change without writing the manifest",
    )
    p_add_vendor.add_argument(
        "--verbose",
        action="store_true",
        help="print plugin ids grouped by action",
    )
    p_add_vendor.set_defaults(func=command_add_vendor_plugins)

    p_remove = sub.add_parser("remove-plugin", help="remove or disable a plugin entry")
    p_remove.add_argument("manifest", help="manifest name, e.g. survival")
    p_remove.add_argument("id", help="plugin id, e.g. ecoitems")
    p_remove.add_argument(
        "--local",
        action="store_true",
        help="only remove a direct entry from this manifest",
    )
    p_remove.add_argument(
        "--override",
        action="store_true",
        help="write a remove override even when a direct plugin entry exists",
    )
    p_remove.add_argument(
        "--dry-run",
        action="store_true",
        help="show what would change without writing the manifest",
    )
    p_remove.set_defaults(func=command_remove_plugin)

    p_list = sub.add_parser("list", help="list manifests")
    p_list.add_argument("--plugins", action="store_true", help="show plugin ids")
    p_list.add_argument(
        "--resolved",
        action="store_true",
        help="show effective inherited plugin ids",
    )
    p_list.set_defaults(func=command_list)

    p_validate = sub.add_parser("validate", help="validate manifest basics")
    p_validate.add_argument(
        "--check-files",
        action="store_true",
        help="also require referenced jar files in deploy/vendor/plugins",
    )
    p_validate.add_argument(
        "--check-hashes",
        action="store_true",
        help="also verify sha256 values against vendor plugin jars",
    )
    p_validate.set_defaults(func=command_validate)

    p_apply_server = sub.add_parser(
        "apply-server",
        help="copy the effective manifest server jar into an instance root as server.jar",
    )
    p_apply_server.add_argument("manifest", help="manifest name, e.g. survival")
    p_apply_server.add_argument(
        "--target",
        help="instance root to write into; default is deploy/<manifest>",
    )
    p_apply_server.add_argument(
        "--output",
        default="server.jar",
        help="output jar name under the target root; default: server.jar",
    )
    p_apply_server.add_argument(
        "--server",
        help="server jar file name under ops/vendor/server; overrides manifest server",
    )
    p_apply_server.add_argument(
        "--auto-single",
        action="store_true",
        help="use the only jar in ops/vendor/server when manifest server is unset",
    )
    p_apply_server.add_argument(
        "--dry-run",
        action="store_true",
        help="show what would change without copying",
    )
    p_apply_server.add_argument(
        "--optional",
        action="store_true",
        help="skip instead of failing when the manifest has no server field",
    )
    p_apply_server.set_defaults(func=command_apply_server)

    p_apply_plugins = sub.add_parser(
        "apply-plugins",
        help="copy effective manifest plugin jars into a server plugins directory",
    )
    p_apply_plugins.add_argument("manifest", help="manifest name, e.g. survival")
    p_apply_plugins.add_argument(
        "--target",
        help=(
            "server root to write into; default is deploy/<manifest>"
        ),
    )
    p_apply_plugins.add_argument(
        "--dry-run",
        action="store_true",
        help="show what would change without copying or pruning",
    )
    p_apply_plugins.add_argument(
        "--prune",
        action="store_true",
        help="remove stale .jar files from the target plugins directory",
    )
    p_apply_plugins.add_argument(
        "--check-hashes",
        action="store_true",
        help="require and verify sha256 values for selected plugin jars",
    )
    p_apply_plugins.add_argument(
        "--verbose",
        action="store_true",
        help="print jar file names grouped by action",
    )
    p_apply_plugins.set_defaults(func=command_apply_plugins)

    p_resolve = sub.add_parser("resolve", help="print an effective manifest")
    p_resolve.add_argument("manifest", help="manifest name, e.g. survival")
    p_resolve.add_argument(
        "--format",
        choices=["yaml", "json"],
        default="yaml",
        help="output format, default: yaml",
    )
    p_resolve.set_defaults(func=command_resolve)

    p_show = sub.add_parser("show", help="show an effective manifest summary")
    p_show.add_argument("manifest", help="manifest name, e.g. survival")
    p_show.set_defaults(func=command_show)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
