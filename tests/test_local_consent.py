"""Synthetic owner-consent tests. No real Automation API is invoked."""
from contextlib import redirect_stderr, redirect_stdout
import http.client
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, call, patch

import service_control
import things_readonly as adapter
import things_service as service
from test_service import SYNTHETIC_TOKEN


def consent_report():
    return {"probe_context": "service_process", "service_pid": 12345, "denial_latched": False,
            "preflight": {"automation": "consent_required"},
            "local_consent": {"supported": True, "attempted": False}}


class LocalConsentTests(unittest.TestCase):
    def finish_worker(self, reader):
        self.assertTrue(reader.lock.acquire(timeout=2), "Synthetic consent worker did not finish")
        reader.lock.release()

    def test_explicit_local_request_prompts_once_without_reading_or_logging(self):
        reader = service.LiveReader()
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output), \
                patch.object(service, "permission_status", side_effect=["consent_required", "granted"]) as permission, \
                patch.object(adapter, "read_page") as read:
            self.assertTrue(reader.begin_local_consent())
            self.finish_worker(reader)
            self.assertFalse(reader.begin_local_consent())
            self.assertEqual(permission.call_args_list, [call(), call(request=True)])
            read.assert_not_called()
        self.assertEqual(reader.consent_state, "granted")
        self.assertEqual(output.getvalue(), "")

    def test_only_initial_consent_required_state_can_prompt(self):
        for status in ["granted", "denied", "app_not_running", "unsupported", "unavailable"]:
            reader = service.LiveReader()
            with self.subTest(status=status), patch.object(service, "permission_status", return_value=status) as permission, \
                    patch.object(adapter, "read_page") as read:
                reader.begin_local_consent()
                self.finish_worker(reader)
                permission.assert_called_once_with()
                self.assertEqual(reader.consent_state, status)
                self.assertEqual(reader.denied, status == "denied")
                read.assert_not_called()

    def test_denial_stops_without_retry_and_never_clears_latch(self):
        reader = service.LiveReader()
        with patch.object(service, "permission_status", side_effect=["consent_required", "denied"]) as permission:
            reader.begin_local_consent()
            self.finish_worker(reader)
            self.assertTrue(reader.denied)
            self.assertFalse(reader.begin_local_consent())
            self.assertTrue(reader.diagnostics()["denial_latched"])
            self.assertEqual(permission.call_count, 2)
        with patch.object(service, "permission_status") as permission:
            already_denied = service.LiveReader()
            already_denied.denied = True
            self.assertFalse(already_denied.begin_local_consent())
            permission.assert_not_called()

    def test_pending_consent_is_task_free_and_serializes_task_reads(self):
        entered, release = threading.Event(), threading.Event()
        def permission(request=False):
            if not request:
                return "consent_required"
            entered.set()
            release.wait(timeout=2)
            return "granted"
        reader = service.LiveReader()
        with patch.object(service, "permission_status", side_effect=permission), patch.object(adapter, "read_page") as read:
            try:
                reader.begin_local_consent()
                self.assertTrue(entered.wait(timeout=1))
                diagnostic = reader.diagnostics()
                self.assertEqual(diagnostic["local_consent"]["state"], "awaiting_user")
                self.assertEqual(diagnostic["preflight"]["reason"], "local_consent_pending")
                self.assertFalse(diagnostic["task_read_performed"])
                with self.assertRaises(service.BusyError):
                    reader(None)
                read.assert_not_called()
            finally:
                release.set()
                self.finish_worker(reader)

    def test_busy_reader_does_not_queue_a_future_prompt(self):
        reader = service.LiveReader()
        reader.lock.acquire()
        try:
            with patch.object(service, "permission_status") as permission:
                self.assertFalse(reader.begin_local_consent())
                self.assertFalse(reader.consent_attempted)
                self.assertEqual(reader.consent_state, "reader_busy")
                permission.assert_not_called()
        finally:
            reader.lock.release()

    def test_consent_failure_is_redacted_and_lock_is_released(self):
        reader = service.LiveReader()
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output), patch.object(service, "permission_status",
                side_effect=["consent_required", RuntimeError("SYNTHETIC_PRIVATE /private/path")]):
            reader.begin_local_consent()
            self.finish_worker(reader)
        self.assertEqual(reader.consent_state, "unavailable")
        self.assertNotIn("SYNTHETIC_PRIVATE", output.getvalue())
        self.assertFalse(reader.begin_local_consent())


