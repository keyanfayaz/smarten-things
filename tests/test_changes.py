"""Durable scoped-write integration using synthetic records/writers only."""
import copy
import http.client
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from test_previews import item
from things_change_client import ChangeClient, ChangeClientError
from things_changes import ChangeService, Journal, REVIEW_TTL_SECONDS
from things_native_write import NativeWriter
from things_previews import ChangeError
import things_access
import things_mcp
import things_service as service


class DurableChanges(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)/'changes'
        self.journal = Journal(self.directory); self.addCleanup(lambda: self.journal.close())
        self.now = time.time()
        self.context = {'app_version': 'synthetic', 'time_zone': 'America/Los_Angeles'}
        self.records = {'synthetic-task': item(), 'synthetic-project': item('project', 'synthetic-project')}
        self.lookup = Mock(side_effect=lambda kind, key: copy.deepcopy(self.records[key]))
        self.writer = Mock(side_effect=self.write)
        self.engine = self.make_engine()

    def make_engine(self):
        return ChangeService(self.journal, 'synthetic-owner', self.lookup, self.lookup, self.writer,
                             lambda action: action(), lambda: dict(self.context), clock=lambda: self.now)

    def write(self, bound):
        change = bound['change']; key = change.get('task_id', 'synthetic-created')
        record = self.records.get(key, item('task', key))
        record['item']['title'] = change.get('title', record['item']['title'])
        if 'start_date' in change: record['item']['scheduled_date'] = change['start_date']
        for field in ('notes', 'deadline'):
            if field in change: record['item'][field] = change[field]
        if change.get('project_id'): record['item']['project'] = {'id': change['project_id'], 'title': 'Synthetic title'}
        self.records[key] = record
        return {'outcome': 'applied', 'id': key}

    def preview(self, **extra):
        return self.engine.preview({'operation': 'update', 'task_id': 'synthetic-task', 'title': 'Synthetic changed', **extra})

    def apply(self, preview, **extra):
        return self.engine.apply(preview['proposal_id'], {'payload_digest': preview['payload_digest'], 'change': preview['change'], **extra})

    def error(self, reason, function, *args, **kwargs):
        with self.assertRaises(ChangeError) as caught: function(*args, **kwargs)
        self.assertEqual(caught.exception.reason, reason)

    def test_preview_contains_exact_payload_and_host_trust_no_mutation(self):
        proposal = self.preview(start_date='2026-11-01')
        self.assertEqual(proposal['before']['id'], proposal['after']['id'])
        self.assertEqual(proposal['change']['start_date'], '2026-11-01')
        self.assertEqual(proposal['status'], 'awaiting_owner_approval')
        self.assertIn('assistant', proposal['approval_trust'])
        self.writer.assert_not_called()

    def test_create_inbox_and_exact_project_return_known_id_and_readback(self):
        for project in [None, 'synthetic-project']:
            request = {'operation': 'create', 'title': 'Synthetic creation', 'start_date': '2028-02-29'}
            if project: request['project_id'] = project
            proposal = self.engine.preview(request)
            result = self.apply(proposal)
            self.assertEqual(result['state'], 'applied')
            self.assertEqual(result['result']['id'], 'synthetic-created')
            self.assertEqual(result['result']['readback']['item']['scheduled_date'], '2028-02-29')

    def test_changed_payload_digest_and_fake_approval_fields_rejected(self):
        proposal = self.preview()
        self.error('invalid_apply_request', self.apply, proposal, approved=True)
        self.error('preview_payload_mismatch', self.engine.apply, proposal['proposal_id'], {'payload_digest': '0'*64, 'change': proposal['change']})
        changed = dict(proposal['change'], title='Different from approved preview')
        self.error('preview_payload_mismatch', self.engine.apply, proposal['proposal_id'], {'payload_digest': proposal['payload_digest'], 'change': changed})
        self.writer.assert_not_called()

    def test_create_with_notes_and_both_dates_then_update_and_clear(self):
        proposal = self.engine.preview({'operation': 'create', 'title': 'Synthetic creation',
            'notes': 'First line\nSecond line — café 🗓', 'start_date': '2028-02-29', 'deadline': '2028-03-05'})
        self.assertEqual(proposal['after']['notes'], proposal['change']['notes'])
        self.assertEqual(proposal['after']['deadline'], '2028-03-05')
        result = self.apply(proposal)['result']['readback']['item']
        self.assertEqual(result['notes'], proposal['change']['notes'])
        self.assertEqual(result['scheduled_date'], '2028-02-29')
        self.assertEqual(result['deadline'], '2028-03-05')
        clear = self.engine.preview({'operation': 'update', 'task_id': result['id'], 'notes': '', 'deadline': None})
        self.assertEqual(clear['before']['deadline'], '2028-03-05')
        observed = self.apply(clear)['result']['readback']['item']
        self.assertEqual(observed['notes'], '')
        self.assertIsNone(observed['deadline'])
        self.assertEqual(observed['scheduled_date'], '2028-02-29')
        self.assertEqual(observed['title'], 'Synthetic creation')

    def test_notes_only_and_deadline_only_updates_preserve_omitted_fields(self):
        record = self.records['synthetic-task']['item']
        record.update(notes='Original description', deadline='2028-03-05', scheduled_date='2028-02-29')
        for change in ({'notes': 'Updated description'}, {'deadline': '2028-03-06'}):
            proposal = self.engine.preview({'operation': 'update', 'task_id': 'synthetic-task', **change})
            result = self.apply(proposal)['result']['readback']['item']
            self.assertEqual(result['notes'], 'Updated description')
            self.assertEqual(result['scheduled_date'], '2028-02-29')
            self.assertEqual(result['title'], 'Synthetic title')

    def test_concurrent_notes_or_deadline_edits_invalidate_preview(self):
        for field, changed in (('notes', 'Concurrent edit'), ('deadline', '2028-03-05')):
            proposal = self.preview()
            self.records['synthetic-task']['item'][field] = changed
            self.error('preview_stale', self.apply, proposal)
        self.writer.assert_not_called()

    def test_changed_notes_or_deadline_cannot_replace_reviewed_payload(self):
        for extra in ({'notes': 'Different'}, {'deadline': None}):
            proposal = self.engine.preview({'operation': 'update', 'task_id': 'synthetic-task',
                'notes': 'Reviewed description', 'deadline': '2028-03-05'})
            self.error('preview_payload_mismatch', self.engine.apply, proposal['proposal_id'],
                {'payload_digest': proposal['payload_digest'], 'change': dict(proposal['change'], **extra)})
        self.writer.assert_not_called()

    def test_unverified_notes_deadline_or_unintended_change_is_uncertain(self):
        original_writer = self.write
        for field, bad in (('notes', 'Wrong content'), ('deadline', '2028-03-07'), ('scheduled_date', '2028-03-08')):
            def incorrect(bound):
                result = original_writer(bound)
                self.records[result['id']]['item'][field] = bad
                return result
            self.writer.side_effect = incorrect
            proposal = self.preview(notes='Expected content', deadline='2028-03-05')
            self.error('write_outcome_uncertain_no_retry_check_status', self.apply, proposal)
            self.assertEqual(self.engine.status(proposal['proposal_id'])['state'], 'uncertain')

    def test_stale_task_or_host_zone_rejected_before_any_event(self):
        for field in ['title', 'zone']:
            proposal = self.preview()
            if field == 'title': self.records['synthetic-task']['item']['title'] += ' concurrent edit'
            else: self.context['time_zone'] = 'UTC'
            self.error('preview_stale', self.apply, proposal)
            self.assertEqual(self.engine.status(proposal['proposal_id'])['state'], 'stale')
        self.writer.assert_not_called()

    def test_expiry_and_once_only_replay(self):
        proposal = self.preview(); self.now += REVIEW_TTL_SECONDS + 1
        self.error('preview_expired_or_unknown', self.apply, proposal)
        self.assertEqual(self.engine.status(proposal['proposal_id'])['state'], 'expired')
        proposal = self.preview(); self.apply(proposal)
        self.error('preview_already_used_check_status', self.apply, proposal)
        self.assertEqual(self.writer.call_count, 1)

    def test_review_can_wait_until_later_without_new_proposal(self):
        proposal = self.preview()
        self.now += 6 * 60 * 60
        status = self.engine.status(proposal['proposal_id'])
        self.assertEqual(status['state'], 'pending')
        self.assertEqual(status['expires_at'], proposal['expires_at'])
        self.assertEqual(self.apply(proposal)['state'], 'applied')
        self.assertEqual(self.writer.call_count, 1)

    def test_delayed_review_still_rejects_changed_baseline(self):
        proposal = self.preview()
        self.now += 6 * 60 * 60
        self.records['synthetic-task']['item']['notes'] = 'Changed while away'
        self.error('preview_stale', self.apply, proposal)
        self.writer.assert_not_called()

    def test_status_expires_at_boundary_and_clears_payload_without_write(self):
        proposal = self.preview()
        self.now += REVIEW_TTL_SECONDS
        status = self.engine.status(proposal['proposal_id'])
        self.assertEqual(status['state'], 'expired')
        self.assertEqual(status['reason'], 'preview_expired')
        self.assertIsNone(self.journal.get(proposal['proposal_id'], 'synthetic-owner')['bound'])
        self.error('preview_already_used_check_status', self.apply, proposal)
        self.writer.assert_not_called()

    def test_old_short_lifetime_is_not_extended(self):
        proposal = self.preview()
        row = self.journal.get(proposal['proposal_id'], 'synthetic-owner')
        with self.journal.connect() as db:
            db.execute('UPDATE operations SET expires=? WHERE id=?', (self.now + 300, row['id']))
        self.now += 301
        self.assertEqual(self.engine.status(row['id'])['state'], 'expired')
        self.writer.assert_not_called()

    def test_status_cannot_expire_another_principals_proposal(self):
        proposal = self.preview()
        self.now += REVIEW_TTL_SECONDS + 1
        self.error('preview_expired_or_unknown', self.journal.current_status,
                   proposal['proposal_id'], 'other-owner', self.now)
        self.assertEqual(self.journal.get(proposal['proposal_id'], 'synthetic-owner')['state'], 'pending')

    def test_durable_result_survives_restart_and_cannot_replay(self):
        proposal = self.preview(); self.apply(proposal)
        self.journal.close(); self.journal = Journal(self.directory); self.engine = self.make_engine()
        self.assertEqual(self.engine.status(proposal['proposal_id'])['state'], 'applied')
        self.error('preview_already_used_check_status', self.apply, proposal)
        self.assertEqual(self.writer.call_count, 1)

    def test_interrupted_inflight_recovers_uncertain_blocks_create_and_retry(self):
        proposal = self.engine.preview({'operation': 'create', 'title': 'Synthetic'})
        self.journal.transition(proposal['proposal_id'], 'synthetic-owner', 'pending', 'in_flight')
        self.journal.close(); self.journal = Journal(self.directory); self.engine = self.make_engine()
        self.assertEqual(self.engine.status(proposal['proposal_id'])['state'], 'uncertain')
        self.error('preview_already_used_check_status', self.apply, proposal)
        self.error('unresolved_operation_requires_owner_reconciliation', self.engine.preview, {'operation': 'create', 'title': 'Another'})
        self.writer.assert_not_called()

    def test_unknown_native_outcome_or_failed_readback_persists_uncertain(self):
        proposal = self.preview(); self.writer.side_effect = TimeoutError('SYNTHETIC_PRIVATE')
        self.error('write_outcome_uncertain_no_retry_check_status', self.apply, proposal)
        self.assertEqual(self.engine.status(proposal['proposal_id'])['state'], 'uncertain')
        self.error('preview_already_used_check_status', self.apply, proposal)
        self.assertEqual(self.writer.call_count, 1)

    def test_preexisting_create_preview_cannot_bypass_uncertain_operation(self):
        first = self.engine.preview({'operation': 'create', 'title': 'First synthetic'})
        second = self.engine.preview({'operation': 'create', 'title': 'Second synthetic'})
        self.writer.side_effect = TimeoutError('SYNTHETIC_PRIVATE')
        self.error('write_outcome_uncertain_no_retry_check_status', self.apply, first)
        self.error('unresolved_operation_requires_owner_reconciliation', self.apply, second)
        self.assertEqual(self.writer.call_count, 1)

    def test_native_precondition_failure_is_rejected_and_never_replayed(self):
        proposal = self.preview(); self.writer.side_effect = None
        self.writer.return_value = {'outcome': 'not_started', 'id': None}
        self.error('native_precondition_failed_no_retry', self.apply, proposal)
        self.assertEqual(self.engine.status(proposal['proposal_id'])['state'], 'rejected')
        self.error('preview_already_used_check_status', self.apply, proposal)

    def test_wrong_update_id_or_readback_content_is_uncertain(self):
        proposal = self.preview(); self.writer.side_effect = None
        self.writer.return_value = {'outcome': 'applied', 'id': 'some-other-task'}
        self.error('write_outcome_uncertain_no_retry_check_status', self.apply, proposal)
        self.assertEqual(self.engine.status(proposal['proposal_id'])['result'], {'id': 'some-other-task'})

    def test_principal_scope_and_cross_thread_claim(self):
        proposal = self.preview()
        self.error('preview_expired_or_unknown', self.journal.get, proposal['proposal_id'], 'another-owner')
        result = []
        def run():
            try: result.append(self.apply(proposal)['state'])
            except ChangeError as error: result.append(error.reason)
        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=3)
        self.assertCountEqual(result, ['applied', 'preview_already_used_check_status'])
        self.assertEqual(self.writer.call_count, 1)

    def test_private_journal_and_exclusive_process_owner(self):
        self.assertEqual(stat.S_IMODE(self.journal.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        with self.assertRaises(BlockingIOError): Journal(self.directory)


class ScopedHTTPTests(unittest.TestCase):
    setUp = DurableChanges.setUp
    make_engine = DurableChanges.make_engine
    write = DurableChanges.write

    def test_mcp_http_preview_apply_and_status_roundtrip_with_multiline_notes(self):
        read_token = 'SYNTHETIC_READ_'+'r'*43
        write_token = 'SYNTHETIC_WRITE_'+'w'*43
        server = service.Server(('127.0.0.1', 0), read_token, reader=Mock(), write_token=write_token, changes=self.engine)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                key = Path(directory)/'write-token'
                key.write_text(write_token); key.chmod(0o600)
                client = ChangeClient(key, 'http://127.0.0.1:'+str(server.server_port))
                def call(name, arguments):
                    response = things_mcp.dispatch({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                        'params': {'name': name, 'arguments': arguments}}, Mock(), changes=client)['result']
                    self.assertFalse(response['isError'])
                    return json.loads(response['content'][0]['text'])
                change = {'operation': 'update', 'task_id': 'synthetic-task',
                    'notes': 'Description\nUnicode: café 🗓\n"quoted" \\ backslash',
                    'start_date': '2028-02-29', 'deadline': '2028-03-05'}
                proposal = call('preview_things_task_change', change)
                self.writer.assert_not_called()
                arguments = {key: proposal[key] for key in ('proposal_id', 'payload_digest', 'change')}
                applied = call('apply_things_task_change', arguments)
                self.assertEqual(applied['result']['readback']['item']['notes'], change['notes'])
                self.assertEqual(applied['result']['readback']['item']['deadline'], change['deadline'])
                self.assertEqual(call('get_things_change_status', {'proposal_id': proposal['proposal_id']})['state'], 'applied')
                self.assertEqual(self.writer.call_count, 1)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_duplicate_json_and_http_enable_attempts_are_rejected(self):
        read_token='SYNTHETIC_READ_'+'r'*43; write_token='SYNTHETIC_WRITE_'+'w'*43
        server=service.Server(('127.0.0.1',0),read_token,reader=Mock(),write_token=write_token,changes=self.engine)
        thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01},daemon=True); thread.start()
        try:
            connection=http.client.HTTPConnection(*server.server_address,timeout=2)
            raw=b'{"operation":"create","title":"First","title":"Changed"}'
            connection.request('POST','/v1/change-previews',raw,{'Authorization':'Bearer '+write_token,'Content-Type':'application/json'})
            response=connection.getresponse(); response.read(); connection.close()
            self.assertEqual(response.status,400)
            connection=http.client.HTTPConnection(*server.server_address,timeout=2)
            connection.request('POST','/v1/write-capability',b'{}',{'Authorization':'Bearer '+read_token,'Content-Type':'application/json'})
            response=connection.getresponse(); response.read(); connection.close()
            self.assertEqual(response.status,405)
            self.assertFalse(server.local_write_enable_requested)
            self.writer.assert_not_called()
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)
    def test_read_token_cannot_preview_apply_or_read_write_records(self):
        read_token = 'SYNTHETIC_READ_'+'r'*43; write_token = 'SYNTHETIC_WRITE_'+'w'*43
        server = service.Server(('127.0.0.1', 0), read_token, reader=Mock(), write_token=write_token, changes=self.engine)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True); thread.start()
        try:
            def request(method, path, token, body=None, headers=None):
                data=json.dumps(body).encode() if body is not None else None
                h={'Authorization':'Bearer '+token}
                if data is not None: h['Content-Type']='application/json'
                h.update(headers or {})
                connection=http.client.HTTPConnection(*server.server_address, timeout=2)
                connection.request(method,path,data,h); response=connection.getresponse(); value=json.loads(response.read()); connection.close()
                return response.status,value
            self.assertEqual(request('POST','/v1/change-previews',read_token,{'operation':'create','title':'Synthetic'})[0],401)
            code,proposal=request('POST','/v1/change-previews',write_token,{'operation':'create','title':'Synthetic'})
            self.assertEqual(code,200)
            body={'payload_digest':proposal['payload_digest'],'change':proposal['change']}
            path='/v1/change-previews/'+proposal['proposal_id']
            self.assertEqual(request('POST',path+'/apply',read_token,body)[0],401)
            self.assertEqual(request('GET',path,read_token)[0],401)
            self.assertEqual(request('POST',path+'/apply',write_token,body,{'Origin':'https://untrusted.invalid'})[0],403)
            self.assertEqual(request('POST',path+'/apply',write_token,body)[0],200)
            self.assertEqual(request('POST',path+'/apply',write_token,body)[0],409)
            self.assertEqual(self.writer.call_count,1)
            with tempfile.TemporaryDirectory() as directory:
                key=Path(directory)/'write-token'; key.write_text(write_token); key.chmod(0o600)
                client=ChangeClient(key, 'http://127.0.0.1:'+str(server.server_port))
                self.assertEqual(client.status({'proposal_id':proposal['proposal_id']})['state'],'applied')
                with self.assertRaises(ChangeError): client.apply_request({'proposal_id':proposal['proposal_id'],'approved':True})
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_service_starts_without_write_capability_and_same_token_rejected(self):
        token='SYNTHETIC_'+'x'*43
        server=service.Server(('127.0.0.1',0), token, reader=Mock())
        try:
            self.assertIsNone(server.changes); self.assertIsNone(server.write_token)
            self.assertFalse(server.local_write_enable_requested)
        finally: server.server_close()
        with self.assertRaises(ValueError): service.Server(('127.0.0.1',0),token,reader=Mock(),write_token=token,changes=self.engine)


