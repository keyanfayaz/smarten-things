"""Permission deadline tests with synthetic callbacks, no native Apple Events."""
import os
import threading
import time
import unittest
from unittest.mock import Mock, patch

import things_access
import things_service as service


class PermissionDeadlineTests(unittest.TestCase):
    def test_prompt_free_success_checks_in_same_process_with_exact_event(self):
        seen = []
        def native(request, **event):
            seen.append((os.getpid(), request, event))
            return 'granted'
        probe = things_access._PermissionProbe(native, timeout=.1)
        self.assertEqual(probe.check(False, event_class=b'core', event_id=b'setd'), 'granted')
        self.assertEqual(seen, [(os.getpid(), False, {'event_class': b'core', 'event_id': b'setd'})])

    def test_hung_check_times_out_without_reads_writes_prompts_or_thread_growth(self):
        release = threading.Event()
        native = Mock(side_effect=lambda *args, **kwargs: release.wait(2) and 'granted')
        probe = things_access._PermissionProbe(native, timeout=.02)
        try:
            with patch.object(things_access, '_permission_probe', probe), \
                    patch.object(service.adapter, 'read_page') as read:
                reader = service.LiveReader()
                started = time.monotonic()
                self.assertEqual(reader.diagnostics()['preflight']['reason'], 'automation_check_timed_out')
                self.assertLess(time.monotonic() - started, 1)
                self.assertTrue(reader.lock.acquire(timeout=.1)); reader.lock.release()
                for _ in range(10):
                    with self.assertRaises(service.ReadUnavailable) as caught: reader(None)
                    self.assertEqual(caught.exception.reason, 'automation_check_timed_out')
                with self.assertRaises(service.ReadUnavailable): reader.perform_change(Mock())
                native.assert_called_once_with(False, event_class=b'core', event_id=b'getd')
                read.assert_not_called()
        finally:
            row = probe.active
            release.set()
            if row: self.assertTrue(row['done'].wait(1))

    def test_late_grant_is_discarded_and_next_check_requires_fresh_native_result(self):
        release = threading.Event()
        calls = []
        def check(*args, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                release.wait(2)
                return 'granted'
            return 'consent_required'
        probe = things_access._PermissionProbe(check, timeout=.02)
        self.assertEqual(probe.check(), 'timed_out')
        row = probe.active; release.set(); self.assertTrue(row['done'].wait(1))
        self.assertEqual(probe.check(), 'consent_required')
        self.assertEqual(len(calls), 2)

    def test_late_denial_latches_only_its_event_and_cannot_prompt_or_retry(self):
        release = threading.Event()
        def native(request, **event):
            if event['event_id'] == b'setd':
                release.wait(2)
                return 'denied'
            return 'granted'
        checker = Mock(side_effect=native)
        probe = things_access._PermissionProbe(checker, timeout=.02)
        self.assertEqual(probe.check(event_id=b'setd'), 'timed_out')
        row = probe.active; release.set(); self.assertTrue(row['done'].wait(1))
        self.assertEqual(probe.check(event_id=b'setd'), 'denied')
        self.assertEqual(probe.check(True, event_id=b'setd'), 'denied')
        self.assertEqual(checker.call_count, 1)
        self.assertEqual(probe.check(), 'granted')  # write denial preserves reads

    def test_stalled_probe_cannot_queue_an_explicit_or_different_event_check(self):
        release = threading.Event()
        checker = Mock(side_effect=lambda *args, **kwargs: release.wait(2) and 'granted')
        probe = things_access._PermissionProbe(checker, timeout=.02)
        try:
            self.assertEqual(probe.check(), 'timed_out')
            self.assertEqual(probe.check(True), 'timed_out')
            self.assertEqual(probe.check(event_id=b'setd'), 'timed_out')
            self.assertEqual(checker.call_count, 1)
        finally:
            row = probe.active; release.set(); self.assertTrue(row['done'].wait(1))

    def test_native_exception_and_thread_start_failure_fail_closed(self):
        checker = Mock(side_effect=RuntimeError('SYNTHETIC_PRIVATE'))
        probe = things_access._PermissionProbe(checker, timeout=.1)
        self.assertEqual(probe.check(), 'unavailable')
        checker.reset_mock()
        with patch.object(threading.Thread, 'start', side_effect=RuntimeError('Synthetic worker failure')):
            self.assertEqual(probe.check(), 'unavailable')
        checker.assert_not_called()
        self.assertIsNone(probe.active)

    def test_explicit_owner_request_is_forwarded_without_automatic_escalation(self):
        checker = Mock(return_value='consent_required')
        probe = things_access._PermissionProbe(checker, timeout=.1)
        self.assertEqual(probe.check(), 'consent_required')
        self.assertEqual(probe.check(True), 'consent_required')
        self.assertEqual([call.args for call in checker.call_args_list], [(False,), (True,)])


if __name__ == '__main__': unittest.main()
