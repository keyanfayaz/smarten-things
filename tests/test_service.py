"""Synthetic real HTTP integration; never invokes Things or reads its task store."""
from contextlib import redirect_stderr, redirect_stdout
import copy
from datetime import date, datetime, timezone
import http.client
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import dashboard_client as dashboard
import service_control
import things_mcp
import things_readonly as adapter
import things_service as service
from test_adapter import synthetic_page

SYNTHETIC_TOKEN = "SYNTHETIC_TOKEN_" + "x" * 32


def metadata_page(args=None):
    args = args or adapter.arguments(["--list", "inbox", "--metadata", "--limit", "2"])
    kind, container = adapter.target_for(args)
    page = synthetic_page(args.offset, args.limit, 3)
    page.update(schema_version=2, kind=kind, container_id=container, source="things",
                unsupported_fields=adapter.UNSUPPORTED_FIELDS)
    for row in page["items"]:
        row.update(project=None, scheduled_date=None, deadline="2026-10-23", modified_at="2026-09-01T12:00:00Z")
    return page


class MetadataTests(unittest.TestCase):
    def validate(self, page):
        return adapter.validate_page(page, "list", "TMInboxListSource", 0, 2, True)

    def test_exact_metadata_contract_and_nulls(self):
        page = metadata_page()
        self.assertEqual(self.validate(page), page)
        page["items"][0]["project"] = {"id": "synthetic-project", "title": "Synthetic"}
        page["items"][0]["modified_at"] = None
        self.validate(page)

    def test_private_fields_dates_and_oversized_strings_rejected(self):
        for key, value in [("deadline", "2026-02-30"), ("scheduled_date", "tomorrow"), ("modified_at", "2026-10-23"),
                           ("modified_at", "2026-99-23T12:00:00Z"), ("project", {"id": "x", "title": "X", "notes": "PRIVATE"}),
                           ("title", "x" * 8193), ("notes", "PRIVATE")]:
            page = metadata_page()
            page["items"][0][key] = value
            with self.subTest(key=key), self.assertRaises(adapter.AdapterError):
                self.validate(page)

    def test_dictionary_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "synthetic.sdef"
            path.write_text('<dictionary><class name="to do"><property name="notes" type="text"/></class></dictionary>')
            with self.assertRaises(adapter.AdapterError):
                adapter.capabilities(path)

    def test_metadata_argv_and_dictionary_preflight(self):
        args = adapter.arguments(["--list", "upcoming", "--metadata", "--limit", "2"])
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(metadata_page(args)), ""))
        with patch.object(adapter, "capabilities") as capabilities:
            adapter.read_page(args, runner=runner)
        capabilities.assert_called_once()
        self.assertEqual(runner.call_args.args[0][-5:], ["list", "TMCalendarListSource", "0", "2", "metadata"])

    def test_capture_limit_and_timeout(self):
        with patch.object(adapter, "MAX_OUTPUT", 100):
            with self.assertRaisesRegex(adapter.AdapterError, "memory limit"):
                adapter.bounded_run([sys.executable, "-c", "print('SYNTHETIC'*100)"], timeout=2)
        with self.assertRaises(subprocess.TimeoutExpired):
            adapter.bounded_run([sys.executable, "-c", "import time; time.sleep(2)"], timeout=0.1)