class NativeBoundaryTests(unittest.TestCase):
    def test_write_denial_does_not_disable_existing_read_authorization(self):
        reader=service.LiveReader()
        ready={'reason':'ready','automation':'granted'}
        with patch.object(reader,'_preflight',return_value=ready), patch.object(service,'write_permission_status',return_value='denied') as permission:
            with self.assertRaises(service.ReadUnavailable): reader.perform_change(Mock())
            with self.assertRaises(service.ReadUnavailable): reader.perform_change(Mock())
            self.assertEqual(permission.call_count,1)
            self.assertTrue(reader.write_denied)
            self.assertFalse(reader.denied)
            with patch.object(service.adapter,'read_page',return_value={'synthetic':'read still works'}):
                self.assertEqual(reader(object()),{'synthetic':'read still works'})

    def test_payload_private_file_removed_and_no_inline_payload_or_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            bound={'change':{'operation':'create','title':'SYNTHETIC_PRIVATE_TITLE'},'baseline':None,'context':{}}
            seen=[]
            def run(argv, **kwargs):
                self.assertEqual(argv[0],'/usr/bin/osascript')
                self.assertNotIn('SYNTHETIC_PRIVATE_TITLE',' '.join(argv))
                path=Path(argv[-1]); seen.append(path)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)
                self.assertEqual(json.loads(path.read_text()),bound)
                return subprocess.CompletedProcess(argv,0,json.dumps({'outcome':'applied','id':'synthetic-new'}),'')
            writer=NativeWriter(Path(directory)/'private',runner=run)
            with patch('things_native_write.write_capabilities'):
                self.assertEqual(writer(bound)['id'],'synthetic-new')
            self.assertFalse(seen[0].exists())

    def test_write_permission_gate_never_requests_os_prompt(self):
        with patch.object(things_access,'permission_status',return_value='granted') as permission:
            self.assertEqual(things_access.write_permission_status(),'granted')
        self.assertEqual(permission.call_count,3)
        self.assertTrue(all(call.args==(False,) for call in permission.call_args_list))

    def test_apply_metadata_is_write_and_requires_scoped_client(self):
        request={'jsonrpc':'2.0','id':1,'method':'tools/list'}
        default=things_mcp.dispatch(request,Mock())['result']['tools']
        self.assertEqual([x['name'] for x in default],['read_things_page'])
        tools=things_mcp.dispatch(request,Mock(),changes=Mock())['result']['tools']
        apply=next(x for x in tools if x['name']=='apply_things_task_change')
        self.assertFalse(apply['annotations']['readOnlyHint'])
        self.assertTrue(apply['annotations']['destructiveHint'])
        self.assertFalse(apply['annotations']['idempotentHint'])


if __name__=='__main__': unittest.main()
