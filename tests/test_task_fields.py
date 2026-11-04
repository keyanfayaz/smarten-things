"""Notes/deadline contracts and native scripts exercised with synthetic records."""
import copy
import json
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest

from things_previews import ChangeError, normalize_change
import things_readonly as adapter
from test_previews import item


class TaskFieldValidationTests(unittest.TestCase):
    def test_multiline_unicode_notes_preserve_exact_text_and_empty_clears(self):
        for notes in ('', 'Description\nSecond line\r\n\tUnicode: café 🗓', 'x'*8192):
            value = {'operation': 'update', 'task_id': 'synthetic-task', 'notes': notes}
            self.assertEqual(normalize_change(value), value)

    def test_invalid_notes_and_dates_are_rejected_without_echoing_content(self):
        invalid = [{'notes': value} for value in (None, True, [], 'x\x00y', 'x\x01y', '\x7f', '\ud800', 'x'*8193, '🗓'*4097)]
        invalid += [{'deadline': value} for value in (True, 12, 'tomorrow', '', '2026-02-30', '2026-11-04T00:00:00Z')]
        for extra in invalid:
            with self.subTest(field=next(iter(extra))), self.assertRaises(ChangeError) as caught:
                normalize_change({'operation': 'update', 'task_id': 'synthetic-task', **extra})
            self.assertEqual(str(caught.exception), 'invalid_change')

    def test_deadline_can_be_set_or_cleared_without_changing_start_day(self):
        for deadline in (None, '2028-02-29', '2026-11-01'):
            change = {'operation': 'update', 'task_id': 'synthetic-task', 'deadline': deadline}
            self.assertEqual(normalize_change(change), change)
            self.assertNotIn('start_date', normalize_change(change))

    def test_exact_schema_requires_complete_notes_and_deadline(self):
        valid = item()
        valid['item'].update(notes='Exact description\n🗓', deadline='2028-02-29')
        self.assertEqual(adapter.validate_item(valid, 'task', 'synthetic-task'), valid)
        for field in ('notes', 'deadline'):
            missing = copy.deepcopy(valid); del missing['item'][field]
            with self.assertRaises(adapter.AdapterError):
                adapter.validate_item(missing, 'task', 'synthetic-task')
        old = copy.deepcopy(valid); old['schema_version'] = 1
        with self.assertRaises(adapter.AdapterError):
            adapter.validate_item(old, 'task', 'synthetic-task')


