"""Synthetic change-state and real loopback tests. Never reads/writes Things."""
import copy
from datetime import datetime, timezone
import http.client
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from dashboard_client import ThingsClient
import things_mcp
import things_previews as changes
import things_readonly as adapter
import things_service as service


def item(kind="task", item_id="synthetic-task"):
    return {"schema_version": 2, "source": "things", "kind": kind,
            "app_version": "synthetic", "time_zone": "America/Los_Angeles",
            "item": {"id": item_id, "title": "Synthetic title", "status": "open", "project": None,
                     "scheduled_date": None, "modified_at": "2026-10-29T01:02:03Z", "notes": "", "deadline": None}}


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.current = item()
        self.now = 100.0
        self.lookup = Mock(side_effect=lambda kind, key: copy.deepcopy(self.current))
        self.writer = Mock(return_value={"id": "synthetic-task"})
        self.verifier = Mock(side_effect=lambda key, digest, receipt: receipt == "SYNTHETIC_HUMAN_RECEIPT")
        self.coordinator = changes.PreviewCoordinator(self.lookup, clock=lambda: self.now,
            wall_clock=lambda: datetime(2026, 10, 29, tzinfo=timezone.utc),
            approval_verifier=self.verifier, writer=self.writer)

    def preview(self, **values):
        return self.coordinator.preview({"operation": "update", "task_id": "synthetic-task",
                                         "title": "Changed synthetic title", **values})

    def apply(self, preview, receipt="SYNTHETIC_HUMAN_RECEIPT"):
        return self.coordinator.apply(preview["proposal_id"], preview["payload_digest"], receipt)

    def error(self, reason, function, *args):
        with self.assertRaises(changes.ChangeError) as caught:
            function(*args)
        self.assertEqual(caught.exception.reason, reason)

    def test_exact_id_title_and_local_calendar_date_preview_no_mutation(self):
        preview = self.preview(start_date="2026-11-01")  # DST boundary, no UTC conversion.
        self.assertEqual(preview["before"]["id"], "synthetic-task")
        self.assertEqual(preview["after"]["id"], "synthetic-task")
        self.assertEqual(preview["after"]["start_date"], "2026-11-01")
        self.assertEqual(preview["before"]["scheduled_date"], None)
        self.assertFalse(preview["apply_enabled"])
        self.assertEqual(preview["time_zone"], "America/Los_Angeles")
        self.lookup.assert_called_once_with("task", "synthetic-task")
        self.writer.assert_not_called()
        self.verifier.assert_not_called()

    def test_create_inbox_has_no_preexisting_id_and_does_not_read_tasks(self):
        preview = self.coordinator.preview({"operation": "create", "title": "Synthetic creation"})
        self.assertIsNone(preview["before"])
        self.assertIsNone(preview["after"]["id"])
        self.assertIsNone(preview["after"]["project_id"])
        self.lookup.assert_not_called()
        self.writer.assert_not_called()

    def test_create_in_exact_existing_project(self):
        self.current = item("project", "synthetic-project")
        preview = self.coordinator.preview({"operation": "create", "title": "Synthetic creation",
                                            "project_id": "synthetic-project", "start_date": "2028-02-29"})
        self.assertEqual(preview["after"]["project_id"], "synthetic-project")
        self.lookup.assert_called_once_with("project", "synthetic-project")

    def test_only_scoped_individual_task_changes_allowed(self):
        invalid = [[], {"operation": "delete", "task_id": "x"}, {"operation": "create"},
                   {"operation": "update", "task_id": "x"},
                   {"operation": "create", "title": "x", "completed": True},
                   {"operation": "create", "title": "x", "notes": None},
                   {"operation": "update", "task_id": "x", "title": "x", "project_id": "p"},
                   {"operation": "create", "title": "x", "task_id": "existing"},
                   {"operation": "create", "title": "\n"},
                   {"operation": "create", "title": "x", "start_date": "2026-02-30"},
                   {"operation": "create", "title": "x", "start_date": "tomorrow"},
                   {"operation": "create", "title": "x", "start_date": "2026-10-29T00:00:00Z"},
                   {"operation": "create", "title": "x", "start_date": None},
                   {"operation": "update", "task_id": "x;do shell script", "title": "x"},
                   {"operation": "create", "title": True}]
        for value in invalid:
            with self.subTest(value=value):
                self.error("invalid_change", self.coordinator.preview, value)
        self.writer.assert_not_called()

    def test_closed_retargeted_or_unversioned_item_fails_closed(self):
        for key, value in [("id", "different"), ("status", "completed"), ("modified_at", None)]:
            self.current = item(); self.current["item"][key] = value
            self.error("item_unavailable", self.preview)

    def test_no_production_approval_bridge_model_boolean_or_nonce_cannot_apply(self):
        coordinator = changes.PreviewCoordinator(self.lookup)
        proposal = coordinator.preview({"operation": "create", "title": "Synthetic"})
        for receipt in [True, "approved", proposal["proposal_id"], {"confirmed": True}]:
            self.error("trusted_human_approval_unavailable", coordinator.apply,
                       proposal["proposal_id"], proposal["payload_digest"], receipt)
        self.writer.assert_not_called()

    def test_payload_hash_and_human_receipt_are_independently_required(self):
        preview = self.preview()
        self.error("preview_payload_mismatch", self.coordinator.apply, preview["proposal_id"], "0" * 64, "SYNTHETIC_HUMAN_RECEIPT")
        self.verifier.assert_not_called()
        self.error("human_approval_required", self.apply, preview, True)
        self.error("human_approval_required", self.apply, preview, preview["proposal_id"])
        self.writer.assert_not_called()

    def test_returned_preview_mutation_cannot_rewrite_bound_payload(self):
        preview = self.preview()
        preview["after"]["title"] = "Agent changed returned data"
        self.apply(preview)
        self.assertEqual(self.writer.call_args.args[0]["title"], "Changed synthetic title")

    def test_stale_version_or_content_blocks_apply_and_replay(self):
        preview = self.preview()
        self.current["item"]["title"] = "Concurrent external change"
        self.error("preview_stale", self.apply, preview)
        self.error("preview_already_used", self.apply, preview)
        self.writer.assert_not_called()

    def test_expiry_before_or_during_verification_blocks_apply(self):
        preview = self.preview(); self.now += 301
        self.error("preview_expired_or_unknown", self.apply, preview)
        self.now = 100
        preview = self.preview()
        def slow_approval(*args):
            self.now += 301
            return True
        self.coordinator._approval_verifier = slow_approval
        self.error("preview_expired_or_unknown", self.apply, preview)
        self.writer.assert_not_called()

    def test_success_consumes_once_with_same_update_id(self):
        preview = self.preview()
        self.assertEqual(self.apply(preview), {"id": "synthetic-task"})
        self.error("preview_already_used", self.apply, preview)
        self.assertEqual(self.writer.call_count, 1)

    def test_ambiguous_outcome_never_automatically_retries_or_echoes_error(self):
        preview = self.preview()
        self.writer.side_effect = TimeoutError("SYNTHETIC_PRIVATE_CONTENT")
        self.error("write_outcome_uncertain_no_retry", self.apply, preview)
        self.error("preview_already_used", self.apply, preview)
        self.assertEqual(self.writer.call_count, 1)

    def test_changed_update_identity_is_uncertain_not_success_or_retry(self):
        preview = self.preview(); self.writer.return_value = {"id": "different-task"}
        self.error("write_outcome_uncertain_no_retry", self.apply, preview)
        self.error("preview_already_used", self.apply, preview)

    def test_concurrent_apply_calls_cannot_duplicate_a_write(self):
        preview = self.preview(); results = []
        def run():
            try: results.append(self.apply(preview))
            except changes.ChangeError as error: results.append(error.reason)
        workers = [threading.Thread(target=run) for _ in range(2)]
        for worker in workers: worker.start()
        for worker in workers: worker.join(timeout=2)
        self.assertEqual(self.writer.call_count, 1)
        self.assertIn("preview_already_used", results)

    def test_bounded_memory_and_expired_proposal_eviction(self):
        coordinator = changes.PreviewCoordinator(self.lookup, capacity=1, clock=lambda: self.now)
        coordinator.preview({"operation": "create", "title": "Synthetic"})
        self.error("preview_capacity", coordinator.preview, {"operation": "create", "title": "Synthetic 2"})
        self.now += 301
        coordinator.preview({"operation": "create", "title": "Synthetic 2"})


