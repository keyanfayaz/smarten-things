#!/usr/bin/env python3
"""Build a private local-marketplace plugin from an explicit source allowlist.

No installation, task reads, credential access, hooks or network operations.
"""
import argparse
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parent
VERSION = "2.2.1"
SOURCES = (
    "things_mcp.py", "dashboard_client.py", "things_service.py",
    "things_readonly.py", "things_access.py", "src/read.applescript",
    "things_previews.py", "src/read_item.applescript",
    "things_changes.py", "things_native_write.py", "things_change_client.py", "src/write_task.applescript",
)
DOCS = ("SETUP.md", "API.md", "CONTRACT.md", "DASHBOARD.md", "REMOTE.md", "PLUGIN.md", "PREVIEWS.md")


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as destination:
        json.dump(value, destination, indent=2, ensure_ascii=False)
        destination.write("\n")


def build(output, source=ROOT):
    output = Path(output).absolute()
    source = Path(source)
    # Resolve every known input before creating output. Never walk/copy a repo.
    files = [(source / name, "server/" + name) for name in SOURCES]
    files += [(source / "docs" / name, "docs/" + name) for name in DOCS]
    files += [(source / "plugin/launch_mcp.sh", "scripts/launch_mcp.sh"),
              (source / "plugin/README.md", "README.md")]
    if any(path.is_symlink() or not path.is_file() for path, _ in files):
        raise ValueError("Expected regular allowlisted source files")
    if output.is_symlink():
        raise ValueError("Build destination cannot be a symlink")
    output.mkdir(parents=True, exist_ok=False)
    plugin = output / "plugins/things-readonly"
    plugin.mkdir(parents=True)
    for path, relative in files:
        target = plugin / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        target.chmod(0o644)
    interface = {
        "displayName": "Smarten for Things",
        "shortDescription": "Read open Things tasks",
        "longDescription": "Local, authenticated Things reads. No writes, automatic scans or remote exposure. Requires the existing Things read-only service.",
        "category": "Productivity", "capabilities": ["Read"],
        "defaultPrompt": ["Show my Things Today tasks.", "List my open Things projects."],
    }
    manifest = {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": "things-readonly", "version": VERSION,
        "description": "Private read-only Things 3 access on the service-hosting Mac.",
        "repository": "https://github.com/keyanfayaz/smarten-things",
        "extensions": {"com.openai": {"requires_local_executor": True, "interface": interface}},
    }
    mcp = {"mcpServers": {"things": {"type": "stdio", "command": "/bin/sh",
        "args": ["${PLUGIN_ROOT}/scripts/launch_mcp.sh"], "cwd": "${PLUGIN_ROOT}"}}}
    write_json(plugin / "plugin.json", manifest)
    write_json(plugin / "mcp.json", mcp)
    # Compatibility files refer to the same server; current portable hosts use
    # the root manifest/config. No credentials or machine-specific paths.
    write_json(plugin / ".codex-plugin/plugin.json", {
        "name": manifest["name"], "version": VERSION,
        "description": manifest["description"], "mcpServers": "./.mcp.json",
        "requires_local_executor": True, "interface": interface,
    })
    write_json(plugin / ".mcp.json", mcp)
    write_json(output / ".agents/plugins/marketplace.json", {
        "name": "things-local", "interface": {"displayName": "Smarten for Things"},
        "plugins": [{"name": "things-readonly",
            "source": {"source": "local", "path": "./plugins/things-readonly"},
            "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
            "category": "Productivity"}],
    })
    return plugin


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist/things-local",
                        help="New marketplace directory; existing output is never overwritten")
    args = parser.parse_args(argv)
    try:
        build(args.output)
    except (OSError, ValueError):
        print("Plugin build failed; check source files and choose a new output directory", file=sys.stderr)
        return 1
    print("Local plugin marketplace built; not registered, installed or activated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
