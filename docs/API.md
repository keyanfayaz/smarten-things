# Structured read API

Default base URL: `http://127.0.0.1:8766`. Every route requires
`Authorization: Bearer <owner-only credential>`. Use a trusted backend client;
never put credentials in browser code, query parameters, shell history or logs.
The generated credential authorizes only the fixed read routes. All Origin
headers are rejected; Host must match the exact configured authority.

| Method and path | Result |
| --- | --- |
| `GET /v1/health` | Task-free process health; no permission or task-access claim |
| `GET /v1/capabilities` | Installed dictionary metadata support and limitations; no task read |
| `GET /v1/diagnostics` | Non-prompting preflight in the actual service process, plus redacted last-read status; no task read |
| `GET /v1/items/task/{id}` | One explicitly selected open task, for preview preparation |
| `GET /v1/items/project/{id}` | One explicitly selected open project, for preview preparation |
| `GET /v1/lists/today` | Open Today items |
| `GET /v1/lists/inbox` | Open Inbox items |
| `GET /v1/lists/upcoming` | Open Upcoming items if the runtime list lookup works |
| `GET /v1/projects` | Open project summaries |
| `GET /v1/projects/{id}/tasks` | Open items in one open project |

Read routes accept only `offset` (0–1,000,000, default 0) and `limit` (1–50,
default 30). Repeated/unknown parameters fail. Project IDs are 1–128 ASCII
letters, digits, hyphens or underscores. Mutation methods remain rejected for the read credential;
unknown paths return 404. Exact-item IDs use the same strict ID alphabet and
accept no query parameters or body. There is no query language, arbitrary
script execution, archived-item access through read routes. Health/capabilities
accept no parameters.

Exact-item responses contain only `schema_version: 2`, `source: things`,
`kind: task|project`, `app_version`, `time_zone`, and one `item` with `id`,
`title`, `status: open`, `project`, `scheduled_date`, `deadline`, `notes`, and `modified_at`.
The exact requested kind and ID are checked before returning data. Notes are
read only for the selected item; paginated list/project responses exclude them.
Notes are at most 8,192 characters and 16,384 UTF-8 bytes. Checklists and other
fields remain excluded. Lookup uses the same non-prompting
service-process preflight, denial latch, reader lock and bounded subprocess.
These routes do not expand the token to any mutation capability.

Example response, **fictional values only**:

```json
{
  "schema_version": 2,
  "source": "things",
  "kind": "list",
  "container_id": "TMInboxListSource",
  "container_name": "Synthetic Inbox",
  "app_version": "synthetic",
  "offset": 0,
  "limit": 1,
  "total_count": 1,
  "returned_count": 1,
  "next_offset": null,
  "has_more": false,
  "unsupported_fields": ["priority", "progress_history", "when_date", "recurrence"],
  "items": [{
    "id": "synthetic-task",
    "title": "Example task",
    "status": "open",
    "project": {"id": "synthetic-project", "title": "Example project"},
    "scheduled_date": null,
    "deadline": "2026-10-25",
    "modified_at": "2026-10-23T12:00:00Z"
  }]
}
```

`project` and all date values can be null. Scheduling means Things' public
**activation date**, not the general When value. Dates are local calendar days;
modification timestamps are UTC. See [field semantics](CONTRACT.md).

Follow `next_offset` explicitly. Never treat a partial page as the whole source.
Pages are independent reads, not a snapshot. Deduplicate by source plus ID across
lists/pages; preserve independent membership in Today and Upcoming. Do not use
project catalog rows as extra task rows. Handle a moving collection by showing
coverage and offering a refresh; do not start an unbounded rescan.

Diagnostics accepts no parameters. It returns a fixed schema: service PID,
`probe_context: service_process`, `task_read_performed: false`, denial-latch
state, preflight Automation state/reason, allowlisted failed prerequisite names,
an app-running boolean or null, and `last_read` (null or fixed stage/reason codes).
It never includes executable paths, responsible-app guesses, credentials, task
identifiers/counts/content, raw errors, or caller-supplied values. A `ready`
preflight is not proof of a successful task read. `last_read` stores only two
status strings in memory and is lost on restart; it contains no previous-process
history. A denial latch cannot be cleared or bypassed through diagnostics.
`local_consent` adds only `supported`, fixed `state`, and `attempted` fields.
During an explicit local consent attempt, diagnostics return the fixed
`local_consent_pending` preflight reason without making a second permission call.
No HTTP method, path, body or parameter can trigger consent; the local OS-owner
signal path is documented in [setup](SETUP.md).