class CredentialTests(unittest.TestCase):
    def test_token_private_non_overwriting_no_print(self):
        with tempfile.TemporaryDirectory() as tmp:
            token = Path(tmp) / "token"
            service.init_token(token)
            self.assertEqual(token.stat().st_mode & 0o777, 0o600)
            self.assertGreaterEqual(len(service.load_token(token)), 43)
            with self.assertRaises(FileExistsError):
                service.init_token(token)
            token.chmod(0o644)
            with self.assertRaises(adapter.AdapterError):
                service.load_token(token)
            token.chmod(0o600)
            link = Path(tmp) / "link"
            link.symlink_to(token)
            with self.assertRaises(adapter.AdapterError):
                service.load_token(link)


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.reader = Mock(side_effect=metadata_page)
        self.server = service.Server(("127.0.0.1", 0), SYNTHETIC_TOKEN, self.reader)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method="GET", path="/v1/lists/inbox?limit=2", auth=True, extra=None):
        headers = {"Authorization": "Bearer " + SYNTHETIC_TOKEN} if auth else {}
        headers.update(extra or {})
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=2)
        try:
            connection.request(method, path, headers=headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), json.loads(response.read())
        finally:
            connection.close()

    def test_real_http_authenticated_metadata_and_client(self):
        status, headers, page = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual(page["source"], "things")
        with tempfile.TemporaryDirectory() as tmp:
            token = Path(tmp) / "token"
            token.write_text(SYNTHETIC_TOKEN)
            token.chmod(0o600)
            client = dashboard.ThingsClient(token, "http://127.0.0.1:" + str(self.server.server_port))
            self.assertEqual(client.get("/v1/lists/inbox?limit=2"), page)
            token.write_text("x" * 43)
            with self.assertRaises(adapter.AdapterError):
                client.get("/v1/lists/inbox?limit=2")

    def test_auth_for_all_routes_and_no_read_on_rejection(self):
        for path in ["/v1/health", "/v1/capabilities", "/v1/lists/today", "/v1/projects", "/unknown"]:
            self.assertEqual(self.request(path=path, auth=False)[0], 401)
        self.assertEqual(self.request(extra={"Authorization": "Bearer wrong"})[0], 401)
        self.reader.assert_not_called()

    def test_host_origin_and_mutations_rejected(self):
        self.assertEqual(self.request(extra={"Host": "attacker.invalid"})[0], 403)
        self.assertEqual(self.request(extra={"Origin": "http://127.0.0.1"})[0], 403)
        for method in ["POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"]:
            self.assertEqual(self.request(method=method)[0], 405)
        self.reader.assert_not_called()

    def test_fixed_routes_and_bounds(self):
        for path in ["/v1/lists/logbook", "/v1/projects/x/complete", "/v1/projects/%22/tasks"]:
            self.assertEqual(self.request(path=path)[0], 404)
        for query in ["limit=51", "offset=-1", "limit=1&limit=2", "token=secret", "limit=", "offset=1000001"]:
            self.assertEqual(self.request(path="/v1/lists/inbox?" + query)[0], 400)
        self.reader.assert_not_called()

    def test_error_sanitization_and_no_logs(self):
        self.reader.side_effect = adapter.AdapterError("SYNTHETIC_PRIVATE_TITLE")
        output = io.StringIO()
        with redirect_stderr(output), redirect_stdout(output):
            status, headers, body = self.request()
        self.assertEqual(status, 503)
        self.assertNotIn("SYNTHETIC_PRIVATE_TITLE", json.dumps(body) + output.getvalue())
        self.assertEqual(output.getvalue(), "")

    def test_health_is_task_free_and_only_loopback_allowed(self):
        self.assertEqual(self.request(path="/v1/health")[0], 200)
        self.reader.assert_not_called()
        with self.assertRaises(ValueError):
            service.Server(("0.0.0.0", 0), SYNTHETIC_TOKEN)

    def test_disconnected_client_does_not_break_next_request(self):
        import socket
        with socket.create_connection(self.server.server_address) as connection:
            connection.sendall(("GET /v1/health HTTP/1.0\r\nHost: 127.0.0.1:" + str(self.server.server_port)
                               + "\r\nAuthorization: Bearer " + SYNTHETIC_TOKEN + "\r\n\r\n").encode())
        self.assertEqual(self.request(path="/v1/health")[0], 200)


