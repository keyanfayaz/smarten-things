"""Synthetic AppleScript fixtures; no app queries or task-store reads.

The foreign-application NSNull regression was additionally exercised with a
task-free synthetic fixture in the authorized daemon.
"""
import json
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "src/read.applescript"
@unittest.skipUnless(platform.system() == "Darwin", "Requires system AppleScript and Foundation")
class NullDateEncodingTests(unittest.TestCase):
    def run_fixture(self, schedule="missing value", deadline="missing value", modified="missing value", omit_schedule=False):
        source = SCRIPT.read_text()
        start = source.index('        tell application "/Applications/Things3.app"')
        end = source.index('            set totalCount to count of eligible', start)
        # Retain the production loop/serializer with local record properties;
        # never import an app dictionary or send an application Apple Event.
        fixture = f'''        tell me
            set appVersion to "synthetic"
            set containerName to "Synthetic Inbox"
            set fixtureDate to date "Wednesday, October 28, 2026 at 12:00:00 PM"
            set syntheticTask to {{id:"synthetic-task", name:"Synthetic fixture", status:"open", project:missing value, |activation date|:{schedule}, |due date|:{deadline}, |modification date|:{modified}}}
            set eligible to {{syntheticTask}}
'''
        if omit_schedule:
            fixture = fixture.replace(f", |activation date|:{schedule}", "", 1)
        source = source[:start] + fixture + source[end:]
        for field in ["activation date", "due date", "modification date"]:
            source = source.replace(field + " of taskRef", "|" + field + "| of taskRef")
        source = source.replace("is not open", 'is not "open"')
        self.assertNotIn("tell application", source)
        self.assertNotIn("/Applications/Things3.app", source)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "synthetic.applescript"
            path.write_text(source)
            result = subprocess.run(["/usr/bin/osascript", str(path), "list", "TMInboxListSource", "0", "1", "metadata"],
                                    capture_output=True, text=True, timeout=10)
        return result

    def page(self, **dates):
        result = self.run_fixture(**dates)
        self.assertEqual(result.returncode, 0, "Synthetic encoding failed; raw output suppressed")
        page = json.loads(result.stdout)
        self.assertEqual(page["returned_count"], 1)
        self.assertEqual(page["total_count"], 1)
        self.assertFalse(page["has_more"])
        self.assertIsNone(page["next_offset"])
        return page["items"][0]

    def test_missing_dates_are_json_null_without_dropping_item(self):
        row = self.page()
        for key in ["scheduled_date", "deadline", "modified_at"]:
            self.assertIsNone(row[key])
        self.assertEqual(row["id"], "synthetic-task")

    def test_known_dates_keep_calendar_and_timestamp_semantics(self):
        row = self.page(schedule="fixtureDate", deadline="fixtureDate", modified="fixtureDate")
        self.assertEqual(row["scheduled_date"], "2026-10-28")
        self.assertEqual(row["deadline"], "2026-10-28")
        self.assertRegex(row["modified_at"], r"^2026-10-28T\d\d:\d\d:\d\dZ$")

    def test_nullable_dates_do_not_suppress_known_modification(self):
        row = self.page(modified="fixtureDate")
        self.assertIsNone(row["scheduled_date"])
        self.assertIsNone(row["deadline"])
        self.assertIsNotNone(row["modified_at"])

    def test_unresolvable_property_still_fails_instead_of_becoming_empty_page(self):
        result = self.run_fixture(omit_schedule=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
