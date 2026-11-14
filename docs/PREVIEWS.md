# Individual Things task changes

The optional workflow is exact preview → fresh owner reply in the authenticated
chat → Apply. The assistant and ChatGPT host are trusted to obtain that per-item
consent. This is not independent cryptographic proof of a human reply. No
`approved: true` field is required or accepted. Prior blanket tool approval and
Things text are never consent. Keep the plugin set to ask before writes and use
one-time host approvals.

## Scope

Create one open task with a title, optional description/notes, literal start day,
deadline and existing open project ID; otherwise use Inbox. Update the title,
notes, start day and/or deadline of one selected open task by exact ID.
An empty notes string clears notes; a null deadline clears the deadline.
Omitted fields are preserved. Notes preserve multiline Unicode text and are
bounded to 8,192 characters and 16,384 UTF-8 bytes. Exact-item reads use schema 2
and include notes/deadline so previews and stale checks bind all reviewed fields.
No deletion, completion, cancellation, bulk import, project creation/move, tags,
recurrence editing, calendars or automatic startup is implemented. Do not select
repeating task templates: the public reader cannot inspect recurrence and that
use is unverified. Start days are literal YYYY-MM-DD calendar days in the Mac's
local time zone, not UTC instants. Start day and deadline are distinct.
Clearing a start day is unsupported.

## Local authorization and lifecycle

The existing `token` remains read-only. A separate owner-only `write-token`
authorizes fixed preview/apply/status routes. No secret is printed, placed in
arguments/environment, packaged, or sent to ChatGPT; only its file path is used.
Read and write tokens are checked separately, and neither substitutes for the
other. Loopback binding, Host checks, Origin rejection and no-store remain.

Every service process starts with writes disabled, even when the write file
exists. Only `service_control.py enable-writes` can queue activation by a local
owner signal to the verified existing service PID. No HTTP/MCP method activates
writes. Restart disables them. The existing read LaunchAgent's login behavior
is unchanged; no write autostart is added. Task-free GET /v1/write-capability
reports actual enablement. A sent signal alone is not proof of activation.

After an explicitly reviewed deployment, initialize the separate private file
using the existing credential helper, then enable the current process locally.
The tunnel facade receives `--write-token-file <private-path>` to advertise
read_things_page, preview_things_task_change, apply_things_task_change, and
get_things_change_status. Without that option its default catalogue stays one
read tool. Legacy --enable-previews remains non-mutating. Neither the existing
OpenAI Read + Use key nor the tunnel association changes.

## Durable preview and apply

A private SQLite journal stores the selected canonical change, exact baseline,
app version, Mac zone, digest and a 24-hour review expiry. At most 32 pending
proposals are allowed. The same proposal ID and digest remain valid while the
owner reviews it, including when they reply hours later. The owner must still
approve the exact preview after it is shown; elapsed time never grants consent.
There is no automatic refresh or approval transfer: expired proposals require a
new preview and new explicit approval. Existing five-minute proposals retain
their original expiry after an upgrade. Status reports expiry accurately and
removes expired pending payloads without executing a Things change.

Apply echoes the proposal ID, digest and unchanged change
object so the reviewed values are visible in host confirmation. Changed,
expired, stale, retargeted and replayed requests are rejected. State and zone
are rechecked under the read service's shared lock, and the fixed native script
checks the selected item again before its first mutation.

The journal commits an in-flight claim before native execution. An exclusive
process lock and atomic claim prevent competing applies. Only documented make,
title/notes/due-date assignments and schedule commands are used; activation date is read-only.
A verified exact-ID read-back precedes recording success. Status inspection
never repeats the operation.

Native Apple Events are not a transaction. A timeout, crash, invalid result or
failed read-back can leave a partial/completed change. The journal records an
uncertain outcome and any known ID; no automatic retry or rollback occurs.
Startup converts interrupted claims to uncertain. New creations stay blocked
while an unresolved operation exists; owner investigation/reconciliation is
required before reopening creation. No title matching or duplicate create is
used to guess an outcome.

## Privacy and prerequisites

The journal contains selected proposals/results, not a full database. Its
directory is 700 and files 600. Expired pending payloads are removed; outcome
records support inspection and unresolved records remain for reconciliation.
It is never logged or uploaded. Private temporary request files are removed
after execution, keeping titles and secrets out of process arguments. HTTP
paths, bodies, headers, raw errors and task contents are not logged.

Things must be running. Read/write permission checks never request OS UI. A
write denial latches writes without disabling a still-valid read grant. A fresh
verified native backup precedes deployment; integrity checks are not an actual
restore drill. Deployment, health and synthetic tests do not authorize a live
mutation. Each exact preview still needs separate owner approval. Creation through the
private ChatGPT plugin and explicit owner approval from iOS were verified on
2026-10-29; live title/start-date updates and iMessage remain unverified. See
[VERIFICATION.md](VERIFICATION.md) for evidence and remaining acceptance checks.

References: [Things AppleScript](https://culturedcode.com/things/support/articles/4562654/),
[ChatGPT confirmations](https://developers.openai.com/api/docs/guides/custom-mcp-server),
[OpenAI server guidance](https://developers.openai.com/plugins/build/mcp-server).
