#!/usr/bin/env python3
"""Bounded, read-only Things pages; standard library only. No persistent state."""

import argparse
import configparser
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import subprocess
import sys
from datetime import date, datetime
import selectors
import time
import xml.etree.ElementTree as ET

BUNDLE_ID = "com.culturedcode.ThingsMac"
APP = Path("/Applications/Things3.app")
SCRIPT = Path(__file__).resolve().parent / "src" / "read.applescript"
ITEM_SCRIPT = Path(__file__).resolve().parent / "src" / "read_item.applescript"
LIST_IDS = {"inbox": "TMInboxListSource", "today": "TMTodayListSource", "upcoming": "TMCalendarListSource"}
MAX_OUTPUT = 1024 * 1024
METADATA_FIELDS = frozenset({"project", "scheduled_date", "deadline", "modified_at"})
UNSUPPORTED_FIELDS = ["priority", "progress_history", "when_date", "recurrence"]
FIELDS = frozenset({
    "schema_version", "kind", "container_id", "container_name", "app_version",
    "offset", "limit", "total_count", "returned_count", "next_offset",
    "has_more", "items",
})
ITEM_FIELDS = frozenset({"id", "title", "status"})
DISCOVERY_SCRIPT = '''ObjC.import("AppKit");
const url = $.NSWorkspace.sharedWorkspace.URLForApplicationWithBundleIdentifier("com.culturedcode.ThingsMac");
JSON.stringify({standard_app_registered: !url.isNil() && ObjC.unwrap(url.path) === "/Applications/Things3.app",
visible_running_instances: Number($.NSRunningApplication.runningApplicationsWithBundleIdentifier("com.culturedcode.ThingsMac").count)});'''


class AdapterError(Exception):
    """A safe-to-display diagnostic containing no Things record content."""


def bounded_int(low, high):
    def parse(value):
        try:
            number = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError("Expected an integer") from None
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"Expected {low}..{high}")
        return number
    return parse


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--list", choices=LIST_IDS)
    target.add_argument("--project", metavar="ID", help="Read one open project by ID")
    target.add_argument("--projects", action="store_true", help="Page open project summaries")
    target.add_argument("--doctor", action="store_true", help="Check prerequisites and discovery without Apple Events or task reads")
    parser.add_argument("--config", type=Path, metavar="INI", help="Explicit machine-local settings file; no implicit config lookup")
    parser.add_argument("--metadata", action="store_true", help="Schema 2: public project, activation, due and modification fields")
    parser.add_argument("--offset", type=bounded_int(0, 1000000), default=0)
    parser.add_argument("--limit", type=bounded_int(1, 50), default=None, help="1..50; overrides configured default (otherwise 30)")
    result = parser.parse_args(argv)
    if result.project is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", result.project):
        parser.error("Project ID must contain 1..128 letters, digits, underscores or hyphens")
    return result


def load_config(path=None):
    settings = {"default_limit": 30, "expected_app_version": ""}
    if path is None:
        return settings
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with path.open(encoding="utf-8") as source:
            text = source.read(16385)
        if len(text) > 16384:
            raise ValueError
        parser.read_string(text)
        if parser.defaults() or parser.sections() != ["adapter"]:
            raise ValueError
        if set(parser["adapter"]) - set(settings):
            raise ValueError
        settings["default_limit"] = parser.getint("adapter", "default_limit", fallback=30)
        settings["expected_app_version"] = parser.get("adapter", "expected_app_version", fallback="").strip()
        if not 1 <= settings["default_limit"] <= 50:
            raise ValueError
        version = settings["expected_app_version"]
        if version and not re.fullmatch(r"[A-Za-z0-9.+_-]{1,64}", version):
            raise ValueError
    except (OSError, UnicodeError, ValueError, configparser.Error):
        raise AdapterError("Invalid or unreadable config; expected [adapter], default_limit=1..50, optional expected_app_version") from None
    return settings


def target_for(args):
    if args.list:
        return "list", LIST_IDS[args.list]
    if args.projects:
        return "projects", "open-projects"
    return "project", args.project