class ExactReadTests(unittest.TestCase):
    def test_exact_item_validation_rejects_identity_private_fields_and_dates(self):
        valid = item()
        self.assertEqual(adapter.validate_item(valid, "task", "synthetic-task"), valid)
        for key, value in [("id", "different"), ("status", "completed"), ("scheduled_date", "2026-02-30"),
                           ("notes", None), ("deadline", "2026-02-30"), ("checklist", "not in scope")]:
            invalid = item(); invalid["item"][key] = value
            with self.subTest(key=key), self.assertRaises(adapter.AdapterError):
                adapter.validate_item(invalid, "task", "synthetic-task")

    def test_exact_reader_uses_fixed_argv_dictionary_and_bounded_runner(self):
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(item()), ""))
        with patch.object(adapter, "capabilities") as capabilities:
            adapter.read_item("task", "synthetic-task", runner=runner)
        capabilities.assert_called_once()
        argv = runner.call_args.args[0]
        self.assertEqual(argv, ["/usr/bin/osascript", str(adapter.ITEM_SCRIPT), "task", "synthetic-task"])
        self.assertEqual(runner.call_args.kwargs["timeout"], 65)
        for key in ["x?secret=yes", "x/../y", "x\"", "x;set status"]:
            with self.assertRaises(adapter.AdapterError): adapter.read_item("task", key, runner=runner)
        self.assertEqual(runner.call_count, 1)

    def test_exact_lookup_shares_nonprompting_permission_gate(self):
        reader = service.LiveReader()
        with patch.object(reader, "_preflight", return_value={"reason": "automation_denied", "automation": "denied"}), patch.object(adapter, "read_item") as lookup:
            with self.assertRaises(service.ReadUnavailable): reader.lookup("task", "synthetic-task")
            lookup.assert_not_called()
        self.assertTrue(reader.denied)


