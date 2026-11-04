"""Backend-only Things client and deterministic review; never persists task data."""
from datetime import date, datetime, timedelta, timezone
import http.client
import json
import re
from urllib.parse import urlsplit

import things_readonly as adapter
from things_service import load_token, page_arguments


class ThingsClient:
    def __init__(self, token_file, base_url="http://127.0.0.1:8766"):
        url = urlsplit(base_url)
        if (url.username or url.password or url.path not in {"", "/"} or url.query or url.fragment
                or not url.hostname or url.scheme not in {"http", "https"}):
            raise ValueError("Invalid Things backend URL")
        if url.scheme == "http" and url.hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError("Remote Things access requires HTTPS")
        self.url = url
        self.token_file = token_file

    def get(self, path):
        # Reject arbitrary URLs/paths before sending the credential anywhere.
        url = urlsplit(path)
        if url.scheme or url.netloc or url.fragment:
            raise ValueError("Unsupported read")
        args = None
        item_match = re.fullmatch(r"/v1/items/(task|project)/([A-Za-z0-9_-]{1,128})", path)
        if path not in {"/v1/health", "/v1/capabilities", "/v1/diagnostics", "/v1/write-capability"}:
            if not item_match:
                args = page_arguments(url.path, url.query)
        token = load_token(self.token_file)
        cls = http.client.HTTPSConnection if self.url.scheme == "https" else http.client.HTTPConnection
        connection = cls(self.url.hostname, self.url.port, timeout=75)
        try:
            # http.client does not follow redirects; no ambient proxy environment.
            connection.request("GET", path, headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
            response = connection.getresponse()
            body = response.read(adapter.MAX_OUTPUT + 1)
            if response.status != 200 or len(body) > adapter.MAX_OUTPUT:
                raise adapter.AdapterError("Things backend unavailable; response suppressed")
            page = json.loads(body)
            if item_match:
                return adapter.validate_item(page, *item_match.groups())
            if args:
                kind, container = adapter.target_for(args)
                return adapter.validate_page(page, kind, container, args.offset, args.limit, True)
            return page
        except (OSError, ValueError, http.client.HTTPException):
            raise adapter.AdapterError("Things backend unavailable; diagnostic suppressed") from None
        finally:
            connection.close()

    def lookup(self, kind, item_id):
        if kind not in {"task", "project"} or type(item_id) is not str or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", item_id) is None:
            raise ValueError("Invalid exact-item request")
        return self.get(f"/v1/items/{kind}/{item_id}")


def things_tasks(pages):
    """Normalize pages already fetched by the caller. No automatic inventory crawl."""
    result = []
    for page in pages:
        if page["kind"] == "projects":
            continue  # Project summaries never become duplicate tasks.
        membership = next((key for key, value in adapter.LIST_IDS.items() if value == page["container_id"]), None)
        for row in page["items"]:
            result.append({"source": "things", "id": row["id"], "title": row["title"], "status": row["status"],
                "project": row["project"], "deadline": row["deadline"], "scheduled_date": row["scheduled_date"],
                "modified_at": row["modified_at"], "memberships": [membership] if membership else [],
                "priority": None, "url": "things:///show?id=" + row["id"]})
    return result


def linear_task(issue):
    """Normalize a Linear issue fetched by an existing read-only dashboard source."""
    state = issue.get("state") or {}
    return {"source": "linear", "id": issue["id"], "title": issue["title"],
            "status": "closed" if state.get("type") in {"completed", "canceled"} else "open",
            "activity_state": state.get("type"), "project": issue.get("project"),
            "deadline": issue.get("dueDate"), "scheduled_date": None,
            "modified_at": issue.get("updatedAt"), "memberships": [],
            "priority": issue.get("priority"), "url": issue.get("url")}


def combine_tasks(tasks, aliases=None):
    """Source-qualified identity, with optional reviewed cross-source aliases.

    aliases = {"linear:<id>": "things:<id>"}; only one-hop Things/Linear links.
    Never merge by title. Both source records survive an explicit linked merge.
    """
    aliases = aliases or {}
    by_key = {}
    for task in tasks:
        if task["source"] not in {"things", "linear"} or task["status"] != "open":
            continue
        key = task["source"] + ":" + task["id"]
        if key in by_key:
            previous = by_key[key]
            memberships = sorted(set(previous.get("memberships", [])) | set(task.get("memberships", [])))
            # Independent pages can race. Prefer newer known metadata, and never
            # make inactivity claims from conflicting/partly unknown snapshots.
            old_time, new_time = _timestamp(previous.get("modified_at")), _timestamp(task.get("modified_at"))
            changed = any(previous.get(field) != task.get(field) for field in
                          ("title", "deadline", "scheduled_date", "modified_at", "project"))
            preferred = dict(task) if new_time and (not old_time or new_time > old_time) else dict(previous)
            preferred["memberships"] = memberships
            preferred["activity_inconsistent"] = previous.get("activity_inconsistent", False) or changed
            by_key[key] = preferred
        else:
            by_key[key] = dict(task)
    for alias, canonical in aliases.items():
        if not alias.startswith("linear:") or not canonical.startswith("things:") or alias == canonical:
            raise ValueError("Aliases must map a Linear ID to a Things ID")
    groups = {}
    for key, task in by_key.items():
        canonical = aliases.get(key, key)
        # If an explicitly canonical source is absent, keep its identity and link.
        group = groups.setdefault(canonical, {"key": canonical, "records": []})
        group["records"].append(task)
    return list(groups.values())


def _day(value):
    try:
        return date.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def _timestamp(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo else None
    except (AttributeError, TypeError, ValueError):
        return None


def daily_review(groups, today=None, now=None, important_keys=()):
    """Candidate advice only: no writes, inferred priority or progress claims."""
    today = today or date.today()
    now = now or datetime.now(timezone.utc)
    week_end = today + timedelta(days=6 - today.weekday())
    priority = []
    overdue, attention, this_week, needs_review, activity_unknown = [], [], [], [], []
    for group in groups:
        records = group["records"]
        deadlines = [value for row in records if (value := _day(row.get("deadline")))]
        scheduled = [value for row in records if (value := _day(row.get("scheduled_date")))]
        deadline = min(deadlines, default=None)
        important = group["key"] in important_keys or any(row["source"] == "linear" and row.get("priority") in (1, 2) for row in records)
        is_today = any("today" in row.get("memberships", []) for row in records)
        is_overdue = deadline is not None and deadline < today
        reasons = []
        if is_overdue:
            reasons.append("deadline_overdue")
        elif deadline == today:
            reasons.append("deadline_today")
        if important:
            reasons.append("explicit_importance")
        if is_today:
            reasons.append("things_today_membership")
        entry = {**group, "reasons": reasons}
        if is_overdue:
            overdue.append(entry)
        if important and (is_today or deadline is not None and deadline <= today):
            attention.append(entry)
        if reasons:
            priority.append(((not is_overdue, deadline != today, not important, not is_today, deadline or date.max, group["key"]), entry))
        week_reasons = []
        if deadline and today <= deadline <= week_end:
            week_reasons.append("deadline_this_week")
        if any(today <= day <= week_end for day in scheduled):
            week_reasons.append("activation_this_week")
        if important and not scheduled and not is_today:
            week_reasons.append("important_unscheduled_review")
        if week_reasons:
            this_week.append({**group, "reasons": week_reasons})
        updates = [_timestamp(row.get("modified_at")) for row in records]
        # A missing activity timestamp on either linked record prevents a stale claim.
        if any(value is None for value in updates) or any(row.get("activity_inconsistent") for row in records):
            activity_unknown.append(group["key"])
            continue
        latest = max(updates)
        threshold = 14 if any(row.get("activity_state") == "started" for row in records) else 30
        if (now - latest >= timedelta(days=threshold) and not any(day > today for day in scheduled)
                and not (deadline and deadline > today)):
            needs_review.append({**group, "reasons": ["no_recorded_modification"],
                                 "inactive_days": (now - latest).days, "threshold_days": threshold})
    return {"today": {"top_3_candidates": [entry for _, entry in sorted(priority, key=lambda item: item[0])[:3]],
                      "overdue": overdue, "immediate_attention": attention},
            "this_week": this_week, "needs_review": needs_review,
            "activity_unknown": activity_unknown,
            "project_progress": None,
            "limitations": ["Review candidates are advice, not accepted priorities or task changes.",
                            "Modification is not evidence of meaningful progress; project progress is unsupported.",
                            "Coverage is limited to the pages supplied by the caller."]}