def validate_page(data, kind, container_id, offset, limit, metadata=False):
    """Reject unknown fields and inconsistent pagination; never echo input."""
    def require(condition):
        if not condition:
            raise AdapterError("Invalid adapter response; output discarded")

    fields = FIELDS | {"source", "unsupported_fields"} if metadata else FIELDS
    require(type(data) is dict and set(data) == fields)
    require(type(data["schema_version"]) is int and data["schema_version"] == (2 if metadata else 1))
    if metadata:
        require(data["source"] == "things" and data["unsupported_fields"] == UNSUPPORTED_FIELDS)
    require(data["kind"] == kind and data["container_id"] == container_id)
    require(type(data["container_name"]) is str and type(data["app_version"]) is str)
    for key in ("offset", "limit", "total_count", "returned_count"):
        require(type(data[key]) is int and data[key] >= 0)
    require(data["offset"] == offset and data["limit"] == limit)
    require(type(data["items"]) is list)
    expected = min(limit, max(0, data["total_count"] - offset))
    require(len(data["items"]) == data["returned_count"] == expected)
    seen = set()
    for row in data["items"]:
        require(type(row) is dict and set(row) == (ITEM_FIELDS | METADATA_FIELDS if metadata else ITEM_FIELDS))
        require(type(row["id"]) is str and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", row["id"]) is not None)
        require(type(row["title"]) is str and len(row["title"]) <= 8192 and row["status"] == "open")
        if metadata:
            project = row["project"]
            require(project is None or (type(project) is dict and set(project) == {"id", "title"}
                    and type(project["id"]) is str and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", project["id"]) is not None
                    and type(project["title"]) is str and len(project["title"]) <= 8192))
            for key in ("scheduled_date", "deadline", "modified_at"):
                value = row[key]
                if value is not None:
                    require(type(value) is str)
                    try:
                        if key == "modified_at":
                            require(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value) is not None)
                            datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
                        else:
                            require(re.fullmatch(r"\d{4}-\d\d-\d\d", value) is not None)
                            date.fromisoformat(value)
                    except ValueError:
                        require(False)
        require(row["id"] not in seen)
        seen.add(row["id"])
    next_offset = offset + expected
    more = next_offset < data["total_count"]
    require(type(data["has_more"]) is bool and data["has_more"] == more)
    require((type(data["next_offset"]) is int and data["next_offset"] == next_offset)
            if more else data["next_offset"] is None)
    return data


def capabilities(path=None):
    """Inspect only the installed public dictionary; never request task properties."""
    path = path or APP / "Contents/Resources/Things.sdef"
    required = {"id": "text", "name": "text", "status": "status", "project": "project",
                "activation date": "date", "due date": "date", "modification date": "date"}
    try:
        root = ET.parse(path).getroot()
        todo = root.find(".//class[@name='to do']")
        if todo is None:
            raise ValueError
        props = {p.get("name"): p.get("type") for p in todo.findall("property") if p.get("hidden") != "yes"}
        if any(props.get(name) != value for name, value in required.items()):
            raise ValueError
        project = root.find(".//class[@name='project']")
        if project is None or project.get("inherits") != "to do":
            raise ValueError
    except (OSError, ValueError, ET.ParseError):
        raise AdapterError("Installed public scripting dictionary lacks required metadata; no read performed") from None
    return {"metadata_supported": True, "unsupported_fields": UNSUPPORTED_FIELDS,
            "scheduled_date_semantics": "activation date, not the general Things When date",
            "upcoming": "runtime list lookup; unsupported if Things cannot resolve it"}


def validate_item(data, kind, item_id):
    """Validate one explicitly requested open task/project; never echo input."""
    fields = {"schema_version", "source", "kind", "app_version", "time_zone", "item"}
    item_fields = {"id", "title", "status", "project", "scheduled_date", "modified_at", "notes", "deadline"}
    if (kind not in {"task", "project"} or type(item_id) is not str
            or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", item_id) is None
            or type(data) is not dict or set(data) != fields
            or type(data["schema_version"]) is not int or data["schema_version"] != 2
            or data["source"] != "things" or data["kind"] != kind
            or type(data["app_version"]) is not str or len(data["app_version"]) > 64
            or type(data["time_zone"]) is not str
            or re.fullmatch(r"[A-Za-z0-9_+./-]{1,128}", data["time_zone"]) is None
            or type(data["item"]) is not dict or set(data["item"]) != item_fields
            or data["item"]["id"] != item_id):
        raise AdapterError("Invalid adapter response; output discarded")
    # Reuse the existing exact field/date validation without changing page APIs.
    from things_previews import valid_notes
    if not valid_notes(data["item"]["notes"]):
        raise AdapterError("Invalid adapter response; output discarded")
    row = {key: value for key, value in data["item"].items() if key != "notes"}
    page = {"schema_version": 2, "source": "things", "unsupported_fields": UNSUPPORTED_FIELDS,
            "kind": "list", "container_id": "exact-item", "container_name": "Exact item",
            "app_version": data["app_version"], "offset": 0, "limit": 1, "total_count": 1,
            "returned_count": 1, "next_offset": None, "has_more": False, "items": [row]}
    validate_page(page, "list", "exact-item", 0, 1, metadata=True)
    return data