class TransportTests(unittest.TestCase):
    def test_default_discovery_unchanged_preview_opt_in_and_no_apply_tool(self):
        client = Mock(); coordinator = changes.PreviewCoordinator(client.lookup)
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        self.assertEqual([t["name"] for t in things_mcp.dispatch(request, client)["result"]["tools"]], ["read_things_page"])
        tools = things_mcp.dispatch(request, client, coordinator)["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], ["read_things_page", "preview_things_task_change"])
        self.assertTrue(all(t["annotations"]["readOnlyHint"] for t in tools))
        for name in ["apply_things_change", "delete_task", "complete_task"]:
            response = things_mcp.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name, "arguments": {"confirmed": True}}}, client, coordinator)
            self.assertTrue(response["result"]["isError"])
        client.get.assert_not_called()
        client.lookup.assert_not_called()

    def test_preview_tool_rejects_model_approval_fields_and_reports_blocked(self):
        client = Mock(); coordinator = changes.PreviewCoordinator(client.lookup)
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "preview_things_task_change", "arguments": {"operation": "create", "title": "Synthetic", "approved": True}}}
        self.assertTrue(things_mcp.dispatch(request, client, coordinator)["result"]["isError"])
        del request["params"]["arguments"]["approved"]
        result = things_mcp.dispatch(request, client, coordinator)["result"]
        self.assertFalse(result["isError"])
        self.assertFalse(json.loads(result["content"][0]["text"])["apply_enabled"])

    def test_real_http_exact_lookup_auth_and_all_mutation_methods_stay_rejected(self):
        reader = Mock(); reader.lookup.side_effect = lambda kind, key: item(kind, key)
        token = "SYNTHETIC_READ_TOKEN_" + "x" * 32
        server = service.Server(("127.0.0.1", 0), token, reader)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True); thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory)/"token"; path.write_text(token); path.chmod(0o600)
                client = ThingsClient(path, f"http://127.0.0.1:{server.server_port}")
                self.assertEqual(client.lookup("task", "synthetic-task"), item())
            reader.lookup.assert_called_once_with("task", "synthetic-task")
            for method, auth in [("GET", False), ("POST", True), ("PUT", True), ("PATCH", True), ("DELETE", True)]:
                connection = http.client.HTTPConnection(*server.server_address, timeout=2)
                headers = {"Authorization": "Bearer " + token} if auth else {}
                connection.request(method, "/v1/items/task/synthetic-task", headers=headers)
                response = connection.getresponse(); response.read(); connection.close()
                self.assertEqual(response.status, 405 if auth else 401)
            self.assertEqual(reader.lookup.call_count, 1)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