@unittest.skipUnless(platform.system() == 'Darwin', 'Requires system AppleScript and Foundation')
class NativeTaskFieldTests(unittest.TestCase):
    """Replace only the application boundary; execute the production field logic.

    No Things dictionary, application Apple Event, task database or credential
    is used. Native application behavior remains a separate live acceptance test.
    """
    def run_native(self, change, *, baseline_notes='Original description', initial_deadline=None, initial_start=None):
        script = Path(__file__).resolve().parents[1]/'src/write_task.applescript'
        source = script.read_text()
        source = source.replace('    set change to request\'s objectForKey:"change"', '''    set fixtureFormatter to current application's NSISO8601DateFormatter's alloc()'s init()
    set fixtureDate to fixtureFormatter's dateFromString:"2026-10-29T01:02:03Z"
    set syntheticTask to {id:"synthetic-task", name:"Synthetic title", |notes|:"Original description", |status|:"open", |project|:missing value, |activation date|:missing value, |due date|:missing value, |modification date|:fixtureDate}
    set change to request's objectForKey:"change"''')
        source = source.replace('tell application "/Applications/Things3.app"', 'tell me')
        if initial_deadline:
            source = source.replace('|due date|:missing value', '|due date|:(my calendarDay("'+initial_deadline+'"))')
        if initial_start:
            source = source.replace('|activation date|:missing value', '|activation date|:(my calendarDay("'+initial_start+'"))')
        source = source.replace('set currentZone to (current application\'s NSTimeZone\'s localTimeZone()\'s |name|()) as text', 'set currentZone to "Synthetic/Zone"')
        source = source.replace('if version is not', 'if "synthetic" is not')
        source = source.replace('set taskRef to to do id requestedID', 'set taskRef to syntheticTask')
        source = source.replace('if class of taskRef is project then error "Project outside task scope"', '')
        source = source.replace('set taskRef to make new to do with properties {name:(newTitle as text)} at beginning of list id "TMInboxListSource"', 'set taskRef to syntheticTask\n                        set name of taskRef to (newTitle as text)')
        source = source.replace('set destinationRef to project id (destinationID as text)', 'error "Project fixture not supported"')
        source = source.replace('set taskRef to make new to do with properties {name:(newTitle as text)} at beginning of destinationRef', 'error "Project fixture not supported"')
        source = source.replace('schedule taskRef for startDay', 'set |activation date| of taskRef to startDay')
        for field in ('status', 'notes', 'project', 'activation date', 'due date', 'modification date'):
            for reference in ('taskRef', 'itemRef'):
                source = source.replace(field+' of '+reference, '|'+field+'| of '+reference)
        source = source.replace('is not open then', 'is not "open" then')
        source = source.replace('    set jsonData to current application\'s NSJSONSerialization', '''    if outcomeName is "applied" then
        set observed to current application's NSMutableDictionary's alloc()'s init()
        observed's setObject:(name of taskRef) forKey:"title"
        observed's setObject:(|notes| of taskRef) forKey:"notes"
        observed's setObject:(my dateValue(|due date| of taskRef, true)) forKey:"deadline"
        observed's setObject:(my dateValue(|activation date| of taskRef, true)) forKey:"start_date"
        output's setObject:observed forKey:"fixture_observed"
    end if
    set jsonData to current application's NSJSONSerialization''')
        self.assertNotIn('tell application', source)
        self.assertNotIn('/Applications/Things3.app', source)
        baseline = item()
        baseline['time_zone'] = 'Synthetic/Zone'
        baseline['item']['notes'] = baseline_notes
        baseline['item']['deadline'] = initial_deadline
        baseline['item']['scheduled_date'] = initial_start
        bound = {'change': change, 'context': {'app_version': 'synthetic', 'time_zone': 'Synthetic/Zone'},
                 'baseline': baseline if change['operation'] == 'update' else None}
        with tempfile.TemporaryDirectory() as directory:
            script_path = Path(directory)/'synthetic.applescript'
            request_path = Path(directory)/'request.json'
            script_path.write_text(source)
            request_path.write_text(json.dumps(bound))
            result = subprocess.run(['/usr/bin/osascript', str(script_path), str(request_path)],
                                    capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, 'Synthetic native execution failed: '+result.stderr)
        return json.loads(result.stdout)

    def test_native_update_notes_start_day_and_deadline(self):
        change = {'operation': 'update', 'task_id': 'synthetic-task', 'notes': 'First\nSecond — 🗓',
                  'start_date': '2028-02-29', 'deadline': '2028-03-05'}
        result = self.run_native(change)
        self.assertEqual(result['outcome'], 'applied')
        observed = result['fixture_observed']
        self.assertEqual(observed, {'title': 'Synthetic title', 'notes': change['notes'],
                                   'start_date': change['start_date'], 'deadline': change['deadline']})

    def test_native_empty_notes_and_null_deadline(self):
        result = self.run_native({'operation': 'update', 'task_id': 'synthetic-task', 'notes': '', 'deadline': None},
                                 initial_deadline='2028-03-05', initial_start='2028-02-29')
        self.assertEqual(result['outcome'], 'applied')
        self.assertEqual(result['fixture_observed']['notes'], '')
        self.assertIsNone(result['fixture_observed']['deadline'])
        self.assertEqual(result['fixture_observed']['start_date'], '2028-02-29')

    def test_native_notes_only_preserves_both_dates(self):
        result = self.run_native({'operation': 'update', 'task_id': 'synthetic-task', 'notes': 'New notes'},
                                 initial_deadline='2028-03-05', initial_start='2028-02-29')
        self.assertEqual(result['outcome'], 'applied')
        self.assertEqual(result['fixture_observed']['deadline'], '2028-03-05')
        self.assertEqual(result['fixture_observed']['start_date'], '2028-02-29')

    def test_native_create_with_description_and_deadline(self):
        change = {'operation': 'create', 'title': 'Synthetic created task', 'notes': 'Creation notes', 'deadline': '2028-03-05'}
        result = self.run_native(change)
        self.assertEqual(result['outcome'], 'applied')
        self.assertEqual(result['fixture_observed']['title'], change['title'])
        self.assertEqual(result['fixture_observed']['notes'], change['notes'])
        self.assertEqual(result['fixture_observed']['deadline'], change['deadline'])

    def test_native_changed_description_rejected_before_mutation(self):
        result = self.run_native({'operation': 'update', 'task_id': 'synthetic-task', 'notes': 'New notes'},
                                 baseline_notes='Stale description')
        self.assertEqual(result['outcome'], 'not_started')
        self.assertNotIn('fixture_observed', result)


if __name__ == '__main__':
    unittest.main()