Task-route 503 responses can additionally include a fixed `reason`, distinguishing
Automation consent/denial/unavailability, app/session prerequisites, dictionary
support, object lookup, timeouts, execution, and invalid/oversized output. Unknown
failures remain `reader_failed`; exception text is never forwarded. Diagnostic
requests use the same credential, Host/Origin checks, serialization and no-store
policy as the existing reads, and cannot request consent or launch the app.

All responses use `Cache-Control: no-store`. No task contents, request paths,
headers, raw exceptions or AppleScript errors are logged. Errors are fixed JSON:
400 invalid request, 401 no/invalid credential, 403 Host/Origin rejection,
404 unsupported read, 405 mutation rejected, 429 reader busy, 503 Things or
permission unavailable. 500/502 discard unexpected/oversized output. A 503 is
**not** an empty list. There are no automatic read retries. A new user refresh
can try again after addressing session availability; a denied service must be
restarted after manual permission authorization.

## Local stdio MCP

With the HTTP service running, start `python3 -B things_mcp.py` from a trusted
MCP client. It exposes one `read_things_page` tool with `target` = `today`,
`inbox`, `upcoming`, `projects` or `project`, optional offset/limit, and a
required `project_id` for `project`. Tool discovery/ping does not read Things.
There are no mutation, shell, arbitrary URL or resource-write tools. The client
reads the credential file itself; do not embed the secret in an MCP config.

The stdio stream necessarily carries private tool results to its authorized
caller. Do not capture it to disk or enable verbose tracing. This is a local
MCP transport, **not** an HTTP `/mcp` endpoint, OAuth server, or configured
ChatGPT plugin. See [remote paths](REMOTE.md).

`--enable-previews` additionally advertises the non-mutating
`preview_things_task_change` tool. It is not enabled by the default local plugin
or the existing tunnel configuration. Apply remains disabled. See
[preview scope and approval blocker](PREVIEWS.md).

## Separately scoped change API

These routes require the distinct write bearer and explicit local enablement:

| Method/path | Behavior |
| --- | --- |
| POST /v1/change-previews | Store one strict individual-task proposal; no mutation |
| POST /v1/change-previews/{proposal_id}/apply | Apply the unchanged reviewed proposal once |
| GET /v1/change-previews/{proposal_id} | Inspect durable state/result; never retries |

POST bodies require application/json, one Content-Length of 1–65536 bytes,
no transfer encoding, duplicate JSON keys, unknown fields or non-finite values.
Preview accepts operation=create|update, title, notes, start_date, deadline, and task_id for
updates or optional project_id for creation. Apply accepts exactly payload_digest
and the unchanged change object. There is no approval boolean. The trusted
assistant/host must obtain a fresh owner reply before submitting apply.

`notes` is the task description. Multiline Unicode text is preserved; an empty
string clears it. `deadline` is a local YYYY-MM-DD due day; null clears it.
`start_date` is a separate local calendar start day, and cannot be cleared by
this interface. Omitted fields are preserved on updates. Neither date field accepts
natural-language or datetime strings. Updates require at least one changed field.
Exact-item schema 1 clients must be updated alongside the service before using
these expanded previews. Old pending proposals fail the fresh-baseline check.

The read bearer cannot use these routes. GET /v1/write-capability uses the read
bearer and returns only task-free enablement metadata; it cannot enable writes.
All routes remain loopback-only with exact Host checks, rejected Origins,
no-store and no raw request/error logging. See [the complete contract](PREVIEWS.md).

The explicit production facade uses `--write-token-file <private-path>` and adds
preview, apply and outcome-status tools under the approved host-mediated consent
model. This differs from legacy `--enable-previews`, which remains preview-only.
See [PREVIEWS.md](PREVIEWS.md) for lifecycle, exact scope and uncertainty handling.