class OwnerCommandTests(unittest.TestCase):
    def test_owner_command_matches_daemon_pid_then_signals_only_fixed_launchagent(self):
        with patch.object(service_control, "ThingsClient") as client, \
                patch.object(service_control.subprocess, "run", side_effect=[
                    subprocess.CompletedProcess([], 0, "\tpid = 12345\n", ""),
                    subprocess.CompletedProcess([], 0, "", "")]) as run, \
                patch.object(service_control, "init_token") as init, patch.object(service_control, "install_runtime") as install:
            client.return_value.get.return_value = consent_report()
            service_control.control("request-access")
            client.return_value.get.assert_called_once_with('/v1/diagnostics')
            target = f"gui/{os.getuid()}/dev.keyanfayaz.things-readonly"
            self.assertEqual(run.call_args_list[0].args[0], ["/bin/launchctl", "print", target])
            self.assertEqual(run.call_args_list[1].args[0], ["/bin/launchctl", "kill", "SIGUSR1", target])
            init.assert_not_called()
            install.assert_not_called()

    def test_old_unready_or_malformed_diagnostic_cannot_signal_daemon(self):
        reports = [None, {}, {**consent_report(), "local_consent": None},
                   {**consent_report(), "denial_latched": True},
                   {**consent_report(), "local_consent": {"supported": True, "attempted": True}},
                   {**consent_report(), "preflight": {"automation": "granted"}}]
        for report in reports:
            with self.subTest(report=report), patch.object(service_control, "ThingsClient") as client, \
                    patch.object(service_control.subprocess, "run") as run:
                client.return_value.get.return_value = report
                with self.assertRaises(adapter.AdapterError):
                    service_control.control("request-access")
                run.assert_not_called()

    def test_pid_mismatch_prevents_signal(self):
        with patch.object(service_control, "ThingsClient") as client, patch.object(service_control.subprocess, "run",
                return_value=subprocess.CompletedProcess([], 0, "pid = 99999\n", "")) as run:
            client.return_value.get.return_value = consent_report()
            with self.assertRaisesRegex(adapter.AdapterError, "identity"):
                service_control.control("request-access")
            self.assertEqual(run.call_count, 1)


class SignalIntegrationTests(unittest.TestCase):
    def request(self, port, path, method="GET"):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        try:
            connection.request(method, path, headers={"Authorization": "Bearer " + SYNTHETIC_TOKEN})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_real_owner_signal_executes_in_same_process_and_http_cannot_trigger_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = Path(tmp) / "synthetic-request-pid.json"
            script = f'''
import json,os
from pathlib import Path
import things_service as service
record=Path({str(record)!r})
def permission(request=False):
    if request:
        record.write_text(json.dumps({{"pid":os.getpid()}}))
        return "granted"
    return "granted" if record.exists() else "consent_required"
service.permission_status=permission
server=service.Server(("127.0.0.1",0),{SYNTHETIC_TOKEN!r})
service.install_local_consent_signal(server)
print(json.dumps({{"pid":os.getpid(),"port":server.server_port}}),flush=True)
server.serve_forever(poll_interval=0.01)
'''
            proc = subprocess.Popen([sys.executable, "-B", "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                state = json.loads(proc.stdout.readline())
                port = state["port"]
                self.assertFalse(record.exists())  # Startup never requests consent.
                for path, method, expected in [("/v1/health", "GET", 200), ("/v1/diagnostics", "GET", 200),
                                               ("/v1/lists/inbox?limit=1", "GET", 503),
                                               ("/v1/request-consent", "GET", 404),
                                               ("/v1/request-consent", "POST", 405)]:
                    self.assertEqual(self.request(port, path, method)[0], expected)
                self.assertFalse(record.exists())
                proc.send_signal(signal.SIGUSR1)
                deadline = time.monotonic() + 2
                while not record.exists() and time.monotonic() < deadline:
                    threading.Event().wait(0.01)
                self.assertTrue(record.exists())
                self.assertEqual(json.loads(record.read_text())["pid"], state["pid"])
                self.assertEqual(state["pid"], proc.pid)
            finally:
                proc.terminate()
                _, stderr = proc.communicate(timeout=2)
                self.assertEqual(stderr, "")


if __name__ == "__main__":
    unittest.main()
