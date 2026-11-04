"""Synthetic only: no osascript, Things app, task store, or network access."""

from contextlib import redirect_stderr
import copy
import io
import json
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import things_readonly as adapter


def synthetic_page(offset=0, limit=2, total=3):
    # Deliberately blank and identical titles are valid; IDs remain distinct.
    count = min(limit, max(0, total - offset))
    more = offset + count < total
    return {
        "schema_version": 1, "kind": "list", "container_id": "TMInboxListSource",
        "container_name": "Synthetic Inbox", "app_version": "synthetic",
        "offset": offset, "limit": limit, "total_count": total,
        "returned_count": count, "next_offset": offset + count if more else None,
        "has_more": more,
        "items": [{"id": f"synthetic-{i}", "title": "" if i == 0 else 'Example ☃ "quoted"\nline',
                   "status": "open"} for i in range(offset, offset + count)],
    }


class PageContractTests(unittest.TestCase):
    def validate(self, page, offset=0, limit=2):
        return adapter.validate_page(page, "list", "TMInboxListSource", offset, limit)

    def test_unicode_blanks_and_duplicate_titles_preserved_in_order(self):
        page = synthetic_page(limit=3)
        original = copy.deepcopy(page)
        self.assertEqual(self.validate(page, limit=3), original)
        self.assertEqual(page["items"][0]["title"], "")
        self.assertEqual(page["items"][1]["title"], page["items"][2]["title"])

    def test_first_last_empty_and_past_end_pages(self):
        for offset, total in ((0, 3), (2, 3), (0, 0), (5, 3)):
            with self.subTest(offset=offset, total=total):
                self.validate(synthetic_page(offset=offset, total=total), offset=offset)

    def test_closed_world_fields_and_open_status(self):
        variants = []
        for field in ("notes", "credentials", "unknown"):
            page = synthetic_page()
            page[field] = "synthetic forbidden value"
            variants.append(page)
            page = synthetic_page()
            page["items"][0][field] = "synthetic forbidden value"
            variants.append(page)
        for status in ("completed", "canceled", None):
            page = synthetic_page()
            page["items"][0]["status"] = status
            variants.append(page)
        for page in variants:
            with self.assertRaises(adapter.AdapterError):
                self.validate(page)

    def test_inconsistent_pages_are_discarded(self):
        mutations = {
            "schema_version": True, "offset": True, "limit": 51, "total_count": -1,
            "returned_count": 1, "has_more": 1, "next_offset": False,
            "container_id": "TMLogbookListSource", "kind": "unexpected", "items": {},
        }
        for key, value in mutations.items():
            page = synthetic_page()
            page[key] = value
            with self.subTest(key=key), self.assertRaises(adapter.AdapterError):
                self.validate(page)
        page = synthetic_page()
        page["items"][1]["id"] = page["items"][0]["id"]
        with self.assertRaises(adapter.AdapterError):
            self.validate(page)
        page = synthetic_page()
        del page["app_version"]
        with self.assertRaises(adapter.AdapterError):
            self.validate(page)


class InvocationTests(unittest.TestCase):
    def test_scope_and_bounds_rejected_before_running(self):
        invalid = [[], ["--list", "archive"], ["--list", "inbox", "--limit", "0"],
                   ["--list", "today", "--limit", "51"], ["--projects", "--offset", "-1"],
                   ["--projects", "--offset", "1000001"], ["--project", 'id";do shell script'],
                   ["--project", ""], ["--projects", "--list", "today"]]
        for argv in invalid:
            with self.subTest(argv=argv), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                adapter.arguments(argv)

    def test_exact_argv_and_deadline_without_shell_or_historical_script(self):
        args = adapter.arguments(["--list", "inbox", "--limit", "2"])
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return SimpleNamespace(returncode=0, stdout=json.dumps(synthetic_page()), stderr="")

        result = adapter.read_page(args, runner=fake_run)
        self.assertEqual(result, synthetic_page())
        self.assertEqual(len(calls), 1)
        cmd, kwargs = calls[0]
        self.assertEqual(cmd, ["/usr/bin/osascript", str(adapter.SCRIPT), "list", "TMInboxListSource", "0", "2"])
        self.assertEqual(kwargs["timeout"], 65)
        self.assertNotIn("shell", kwargs)
        self.assertEqual(adapter.SCRIPT.name, "read.applescript")

    def test_project_and_catalog_scope(self):
        for argv, expected in ((["--project", "synthetic-project"], ("project", "synthetic-project")),
                               (["--projects"], ("projects", "open-projects")),
                               (["--list", "today"], ("list", "TMTodayListSource"))):
            self.assertEqual(adapter.target_for(adapter.arguments(argv)), expected)

    def test_no_retry_and_raw_error_content_suppressed(self):
        for code in ("-1743", "-1742", "-10827", "-1728", "-1712", "-2700"):
            calls = []

            def fake_run(*args, **kwargs):
                calls.append(args)
                return SimpleNamespace(returncode=1, stdout="", stderr=f"SYNTHETIC_PRIVATE_TITLE ({code})")

            with self.subTest(code=code), self.assertRaises(adapter.AdapterError) as caught:
                adapter.read_page(adapter.arguments(["--projects"]), runner=fake_run)
            self.assertEqual(len(calls), 1)
            self.assertNotIn("SYNTHETIC_PRIVATE_TITLE", str(caught.exception))
            if code in ("-1743", "-1742"):
                self.assertIn("denied", str(caught.exception))
            elif code in ("-10827", "-1728"):
                self.assertNotIn("access denied", str(caught.exception))

    def test_timeout_and_invalid_json_do_not_leak_output(self):
        args = adapter.arguments(["--projects"])
        with patch.object(adapter, "bounded_run", side_effect=subprocess.TimeoutExpired("synthetic", 65)) as runner:
            with self.assertRaisesRegex(adapter.AdapterError, "65-second"):
                adapter.read_page(args)
            self.assertEqual(runner.call_count, 1)
        with patch.object(adapter, "bounded_run", return_value=SimpleNamespace(returncode=0, stdout="SYNTHETIC_PRIVATE_TITLE", stderr="")):
            with self.assertRaises(adapter.AdapterError) as caught:
                adapter.read_page(args)
            self.assertNotIn("SYNTHETIC_PRIVATE_TITLE", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