def read_item(kind, item_id, runner=None):
    if kind not in {"task", "project"} or type(item_id) is not str or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", item_id) is None:
        raise AdapterError("Invalid exact-item request")
    capabilities()
    runner = runner or bounded_run
    try:
        result = runner(["/usr/bin/osascript", str(ITEM_SCRIPT), kind, item_id],
                        capture_output=True, text=True, encoding="utf-8", timeout=65, check=False)
    except subprocess.TimeoutExpired:
        raise AdapterError("Read exceeded the 65-second process deadline; stopped without retry") from None
    except (OSError, UnicodeError):
        raise AdapterError("Could not execute or decode the Things reader") from None
    if result.returncode:
        raise AdapterError(classify_failure(result.stderr))
    try:
        data = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise AdapterError("Invalid JSON from reader; output discarded") from None
    return validate_item(data, kind, item_id)


def bounded_run(cmd, **kwargs):
    """Capture in memory with a shared byte cap and a hard process deadline."""
    deadline = time.monotonic() + kwargs.get("timeout", 65)
    try:
        with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
            chunks = {proc.stdout: bytearray(), proc.stderr: bytearray()}
            total = 0
            try:
                with selectors.DefaultSelector() as selector:
                    for stream in chunks:
                        os.set_blocking(stream.fileno(), False)
                        selector.register(stream, selectors.EVENT_READ)
                    while selector.get_map():
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise subprocess.TimeoutExpired(cmd[0], kwargs.get("timeout", 65))
                        for key, _ in selector.select(min(remaining, 0.1)):
                            block = os.read(key.fd, 65536)
                            if not block:
                                selector.unregister(key.fileobj)
                                continue
                            total += len(block)
                            if total > MAX_OUTPUT:
                                raise AdapterError("Reader output exceeded memory limit; discarded")
                            chunks[key.fileobj].extend(block)
                proc.wait(timeout=max(0.001, deadline - time.monotonic()))
                return subprocess.CompletedProcess(cmd, proc.returncode,
                    bytes(chunks[proc.stdout]).decode("utf-8"), bytes(chunks[proc.stderr]).decode("utf-8"))
            except BaseException:
                proc.kill()
                proc.wait()
                raise
    except (OSError, UnicodeError):
        raise AdapterError("Could not execute or decode the Things reader") from None


def classify_failure(stderr):
    # Never forward raw osascript diagnostics: they may contain record titles.
    codes = set(re.findall(r"(?<!\d)-\d{3,6}(?!\d)", stderr))
    if codes & {"-1743", "-1742"}:
        return "Apple Events access denied (-1742/-1743); stopped without retry or settings changes"
    if "-10827" in codes:
        return "App launch/discovery failed (-10827); list access is unverified"
    if "-1728" in codes:
        return "App or object resolution failed (-1728); this alone does not establish permission denial"
    if "-1712" in codes:
        return "Apple Event timed out (-1712); no automatic retry"
    return "Things read failed; no automatic retry (raw diagnostic suppressed)"


def check_environment(expected_version=""):
    if platform.system() != "Darwin":
        raise AdapterError("Live reads require macOS; synthetic tests work on other platforms")
    try:
        with (APP / "Contents" / "Info.plist").open("rb") as source:
            info = plistlib.load(source)
        if info.get("CFBundleIdentifier") != BUNDLE_ID:
            raise ValueError
        executable = info["CFBundleExecutable"]
        if not isinstance(executable, str) or Path(executable).name != executable:
            raise ValueError
        if not (APP / "Contents" / "MacOS" / executable).is_file():
            raise ValueError
        version = info.get("CFBundleShortVersionString", "")
        if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9.+_-]{1,64}", version):
            raise ValueError
    except (OSError, ValueError, KeyError, plistlib.InvalidFileException):
        raise AdapterError("Expected a valid Things app at /Applications/Things3.app") from None
    if expected_version and version != expected_version:
        raise AdapterError("Installed Things version differs from machine-local expected_app_version; no read performed")
    return version


