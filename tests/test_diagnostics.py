"""Synthetic diagnostics tests; never sends an Apple Event or reads real tasks."""
from contextlib import redirect_stderr, redirect_stdout
import copy
import http.client
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

import dashboard_client
import things_readonly as adapter
import things_service as service
from test_service import SYNTHETIC_TOKEN, metadata_page


def ready_report():
    checks = {name: {"ok": True} for name in service.CHECK_NAMES}
    checks["app_discovery"]["visible_running_instances"] = 1
    return {"checks": checks, "prerequisites_met": True}


class DiagnosticGateTests(unittest.TestCase):
    def test_permission_states_are_service_context_task_free_and_never_prompt(self):
        for status, reason in service.PERMISSION_REASONS.items():
            reader = service.LiveReader()
            with self.subTest(status=status), patch.object(service, "permission_status", return_value=status) as permission, \
                    patch.object(adapter, "diagnose", return_value=ready_report()), patch.object(adapter, "read_page") as read:
                result = reader.diagnostics()
                self.assertEqual(result["probe_context"], "service_process")
                self.assertEqual(result["service_pid"], os.getpid())
                self.assertFalse(result["task_read_performed"])
                self.assertIsNone(result["last_read"])
                self.assertEqual(result["preflight"]["automation"], status)
                self.assertEqual(result["preflight"]["reason"], reason)
                permission.assert_called_once_with()  # request=True is never supplied.
                read.assert_not_called()

    def test_unknown_status_and_exceptions_do_not_leak(self):
        for error in [None, RuntimeError("SYNTHETIC_PRIVATE /private/unrelated/path")]:
            with self.subTest(error=bool(error)), patch.object(service, "permission_status",
                    return_value="SYNTHETIC_PRIVATE", side_effect=error), patch.object(adapter, "read_page") as read:
                result = service.LiveReader().diagnostics()
                self.assertEqual(result["preflight"]["reason"], "automation_unavailable")
                self.assertNotIn("SYNTHETIC_PRIVATE", json.dumps(result))
                read.assert_not_called()

    def test_prerequisite_names_allowlisted_and_details_excluded(self):
        report = ready_report()
        report["checks"]["app_discovery"] = {"ok": False, "detail": "SYNTHETIC_PRIVATE /private/unrelated/path"}
        report["checks"]["SYNTHETIC_PRIVATE"] = {"ok": False}
        report["prerequisites_met"] = False
        with patch.object(service, "permission_status", return_value="granted"), \
                patch.object(adapter, "diagnose", return_value=report), patch.object(adapter, "read_page") as read:
            result = service.LiveReader().diagnostics()
        self.assertEqual(result["preflight"]["reason"], "prerequisites_unavailable")
        self.assertEqual(result["preflight"]["failed_checks"], ["app_discovery"])
        self.assertNotIn("SYNTHETIC_PRIVATE", json.dumps(result))
        self.assertNotIn("/private/", json.dumps(result))
        read.assert_not_called()

    def test_missing_app_and_broken_preflight_are_distinct(self):
        report = ready_report()
        report["checks"]["app_discovery"]["visible_running_instances"] = 0
        with patch.object(service, "permission_status", return_value="granted"), \
                patch.object(adapter, "diagnose", return_value=report):
            self.assertEqual(service.LiveReader().diagnostics()["preflight"]["reason"], "app_not_running")
        with patch.object(service, "permission_status", return_value="granted"), \
                patch.object(adapter, "diagnose", side_effect=RuntimeError("SYNTHETIC_PRIVATE")):
            result = service.LiveReader().diagnostics()
            self.assertEqual(result["preflight"]["reason"], "preflight_unavailable")
            self.assertNotIn("SYNTHETIC_PRIVATE", json.dumps(result))

    def test_denial_latches_and_diagnostics_cannot_retry_or_clear_it(self):
        reader = service.LiveReader()
        with patch.object(service, "permission_status", return_value="denied") as permission, \
                patch.object(adapter, "read_page") as read:
            self.assertTrue(reader.diagnostics()["denial_latched"])
            self.assertEqual(reader.diagnostics()["preflight"]["reason"], "automation_denied_latched")
            with self.assertRaises(service.ReadUnavailable):
                reader(None)
            permission.assert_called_once_with()
            read.assert_not_called()

    def test_reader_failures_are_classified_without_retaining_raw_context(self):
        cases = [("SYNTHETIC_PRIVATE /private/path (-1743)", "automation_denied"),
                 ("SYNTHETIC_PRIVATE (-10827)", "app_discovery_failed"),
                 ("SYNTHETIC_PRIVATE (-1728)", "object_resolution_failed"),
                 ("SYNTHETIC_PRIVATE (-1712)", "apple_event_timeout"),
                 ("SYNTHETIC_PRIVATE denied /private/path", "reader_failed")]
        args = adapter.arguments(["--list", "inbox", "--metadata", "--limit", "1"])
        for raw_error, reason in cases:
            reader = service.LiveReader()
            with self.subTest(reason=reason), patch.object(service, "permission_status", return_value="granted"), \
                    patch.object(adapter, "diagnose", return_value=ready_report()), \
                    patch.object(adapter, "capabilities"), \
                    patch.object(adapter, "bounded_run", return_value=subprocess.CompletedProcess([], 1, "", raw_error)):
                with self.assertRaises(service.ReadUnavailable) as raised:
                    reader(args)
                self.assertEqual(raised.exception.reason, reason)
                self.assertEqual(reader.last_read, {"stage": "reader", "reason": reason})
                self.assertEqual(reader.denied, reason == "automation_denied")
                result = reader.diagnostics()
                self.assertNotIn("SYNTHETIC_PRIVATE", json.dumps(result))
                self.assertNotIn("/private/", json.dumps(result))
        self.assertEqual(service.ReadUnavailable("SYNTHETIC_PRIVATE").reason, "reader_failed")

    def test_success_retains_only_status_and_probe_does_not_read_again(self):
        reader = service.LiveReader()
        page = metadata_page()
        expected = copy.deepcopy(page)
        with patch.object(service, "permission_status", return_value="granted"), \
                patch.object(adapter, "diagnose", return_value=ready_report()), \
                patch.object(adapter, "read_page", return_value=page) as read:
            self.assertEqual(reader(None), expected)
            result = reader.diagnostics()
            read.assert_called_once_with(None)
        self.assertEqual(result["last_read"], {"stage": "reader", "reason": "succeeded"})
        self.assertEqual(page, expected)
        self.assertNotIn("items", json.dumps(result))
        self.assertNotIn("count", json.dumps(result))


