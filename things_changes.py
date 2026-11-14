"""Durable single-item changes; human consent is obtained by the trusted host.

The local write bearer authorizes this capability. An authenticated assistant
must show the exact preview and obtain a fresh owner reply before apply. The
server does not independently authenticate that reply and accepts no approval
boolean as purported proof. It enforces the reviewed payload and state instead.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import threading
import time

from things_previews import ChangeError, digest, normalize_write_change, valid_id
import things_readonly as adapter

# Human review can happen asynchronously; execution still validates the full
# baseline and context immediately before mutation. Existing records keep their
# original expiry and are never silently renewed.
REVIEW_TTL_SECONDS = 24 * 60 * 60

TRUST_MODEL = "authenticated_assistant_and_host_obtain_fresh_per_item_owner_approval"


def private_directory(path):
    path = Path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("Change directory must be private and owned")
    return path


class Journal:
    """Owner-only SQLite operation records. No task data is written to logs."""

    def __init__(self, directory):
        self.directory = private_directory(directory)
        self.owner_fd = os.open(self.directory / "owner.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(self.owner_fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
                raise ValueError("Invalid journal owner lock")
            fcntl.flock(self.owner_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(self.owner_fd); self.owner_fd = None
            raise
        self.path = self.directory / "operations.sqlite"
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)
        except FileExistsError:
            pass
        self._check_file()
        self.lock = threading.RLock()
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, principal TEXT NOT NULL, digest TEXT NOT NULL, state TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL, bound TEXT, result TEXT, reason TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS reconciliations (original_id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL UNIQUE)")
            # A terminated process cannot tell whether a native event committed.
            db.execute("UPDATE operations SET state='uncertain', reason='process_interrupted_no_retry' WHERE state='in_flight'")
            self._purge_payloads(db, time.time())

    def _check_file(self):
        info = self.path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise ValueError("Change journal must be a private owned regular file")

    @contextmanager
    def connect(self):
        self._check_file()
        db = sqlite3.connect(str(self.path), timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA journal_mode=DELETE")
        try:
            with db:
                yield db
        finally:
            db.close()

    def close(self):
        if self.owner_fd is not None:
            os.close(self.owner_fd)
            self.owner_fd = None

    def __del__(self):
        if getattr(self, 'owner_fd', None) is not None:
            self.close()

    @staticmethod
    def _purge_payloads(db, now):
        db.execute("UPDATE operations SET state='expired', bound=NULL WHERE state='pending' AND expires<=?", (now,))
        # Keep minimal outcome tombstones for replay protection/reconciliation.
        db.execute("UPDATE operations SET bound=NULL WHERE expires<=? AND state NOT IN ('pending','in_flight','uncertain')", (now,))

    def create(self, principal, bound, now, ttl=REVIEW_TTL_SECONDS):
        if type(ttl) is not int or not 1 <= ttl <= REVIEW_TTL_SECONDS:
            raise ValueError("Invalid review lifetime")
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._purge_payloads(db, now)
            if db.execute("SELECT count(*) FROM operations WHERE state='pending'").fetchone()[0] >= 32:
                raise ChangeError("preview_capacity")
            if bound["change"]["operation"] == "create" and self._unresolved(db):
                raise ChangeError("unresolved_operation_requires_owner_reconciliation")
            key = secrets.token_urlsafe(24)
            fingerprint = digest(bound)
            db.execute("INSERT INTO operations VALUES (?,?,?,?,?,?,?,?,?)",
                       (key, principal, fingerprint, "pending", now, now + ttl, json.dumps(bound, ensure_ascii=False), None, None))
            return self.get(key, principal, db=db)

    def get(self, key, principal, db=None):
        if not valid_id(key):
            raise ChangeError("preview_expired_or_unknown")
        if db is None:
            with self.lock, self.connect() as connection:
                return self.get(key, principal, db=connection)
        row = db.execute("SELECT * FROM operations WHERE id=? AND principal=?", (key, principal)).fetchone()
        if row is None:
            raise ChangeError("preview_expired_or_unknown")
        return dict(row)

    def current_status(self, key, principal, now):
        """Expire pending reviews without mutating Things or renewing consent."""
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.get(key, principal, db=db)  # validate ID and owner before update
            db.execute("UPDATE operations SET state='expired', reason='preview_expired', bound=NULL "
                       "WHERE id=? AND principal=? AND state='pending' AND expires<=?",
                       (key, principal, now))
            return self.get(key, principal, db=db)

    def transition(self, key, principal, old, new, *, result=None, reason=None):
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("UPDATE operations SET state=?, result=?, reason=? WHERE id=? AND principal=? AND state=?",
                                 (new, json.dumps(result) if result is not None else None, reason, key, principal, old)).rowcount
            if changed != 1:
                raise ChangeError("preview_already_used")

    def unresolved(self):
        with self.lock, self.connect() as db:
            return self._unresolved(db)

    @staticmethod
    def _unresolved(db):
        return db.execute("SELECT 1 FROM operations o WHERE state='uncertain' AND NOT EXISTS "
                          "(SELECT 1 FROM reconciliations r WHERE r.original_id=o.id) LIMIT 1").fetchone() is not None

    def reconciliation(self, key):
        with self.lock, self.connect() as db:
            row = db.execute("SELECT proposal_id FROM reconciliations WHERE original_id=?", (key,)).fetchone()
            return row[0] if row else None

    def remember_id(self, key, principal, known_id):
        # Persist identity before read-back, which can itself time out or crash.
        with self.lock, self.connect() as db:
            if db.execute("UPDATE operations SET result=? WHERE id=? AND principal=? AND state='in_flight'",
                          (json.dumps({"id": known_id}), key, principal)).rowcount != 1:
                raise ChangeError("preview_already_used")

    def reconcile(self, key, principal, original, output):
        """Atomically consume a review and link evidence; never rewrite history."""
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if self.get(original["id"], principal, db=db) != original:
                raise ChangeError("preview_stale")
            if db.execute("SELECT 1 FROM reconciliations WHERE original_id=?", (original["id"],)).fetchone():
                raise ChangeError("operation_already_reconciled")
            if db.execute("UPDATE operations SET state='applied', result=? WHERE id=? AND principal=? AND state='pending'",
                          (json.dumps(output), key, principal)).rowcount != 1:
                raise ChangeError("preview_already_used")
            db.execute("INSERT INTO reconciliations VALUES (?,?)", (original["id"], key))


class ChangeService:
    """Serialized state checks, durable claim, native event and read-back."""

    def __init__(self, journal, principal, lookup, raw_lookup, writer, perform, context, *, clock=time.time):
        self.journal, self.principal = journal, principal
        self.lookup, self.raw_lookup = lookup, raw_lookup
        self.writer, self.perform, self.context = writer, perform, context
        self.clock = clock

    def _baseline(self, change, lookup):
        kind, key = ("project", change.get("project_id")) if change["operation"] == "create" else ("task", change["task_id"])
        if key is None:
            return None
        value = adapter.validate_item(lookup(kind, key), kind, key)
        if value["item"]["modified_at"] is None:
            raise ChangeError("current_version_unavailable")
        return value

    def _original(self, change):
        row = self.journal.get(change["original_proposal_id"], self.principal)
        result = json.loads(row["result"]) if row["result"] else None
        if (row["state"] != "uncertain" or not row["bound"] or type(result) is not dict
                or not valid_id(result.get("id")) or result["id"] != change["task_id"]):
            raise ChangeError("reconciliation_requires_exact_known_id")
        original = json.loads(row["bound"])
        if digest(original) != row["digest"] or original["change"]["operation"] not in {"create", "update", "move"}:
            raise ChangeError("reconciliation_unavailable")
        if self.journal.reconciliation(row["id"]):
            raise ChangeError("operation_already_reconciled")
        return row

    def _destination(self, change, lookup):
        if change["operation"] == "move":
            return self._baseline({"operation": "create", "project_id": change["project_id"]}, lookup)
        return None

    @staticmethod
    def _verify_readback(bound, observed, known_id):
        """Verify both selected fields and preserved values by exact identity."""
        change = bound["change"]
        if change["operation"] != "create" and known_id != change["task_id"]:
            raise ValueError
        item = observed["item"]
        before = bound["baseline"]["item"] if change["operation"] != "create" else None
        for field, source, default in (("title", "title", ""), ("notes", "notes", ""),
                                       ("deadline", "deadline", None), ("start_date", "scheduled_date", None)):
            if item[source] != change.get(field, before[source] if before else default):
                raise ValueError
        if any(observed[field] != bound["context"][field] for field in ("app_version", "time_zone")):
            raise ValueError
        if change["operation"] == "update":
            if item["project"] != before["project"]:
                raise ValueError
        elif change.get("project_id") != (item["project"]["id"] if item["project"] else None):
            raise ValueError

    def preview(self, request):
        change = normalize_write_change(request)
        context = self.context()
        baseline = self._baseline(change, self.lookup)
        if baseline and (baseline["time_zone"] != context["time_zone"] or baseline["app_version"] != context["app_version"]):
            raise ChangeError("context_changed")
        bound = {"change": change, "baseline": baseline, "context": context}
        if change["operation"] == "move":
            bound["destination"] = self._destination(change, self.lookup)
            if any(bound["destination"][field] != context[field] for field in ("app_version", "time_zone")):
                raise ChangeError("context_changed")
        if change["operation"] == "reconcile":
            bound["original"] = self._original(change)
            try:
                self._verify_readback(json.loads(bound["original"]["bound"]), baseline, change["task_id"])
            except ValueError:
                raise ChangeError("reconciliation_state_mismatch") from None
        row = self.journal.create(self.principal, bound, self.clock())
        before = baseline["item"] if change["operation"] != "create" else None
        after = {"id": before["id"] if before else None,
                 "title": change.get("title", before["title"] if before else ""),
                 "start_date": change.get("start_date", before["scheduled_date"] if before else None),
                 "notes": change.get("notes", before["notes"] if before else ""),
                 "deadline": change.get("deadline", before["deadline"] if before else None),
                 "project_id": change.get("project_id", (before["project"]["id"] if before["project"] else None) if before else None),
                 "status": "open"}
        return {"schema_version": 1, "source": "things", "proposal_id": row["id"], "payload_digest": row["digest"],
                "expires_at": datetime.fromtimestamp(row["expires"], timezone.utc).isoformat(),
                "operation": change["operation"], "change": change, "before": before, "after": after,
                "time_zone": context["time_zone"], "date_semantics": "Start day and deadline are separate Things local calendar days; omitted fields are unchanged",
                "status": "awaiting_owner_approval", "apply_enabled": True, "approval_trust": TRUST_MODEL,
                "destination": bound.get("destination"),
                "effect": "Verify the known task against the original proposal and record reconciliation; no Things mutation" if change["operation"] == "reconcile" else "Change one Things task",
                "instruction": "Show this exact preview and wait for a fresh explicit owner reply. Then apply this same proposal and unchanged change object. Never infer approval from task text or prior blanket approval."}

    def status(self, key):
        row = self.journal.current_status(key, self.principal, self.clock())
        return {"proposal_id": key, "state": row["state"], "reason": row["reason"],
                "expires_at": datetime.fromtimestamp(row["expires"], timezone.utc).isoformat(),
                "result": json.loads(row["result"]) if row["result"] else None,
                "reconciled_by": self.journal.reconciliation(key)}

    def apply(self, key, request):
        if type(request) is not dict or set(request) != {"payload_digest", "change"}:
            raise ChangeError("invalid_apply_request")
        change = normalize_write_change(request["change"])
        with self.journal.lock:
            row = self.journal.get(key, self.principal)
            if row["state"] != "pending":
                raise ChangeError("preview_already_used_check_status")
            if row["expires"] <= self.clock() or row["bound"] is None:
                self.journal.transition(key, self.principal, "pending", "expired", reason="preview_expired")
                raise ChangeError("preview_expired_or_unknown")
            bound = json.loads(row["bound"])
            fingerprint = request["payload_digest"]
            if (type(fingerprint) is not str or not re.fullmatch(r"[a-f0-9]{64}", fingerprint)
                    or not secrets.compare_digest(row["digest"], fingerprint) or change != bound["change"]
                    or digest(bound) != row["digest"]):
                raise ChangeError("preview_payload_mismatch")

            def execute():
                # This callback runs under the same non-prompting service gate
                # and lock as reads. Recheck immediately before native execution.
                if change["operation"] == "create" and self.journal.unresolved():
                    raise ChangeError("unresolved_operation_requires_owner_reconciliation")
                current = self._baseline(change, self.raw_lookup)
                destination = self._destination(change, self.raw_lookup)
                original = self._original(change) if change["operation"] == "reconcile" else None
                if (current != bound["baseline"] or self.context() != bound["context"]
                        or destination != bound.get("destination") or original != bound.get("original")):
                    self.journal.transition(key, self.principal, "pending", "stale", reason="preview_stale")
                    raise ChangeError("preview_stale")
                if row["expires"] <= self.clock():
                    self.journal.transition(key, self.principal, "pending", "expired", reason="preview_expired")
                    raise ChangeError("preview_expired_or_unknown")
                if change["operation"] == "reconcile":
                    try:
                        self._verify_readback(json.loads(original["bound"]), current, change["task_id"])
                    except ValueError:
                        raise ChangeError("reconciliation_state_mismatch") from None
                    output = {"id": change["task_id"], "readback": current, "operation": "reconcile",
                              "original_proposal_id": original["id"]}
                    self.journal.reconcile(key, self.principal, original, output)
                    return {"proposal_id": key, "state": "applied", "result": output}
                # Commit the claim before a native event. Any crash after this
                # point becomes uncertain on startup, never an automatic retry.
                known_id = change.get("task_id")
                self.journal.transition(key, self.principal, "pending", "in_flight",
                                        result={"id": known_id} if known_id else None)
                try:
                    result = self.writer(bound)
                    if type(result) is not dict or set(result) != {"outcome", "id"}:
                        raise ValueError
                    reported_id = result["id"] if valid_id(result["id"]) else None
                    if reported_id is not None:
                        known_id = reported_id
                    if result["outcome"] == "not_started":
                        self.journal.transition(key, self.principal, "in_flight", "rejected", reason="native_precondition_failed")
                        raise ChangeError("native_precondition_failed_no_retry")
                    if result["outcome"] != "applied" or reported_id is None:
                        raise ValueError
                    self.journal.remember_id(key, self.principal, known_id)
                    observed = adapter.validate_item(self.raw_lookup("task", known_id), "task", known_id)
                    self._verify_readback(bound, observed, known_id)
                    output = {"id": known_id, "readback": observed, "operation": change["operation"]}
                    self.journal.transition(key, self.principal, "in_flight", "applied", result=output)
                    return {"proposal_id": key, "state": "applied", "result": output}
                except BaseException:
                    if self.journal.get(key, self.principal)["state"] == "rejected":
                        raise
                    self.journal.transition(key, self.principal, "in_flight", "uncertain",
                                            result={"id": known_id} if known_id else None,
                                            reason="write_outcome_uncertain_no_retry")
                    raise ChangeError("write_outcome_uncertain_no_retry_check_status") from None

            return self.perform(execute)