def diagnose(settings, runner=None):
    """Disk metadata + NSWorkspace lookup only. Never send Things an Apple Event."""
    runner = runner or subprocess.run
    mac = platform.system() == "Darwin"
    osa = Path("/usr/bin/osascript").is_file() and os.access("/usr/bin/osascript", os.X_OK)
    checks = {
        "python_3_10_or_newer": {"ok": sys.version_info >= (3, 10)},
        "macos": {"ok": mac},
        "system_osascript": {"ok": osa},
        "reader_source_present": {"ok": SCRIPT.is_file()},
    }
    try:
        version = check_environment()
        expected = settings["expected_app_version"]
        checks["installed_app"] = {"ok": True, "version": version, "version_source": "Info.plist"}
        checks["expected_version"] = {"ok": not expected or expected == version,
                                      "required": bool(expected)}
    except AdapterError as error:
        checks["installed_app"] = {"ok": False, "detail": str(error)}
    checks["app_discovery"] = {"ok": False, "detail": "Not run: requires macOS and executable system osascript"}
    if mac and osa:
        try:
            result = runner(["/usr/bin/osascript", "-l", "JavaScript", "-e", DISCOVERY_SCRIPT],
                            capture_output=True, text=True, encoding="utf-8", timeout=10, check=False)
            if result.returncode:
                raise AdapterError("Discovery probe failed: " + classify_failure(result.stderr))
            data = json.loads(result.stdout)
            if (type(data) is not dict or set(data) != {"standard_app_registered", "visible_running_instances"}
                    or type(data["standard_app_registered"]) is not bool
                    or type(data["visible_running_instances"]) is not int
                    or data["visible_running_instances"] < 0):
                raise ValueError
            checks["app_discovery"] = {"ok": data["standard_app_registered"],
                                       "visible_running_instances": data["visible_running_instances"],
                                       "detail": "Standard app registration lookup; no launch or repair attempted"}
        except AdapterError as error:
            checks["app_discovery"] = {"ok": False, "detail": str(error)}
        except (OSError, UnicodeError, ValueError, subprocess.TimeoutExpired):
            checks["app_discovery"] = {"ok": False, "detail": "Discovery probe failed or timed out; raw output suppressed; no retry"}
    return {"diagnostic_schema_version": 1, "prerequisites_met": all(row["ok"] for row in checks.values()),
            "checks": checks, "apple_events_access": "not_probed", "live_task_reads": "not_probed"}


def read_page(args, runner=None):
    kind, container_id = target_for(args)
    limit = args.limit if args.limit is not None else 30
    metadata = getattr(args, "metadata", False)
    if metadata:
        capabilities()
    runner = runner or bounded_run
    command = ["/usr/bin/osascript", str(SCRIPT), kind, container_id, str(args.offset), str(limit)]
    if metadata:
        command.append("metadata")
    try:
        result = runner(
            command,
            capture_output=True, text=True, encoding="utf-8", timeout=65, check=False,
        )
    except subprocess.TimeoutExpired:
        raise AdapterError("Read exceeded the 65-second process deadline; stopped without retry") from None
    except (OSError, UnicodeError):
        raise AdapterError("Could not execute or decode the Things reader") from None
    if result.returncode:
        raise AdapterError(classify_failure(result.stderr))
    try:
        data = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise AdapterError("Invalid JSON from reader; output discarded") from None
    return validate_page(data, kind, container_id, args.offset, limit, metadata)


def main(argv=None):
    args = arguments(argv)
    try:
        settings = load_config(args.config)
        if args.doctor:
            report = diagnose(settings)
            print(json.dumps(report, indent=2))
            return 0 if report["prerequisites_met"] else 1
        args.limit = args.limit if args.limit is not None else settings["default_limit"]
        check_environment(settings["expected_app_version"])
        page = read_page(args)
    except AdapterError as error:
        print(str(error), file=sys.stderr)
        return 1
    # Emit only a fully validated page. This data is private even without notes.
    print(json.dumps(page, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
