"""Bounded offline previews and strict shared task-change input validation.

The coordinator's apply state machine is testable with synthetic adapters. No
HTTP/MCP argument, boolean, nonce or configuration flag installs those adapters.
The legacy --enable-previews mode stays non-mutating. The separately scoped
production workflow in things_changes uses the accepted assistant/host consent
model and a durable journal; it does not use the synthetic receipt adapter here.
"""
import copy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import re
import secrets
import threading
import time


class ChangeError(Exception):
    """Only fixed reason codes cross the MCP boundary."""

    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def valid_id(value):
    return type(value) is str and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value) is not None


def valid_notes(value):
    if (type(value) is not str or len(value) > 8192
            or any((ord(char) < 32 and char not in '\t\r\n') or ord(char) == 127 for char in value)):
        return False
    try:
        return len(value.encode('utf-8')) <= 16384
    except UnicodeError:
        return False


def normalize_change(value):
    if type(value) is not dict or set(value) - {"operation", "task_id", "title", "start_date", "project_id", "notes", "deadline"}:
        raise ChangeError("invalid_change")
    change = copy.deepcopy(value)
    operation = change.get("operation")
    if operation == "create":
        if "title" not in change or "task_id" in change:
            raise ChangeError("invalid_change")
    elif operation == "update":
        if not valid_id(change.get("task_id")) or "project_id" in change or not ({"title", "start_date", "notes", "deadline"} & change.keys()):
            raise ChangeError("invalid_change")
    else:
        raise ChangeError("invalid_change")
    if "project_id" in change and not valid_id(change["project_id"]):
        raise ChangeError("invalid_change")
    if "title" in change:
        title = change["title"]
        if (type(title) is not str or not title.strip() or len(title) > 8192
                or any(ord(char) < 32 or ord(char) == 127 for char in title)):
            raise ChangeError("invalid_change")
        try:
            title.encode('utf-8')
        except UnicodeError:
            raise ChangeError("invalid_change") from None
    if "notes" in change and not valid_notes(change["notes"]):
        raise ChangeError("invalid_change")
    for field in ("start_date", "deadline"):
        if field not in change or (field == "deadline" and change[field] is None):
            continue
        value = change[field]
        if type(value) is not str or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ChangeError("invalid_change")
        try:
            date.fromisoformat(value)
        except ValueError:
            raise ChangeError("invalid_change") from None
    return change


def digest(value):
    data = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