class DiagnosticHTTPTests(unittest.TestCase):
    def setUp(self):
        self.reader = service.LiveReader()
        self.server = service.Server(("127.0.0.1", 0), SYNTHETIC_TOKEN, self.reader)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path="/v1/diagnostics", method="GET", headers=None):
        headers = headers if headers is not None else {"Authorization": "Bearer " + SYNTHETIC_TOKEN}
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=2)
        try:
            connection.request(method, path, headers=headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), json.loads(response.read())
        finally:
            connection.close()

    def test_diagnostics_auth_host_origin_and_mutations_keep_existing_boundary(self):
        with patch.object(service, "permission_status") as permission, patch.object(adapter, "read_page") as read:
            self.assertEqual(self.request(headers={})[0], 401)
            self.assertEqual(self.request(headers={"Authorization": "Bearer wrong"})[0], 401)
            for key, value in [("Host", "untrusted.invalid"), ("Origin", "http://127.0.0.1")]:
                self.assertEqual(self.request(headers={"Authorization": "Bearer " + SYNTHETIC_TOKEN, key: value})[0], 403)
            self.assertEqual(self.request(method="POST")[0], 405)
            self.assertEqual(self.request(path="/v1/diagnostics?request=true")[0], 400)
            permission.assert_not_called()
            read.assert_not_called()

    def test_real_http_client_observes_daemon_preflight_without_task_read_or_logs(self):
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, redirect_stderr(output), redirect_stdout(output), \
                patch.object(service, "permission_status", return_value="consent_required"), \
                patch.object(adapter, "read_page") as read:
            token = Path(tmp) / "token"
            token.write_text(SYNTHETIC_TOKEN)
            token.chmod(0o600)
            client = dashboard_client.ThingsClient(token, "http://127.0.0.1:" + str(self.server.server_port))
            result = client.get("/v1/diagnostics")
            status, headers, direct = self.request()
            self.assertEqual(status, 200)
            self.assertEqual(result, direct)
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertEqual(result["preflight"]["reason"], "automation_consent_required")
            self.assertEqual(result["service_pid"], os.getpid())
            read.assert_not_called()
        self.assertEqual(output.getvalue(), "")
        self.assertNotIn(SYNTHETIC_TOKEN, json.dumps(result))

    def test_read_error_and_diagnostics_expose_same_fixed_cause(self):
        with patch.object(service, "permission_status", return_value="consent_required"), \
                patch.object(adapter, "read_page") as read:
            status, _, body = self.request(path="/v1/lists/inbox?limit=1")
            self.assertEqual(status, 503)
            self.assertEqual(body["reason"], "automation_consent_required")
            result = self.request()[2]
            self.assertEqual(result["last_read"], {"stage": "preflight", "reason": body["reason"]})
            read.assert_not_called()

    def test_busy_diagnostics_do_not_probe_or_read(self):
        self.reader.lock.acquire()
        try:
            with patch.object(service, "permission_status") as permission, patch.object(adapter, "read_page") as read:
                self.assertEqual(self.request()[0], 429)
                permission.assert_not_called()
                read.assert_not_called()
        finally:
            self.reader.lock.release()


if __name__ == "__main__":
    unittest.main()
