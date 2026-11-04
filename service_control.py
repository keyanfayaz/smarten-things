#!/usr/bin/env python3
"""Install/start/stop the private per-user macOS LaunchAgent. Never reads tasks."""
import argparse
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys

from things_service import DEFAULT_TOKEN, init_token, load_token
from things_readonly import AdapterError
from dashboard_client import ThingsClient

LABEL = "dev.keyanfayaz.things-readonly"
AGENT = Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")
ROOT = Path(__file__).resolve().parent
RUNTIME = DEFAULT_TOKEN.parent / "app"
RUNTIME_FILES = ("things_readonly.py", "things_access.py", "things_service.py",
                 "dashboard_client.py", "things_mcp.py", "things_previews.py", "verify_live.py",
                 "things_changes.py", "things_native_write.py", "things_change_client.py",
                 "src/read.applescript", "src/read_item.applescript", "src/write_task.applescript")


def agent_definition(root=RUNTIME, python=sys.executable, token=DEFAULT_TOKEN):
    return {"Label": LABEL,
            "ProgramArguments": [str(Path(python).resolve()), "-B", str(root / "things_service.py"), "--token-file", str(token)],
            "WorkingDirectory": str(root), "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False}, "ThrottleInterval": 30,
            "LimitLoadToSessionType": "Aqua", "ProcessType": "Background", "Umask": 0o077,
            "StandardOutPath": "/dev/null", "StandardErrorPath": "/dev/null"}


def install_runtime(source=ROOT, destination=RUNTIME):
    """Copy only reviewed code, never a repository, configuration or task data.

    LaunchAgents should not depend on Documents/Desktop privacy access or cloud
    file hydration. No Full Disk Access or other security setting is required.
    """
    for directory in (destination, destination / "src"):
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        info = directory.stat()
        if directory.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise AdapterError("Runtime directory must be owner-only")
    for name in RUNTIME_FILES:
        content = (source / name).read_bytes()
        fd = os.open(destination / name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as target:
            target.write(content)


def control(action):
    target = f"gui/{os.getuid()}"
    if action == "request-access":
        # Authenticate the fixed, task-free endpoint and match its PID to the
        # exact launchd job. Refuse old daemons without the signal capability.
        report = ThingsClient(DEFAULT_TOKEN).get('/v1/diagnostics')
        if (type(report) is not dict
                or type(report.get("preflight")) is not dict
                or type(report.get("local_consent")) is not dict
                or report.get("probe_context") != "service_process"
                or report.get("denial_latched") is not False
                or report.get("preflight", {}).get("automation") != "consent_required"
                or report.get("local_consent", {}).get("supported") is not True
                or report.get("local_consent", {}).get("attempted") is not False
                or type(report.get("service_pid")) is not int or report["service_pid"] <= 0):
            raise AdapterError("Service is not ready for a first local consent request; no signal sent")
        state = subprocess.run(["/bin/launchctl", "print", target + "/" + LABEL],
                               capture_output=True, text=True, timeout=15, check=False)
        pids = re.findall(r"^\s*pid = ([0-9]+)\s*$", state.stdout, re.MULTILINE)
        if state.returncode or pids != [str(report["service_pid"])]:
            raise AdapterError("Service identity changed or is unverified; no signal sent")
        command = ["/bin/launchctl", "kill", "SIGUSR1", target + "/" + LABEL]
    elif action == "enable-writes":
        report = ThingsClient(DEFAULT_TOKEN).get('/v1/write-capability')
        if (type(report) is not dict or report.get('supported') is not True or report.get('startup_default') != 'disabled'
                or type(report.get('service_pid')) is not int or report['service_pid'] <= 0):
            raise AdapterError('Service write capability is unverified; no signal sent')
        state = subprocess.run(["/bin/launchctl", "print", target + "/" + LABEL], capture_output=True, text=True, timeout=15, check=False)
        pids = re.findall(r"^\s*pid = ([0-9]+)\s*$", state.stdout, re.MULTILINE)
        if state.returncode or pids != [str(report['service_pid'])]:
            raise AdapterError('Service identity changed; no signal sent')
        command = ["/bin/launchctl", "kill", "SIGUSR2", target + "/" + LABEL]
    elif action in {"install", "update"}:
        if action == "install" and (AGENT.exists() or AGENT.is_symlink()):
            raise AdapterError("LaunchAgent already exists; inspect it before changing installation")
        if action == "update":
            if AGENT.is_symlink():
                raise AdapterError("Unexpected LaunchAgent symlink")
            with AGENT.open("rb") as source:
                existing = plistlib.load(source)
            arguments = existing.get("ProgramArguments", [])
            if (existing.get("Label") != LABEL or len(arguments) < 3
                    or Path(arguments[-3]).name != "things_service.py"):
                raise AdapterError("Existing LaunchAgent is not this service")
            subprocess.run(["/bin/launchctl", "bootout", target + "/" + LABEL],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=False)
        if not DEFAULT_TOKEN.exists():
            init_token(DEFAULT_TOKEN)
        load_token(DEFAULT_TOKEN)
        install_runtime()
        AGENT.parent.mkdir(parents=True, exist_ok=True)
        if action == "install":
            destination = AGENT.open("xb")
        else:
            destination = AGENT.open("wb")
        with destination:
            os.fchmod(destination.fileno(), 0o600)
            plistlib.dump(agent_definition(), destination)
        command = ["/bin/launchctl", "bootstrap", target, str(AGENT)]
    elif action == "start":
        command = ["/bin/launchctl", "bootstrap", target, str(AGENT)]
    elif action == "stop":
        command = ["/bin/launchctl", "bootout", target + "/" + LABEL]
    elif action == "restart":
        command = ["/bin/launchctl", "kickstart", "-k", target + "/" + LABEL]
    else:
        raise ValueError
    result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=False)
    if result.returncode:
        raise AdapterError("LaunchAgent action failed; check signed-in GUI session and existing service state")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["install", "update", "start", "stop", "restart", "request-access", "enable-writes"])
    args = parser.parse_args()
    try:
        control(args.action)
        if args.action == "request-access":
            print("Local consent signal sent; review the macOS prompt yourself. No task read was performed")
        else:
            print("LaunchAgent action succeeded; no task read was performed")
        return 0
    except (OSError, AdapterError, subprocess.TimeoutExpired):
        print("LaunchAgent action failed; existing files and task data were not removed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