class PreviewCoordinator:
    """Private in-memory proposals; fail closed without a trusted approval bridge.

    lookup(kind, id) must return the validated exact-item response. The optional
    approval verifier and writer are internal dependencies used in synthetic
    tests only in this legacy coordinator. Production uses things_changes and
    the explicitly accepted assistant/host-mediated consent trust model.
    In particular, an approval receipt's existence is not itself approval.
    """

    def __init__(self, lookup, *, ttl=300, capacity=32, clock=time.monotonic,
                 wall_clock=lambda: datetime.now(timezone.utc), approval_verifier=None, writer=None):
        if type(ttl) is not int or not 1 <= ttl <= 300 or type(capacity) is not int or not 1 <= capacity <= 32:
            raise ValueError("Invalid preview bounds")
        self.lookup = lookup
        self.ttl, self.capacity = ttl, capacity
        self.clock, self.wall_clock = clock, wall_clock
        self._approval_verifier, self._writer = approval_verifier, writer
        self._records = {}
        self._lock = threading.Lock()

    def _current(self, change):
        kind, item_id = ("task", change["task_id"]) if change["operation"] == "update" else ("project", change.get("project_id"))
        if item_id is None:
            return None
        try:
            value = self.lookup(kind, item_id)
        except Exception:
            raise ChangeError("item_unavailable") from None
        # The actual client validates the complete envelope. Recheck identity
        # and version here, so a faulty injected lookup cannot retarget a write.
        if (type(value) is not dict or value.get("kind") != kind
                or type(value.get("item")) is not dict
                or value["item"].get("id") != item_id or value["item"].get("status") != "open"
                or not value["item"].get("modified_at")):
            raise ChangeError("item_unavailable")
        return copy.deepcopy(value)

    def preview(self, value):
        change = normalize_change(value)
        baseline = self._current(change)
        before = baseline["item"] if change["operation"] == "update" else None
        if before is None:
            after = {"id": None, "title": change["title"], "status": "open",
                     "project_id": change.get("project_id"), "start_date": change.get("start_date")}
        else:
            after = {"id": before["id"], "title": change.get("title", before["title"]), "status": "open",
                     "project_id": before["project"]["id"] if before["project"] else None,
                     "start_date": change.get("start_date", before["scheduled_date"])}
        after["notes"] = change.get("notes", before["notes"] if before else "")
        after["deadline"] = change.get("deadline", before["deadline"] if before else None)
        bound = {"change": change, "baseline": baseline,
                 "date_semantics": "Start day and deadline are separate Things local calendar days; omitted fields are unchanged"}
        with self._lock:
            now = self.clock()
            self._records = {key: row for key, row in self._records.items() if row["expires"] > now}
            if len(self._records) >= self.capacity:
                raise ChangeError("preview_capacity")
            proposal_id = secrets.token_urlsafe(24)
            payload_digest = digest(bound)
            expires_at = self.wall_clock() + timedelta(seconds=self.ttl)
            preview = {"schema_version": 1, "source": "things", "status": "preview_only",
                       "proposal_id": proposal_id, "payload_digest": payload_digest,
                       "expires_at": expires_at.isoformat(), "operation": change["operation"],
                       "before": copy.deepcopy(before), "after": after,
                       "date_semantics": bound["date_semantics"],
                       "time_zone": baseline["time_zone"] if baseline else "service-host local calendar",
                       "apply_enabled": False, "blocked_reason": "trusted_human_approval_unavailable"}
            self._records[proposal_id] = {"bound": bound, "digest": payload_digest, "expires": now + self.ttl,
                                           "state": "pending", "preview": preview}
            return copy.deepcopy(preview)

    def apply(self, proposal_id, payload_digest, approval_receipt):
        """Legacy synthetic state machine, not advertised over HTTP/MCP.

        A future integration must verify the human-origin receipt independently
        of model tool arguments. The state machine consumes the proposal before
        calling a writer; failures remain uncertain and are never auto-retried.
        Durable reconciliation across restart is additionally required before
        a real writer can be installed.
        """
        if self._approval_verifier is None or self._writer is None:
            raise ChangeError("trusted_human_approval_unavailable")
        if not valid_id(proposal_id):
            raise ChangeError("preview_expired_or_unknown")
        with self._lock:
            row = self._records.get(proposal_id)
            if row is None or row["expires"] <= self.clock():
                raise ChangeError("preview_expired_or_unknown")
            if type(payload_digest) is not str or not secrets.compare_digest(row["digest"], payload_digest):
                raise ChangeError("preview_payload_mismatch")
            if row["state"] != "pending":
                raise ChangeError("preview_already_used")
            try:
                approved = self._approval_verifier(proposal_id, row["digest"], approval_receipt)
            except Exception:
                approved = False
            if approved is not True:
                raise ChangeError("human_approval_required")
            if digest(self._current(row["bound"]["change"])) != digest(row["bound"]["baseline"]):
                row["state"] = "stale"
                raise ChangeError("preview_stale")
            if row["expires"] <= self.clock():
                raise ChangeError("preview_expired_or_unknown")
            row["state"] = "in_flight"
            try:
                result = self._writer(copy.deepcopy(row["bound"]["change"]))
                if (type(result) is not dict or set(result) != {"id"} or not valid_id(result["id"])
                        or (row["bound"]["change"]["operation"] == "update"
                            and result["id"] != row["bound"]["change"]["task_id"])):
                    raise ValueError
            except Exception:
                row["state"] = "outcome_uncertain"
                raise ChangeError("write_outcome_uncertain_no_retry") from None
            row["state"] = "applied"
            return result
