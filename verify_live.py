#!/usr/bin/env python3
"""Explicit live verification. Emits only fixed status labels, never task data."""
import argparse
import json

import things_readonly as adapter
from things_access import permission_status
from things_service import DEFAULT_TOKEN, LiveReader
from dashboard_client import ThingsClient


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extended", action="store_true", help="One item each from Inbox, Today, Upcoming, project catalog and first open project")
    parser.add_argument("--http", action="store_true", help="Verify installed localhost service instead of direct reads")
    args = parser.parse_args(argv)
    report = {}
    if not args.http:
        status = permission_status()
        if status != "granted":
            print(json.dumps({"live_read": "pending", "apple_events_access": status, "tasks_read": False}))
            return 1
    reader = LiveReader()
    client = ThingsClient(DEFAULT_TOKEN)
    targets = ["inbox", "today", "upcoming", "projects"] if args.extended else ["inbox"]
    for target in targets:
        try:
            path = "/v1/projects?limit=1" if target == "projects" else "/v1/lists/" + target + "?limit=1"
            read_args = adapter.arguments((["--projects"] if target == "projects" else ["--list", target]) + ["--metadata", "--limit", "1"])
            page = client.get(path) if args.http else reader(read_args)
            report[target] = "passed_nonempty" if page["items"] else "passed_empty"
            if target == "projects" and page["items"]:
                project_id = page["items"][0]["id"]
                project_args = adapter.arguments(["--project", project_id, "--metadata", "--limit", "1"])
                project_page = client.get("/v1/projects/" + project_id + "/tasks?limit=1") if args.http else reader(project_args)
                report["project_tasks"] = "passed_nonempty" if project_page["items"] else "passed_empty"
        except adapter.AdapterError:
            report[target] = "unavailable_no_data_emitted"
            break
    print(json.dumps(report))
    return 0 if report and all(value.startswith("passed_") for value in report.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