class LiveGateTests(unittest.TestCase):
    def test_permission_denial_latches_no_task_read_or_retry(self):
        reader = service.LiveReader()
        with patch.object(service, "permission_status", return_value="denied") as permission, patch.object(adapter, "read_page") as read:
            for _ in range(2):
                with self.assertRaises(adapter.AdapterError):
                    reader(adapter.arguments(["--list", "inbox"]))
            permission.assert_called_once_with()
            read.assert_not_called()

    def test_only_one_reader_and_no_launch(self):
        reader = service.LiveReader()
        reader.lock.acquire()
        try:
            with self.assertRaises(service.BusyError):
                reader(None)
        finally:
            reader.lock.release()
        report = {"prerequisites_met": True, "checks": {"app_discovery": {"visible_running_instances": 0}}}
        with patch.object(service, "permission_status", return_value="granted"), patch.object(adapter, "diagnose", return_value=report), patch.object(adapter, "read_page") as read:
            with self.assertRaises(adapter.AdapterError):
                reader(None)
            read.assert_not_called()


class MCPTests(unittest.TestCase):
    def test_discovery_and_initialize_never_read(self):
        client = Mock()
        for method in ["initialize", "tools/list", "ping"]:
            result = things_mcp.dispatch({"jsonrpc": "2.0", "id": 1, "method": method}, client)
            self.assertIn("result", result)
        self.assertTrue(things_mcp.TOOLS[0]["annotations"]["readOnlyHint"])
        client.get.assert_not_called()

    def test_only_fixed_read_tool_and_no_notifications_execute(self):
        client = Mock()
        for arguments in [{"target": "complete"}, {"target": "today", "limit": True},
                          {"target": "project", "project_id": "x/../../"}, {"target": "today", "url": "evil"}]:
            response = things_mcp.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "read_things_page", "arguments": arguments}}, client)
            self.assertTrue(response["result"]["isError"])
        self.assertIsNone(things_mcp.dispatch({"jsonrpc": "2.0", "method": "tools/call"}, client))
        client.get.assert_not_called()

    def test_successful_read_and_unsupported_method(self):
        client = Mock()
        client.get.return_value = {"synthetic": True}
        response = things_mcp.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "read_things_page", "arguments": {"target": "today", "limit": 1}}}, client)
        self.assertFalse(response["result"]["isError"])
        client.get.assert_called_once_with("/v1/lists/today?offset=0&limit=1")
        response = things_mcp.dispatch({"jsonrpc": "2.0", "id": 3, "method": "execute"}, client)
        self.assertEqual(response["error"]["code"], -32601)

    def test_real_stdio_discovery(self):
        result = subprocess.run([sys.executable, "-B", "things_mcp.py"],
            input=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n", text=True, capture_output=True, timeout=3)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["result"]["tools"][0]["name"], "read_things_page")
        self.assertEqual(result.stderr, "")


class ReviewTests(unittest.TestCase):
    def task(self, **kwargs):
        return {"source": "things", "id": "a", "title": "Synthetic", "status": "open", "memberships": [],
                "modified_at": "2026-10-01T00:00:00Z", "deadline": None, "scheduled_date": None, **kwargs}

    def review(self, rows, aliases=None):
        return dashboard.daily_review(dashboard.combine_tasks(rows, aliases), today=date(2026, 10, 23),
                                      now=datetime(2026, 10, 23, tzinfo=timezone.utc))

    def test_id_dedupe_memberships_and_explicit_cross_source_links(self):
        a = self.task(memberships=["today"])
        duplicate = self.task(memberships=["upcoming"])
        b = self.task(source="linear", id="b")
        groups = dashboard.combine_tasks([a, duplicate, b])
        self.assertEqual(len(groups), 2)  # Same title alone never merges sources.
        self.assertEqual(groups[0]["records"][0]["memberships"], ["today", "upcoming"])
        linked = dashboard.combine_tasks([a, duplicate, b], {"linear:b": "things:a"})
        self.assertEqual(len(linked), 1)
        self.assertEqual({row["source"] for row in linked[0]["records"]}, {"things", "linear"})
        self.assertEqual(a["memberships"], ["today"])  # Input never mutated.

    def test_missing_activity_and_creation_age_never_mark_stale(self):
        result = self.review([self.task(modified_at=None, created_at="2000-01-01")])
        self.assertEqual(result["needs_review"], [])
        self.assertEqual(result["activity_unknown"], ["things:a"])
        self.assertIsNone(result["project_progress"])

    def test_real_modified_review_and_scheduled_future_exclusion(self):
        task = self.task(modified_at="2026-01-01T00:00:00Z")
        self.assertEqual(len(self.review([task])["needs_review"]), 1)
        task["scheduled_date"] = "2027-01-01"
        self.assertEqual(self.review([task])["needs_review"], [])

    def test_changing_pages_do_not_create_false_stale_claim(self):
        older = self.task(modified_at="2026-01-01T00:00:00Z")
        newer = self.task(modified_at="2026-10-23T00:00:00Z")
        for rows in ([older, newer], [newer, older]):
            result = self.review(rows)
            self.assertEqual(result["needs_review"], [])
            self.assertEqual(result["activity_unknown"], ["things:a"])

    def test_overdue_explicit_importance_today_and_this_week(self):
        rows = [self.task(id="overdue", deadline="2026-10-22"), self.task(id="today", memberships=["today"]),
                self.task(id="linear", source="linear", priority=1, deadline="2026-10-23"),
                self.task(id="week", deadline="2026-10-25"), self.task(id="ordinary")]
        result = self.review(rows)
        self.assertEqual(result["today"]["top_3_candidates"][0]["key"], "things:overdue")
        self.assertEqual(len(result["today"]["top_3_candidates"]), 3)
        self.assertEqual(len(result["today"]["overdue"]), 1)
        self.assertEqual(len(result["this_week"]), 2)

    def test_project_summaries_not_tasks_and_closed_linear_filtered(self):
        args = adapter.arguments(["--projects", "--metadata", "--limit", "2"])
        self.assertEqual(dashboard.things_tasks([metadata_page(args)]), [])
        issue = {"id": "l", "title": "Synthetic", "state": {"type": "completed"}}
        self.assertEqual(dashboard.combine_tasks([dashboard.linear_task(issue)]), [])

    def test_client_disallows_plain_remote_and_redirect_targets(self):
        for url in ["http://100.64.0.1:8766", "http://user:secret@localhost:8766", "ftp://localhost"]:
            with self.assertRaises(ValueError):
                dashboard.ThingsClient(Path("unused"), url)

    def test_launchagent_aqua_private_logs_and_absolute_arguments(self):
        definition = service_control.agent_definition()
        self.assertEqual(definition["LimitLoadToSessionType"], "Aqua")
        self.assertEqual(definition["StandardOutPath"], "/dev/null")
        self.assertEqual(definition["Umask"], 0o077)
        self.assertEqual(definition["ThrottleInterval"], 30)
        self.assertTrue(Path(definition["ProgramArguments"][0]).is_absolute())

    def test_runtime_copies_only_allowlisted_code_and_no_private_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, destination = Path(tmp) / "source", Path(tmp) / "runtime"
            (source / "src").mkdir(parents=True)
            for name in service_control.RUNTIME_FILES:
                (source / name).write_text("# synthetic source\n")
            (source / "private-export.json").write_text('{"synthetic":"private"}')
            service_control.install_runtime(source, destination)
            actual = {str(path.relative_to(destination)) for path in destination.rglob("*") if path.is_file()}
            self.assertEqual(actual, set(service_control.RUNTIME_FILES))
            self.assertEqual(destination.stat().st_mode & 0o777, 0o700)
            self.assertTrue(all(path.stat().st_mode & 0o777 == 0o600 for path in destination.rglob("*") if path.is_file()))


if __name__ == "__main__":
    unittest.main()
