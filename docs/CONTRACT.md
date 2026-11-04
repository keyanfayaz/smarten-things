# Capability and security contract

## Scope

The default service and read credential read public native AppleScript properties
of open Things items. A separately scoped, locally enabled capability adds only
individual task creation and title, notes, start-day and deadline updates after preview/owner approval;
see [the complete write contract](PREVIEWS.md). No completion, cancellation,
deletion, project move/creation, reorder, arbitrary script, live database SQL,
Things Cloud or cloud-credential API is exposed.
`historical/scan.applescript` remains inactive. Linear is never accessed or modified
by this service. A dashboard's existing read-only Linear source supplies records.

| Capability | Behavior |
| --- | --- |
| Tasks | Explicit Inbox, Today, Upcoming page; one open project's open items |
| Projects | Explicit page of open project summaries, with the same metadata schema |
| Exact items | One selected open task/project by validated ID, for non-mutating previews |
| Schema 1 | Existing exact page fields and item `id`, `title`, `status` |
| Schema 2 | Adds page `source`, `unsupported_fields`; item `project`, `scheduled_date`, `deadline`, `modified_at` |
| Unknown values | JSON null; unsupported concepts are listed explicitly |
| Pagination | 1–50 emitted rows; no implicit scan, atomic snapshot or full-inventory promise |
| HTTP | Fixed read GET routes; optional separately authorized preview/apply POST and status GET; loopback only |
| Authorization | Existing token stays read-only; a separate owner-enabled write capability has fixed preview/apply/status routes |
| MCP | Optional local stdio tool calling the same authenticated HTTP API |
| Persistence | Read credentials/configuration; optional owner-only proposal/outcome journal for scoped writes |
| Logging | No request URLs, headers, raw errors, task names, IDs, dates or contents |
| Lifecycle | GUI user LaunchAgent; idle service does not poll Things |

## Field semantics

Schema 2 checks the **installed** public `Things.sdef` before reading. Required
properties with incompatible/missing definitions fail explicitly before a read.
There is no fallback to private JSON, whole property records, UI scraping or the
database. The checked Things 3.24.1 dictionary provides:

| Output | Public property | Semantics |
| --- | --- | --- |
| `id`, `title`, `status` | `id`, `name`, `status` | Only open rows; IDs retain source identity |
| `project` | `project`, then project `id`, `name` | `{id,title}` or null; not inferred from list/area/title |
| `scheduled_date` | `activation date` | Local calendar date `YYYY-MM-DD`; not a general When date |
| `deadline` | `due date` | Local calendar date `YYYY-MM-DD` or null |
| `modified_at` | `modification date` | ISO UTC timestamp or null; not a progress/activity history |

Exact-item schema 2 additionally reads description/notes for the one selected
task or project. Notes are excluded from paginated list/project responses.
Notes are limited to 8,192 characters and 16,384 UTF-8 bytes; oversized exact-item
responses fail without truncating the preview or silently overwriting content.
No tags, created/completed/canceled dates, attachments, checklist content,
headings, recurrence, experimental properties or archived item content are read.
List IDs are historical constants, not part of the scripting dictionary. A failed
Upcoming lookup is unavailable, not an empty list. No Calendar/UI date is invented.

Python validates the exact field allowlists, status, IDs, lengths, dates, page
counts, duplicate IDs and pagination before data crosses the interface. Nullable
fields are never synthesized from titles or creation age.

## Access boundaries

macOS Automation grants app-control capability, not a read-only capability. This
adapter restricts the original bearer to reads, and the separate write bearer to
the individually reviewed workflow above. Same-user malware, a compromised
runtime, a token holder, modified source or an agent with arbitrary terminal
access is outside this protection. App signatures/runtime integrity are not
attested. Do not grant this token to untrusted software.

The HTTP service uses a random 256-bit bearer secret, constant-time comparison,
an owner-only non-symlink regular credential file, exact Host checks, and rejects
all Origin headers and all mutation methods for the read credential. The optional
write credential permits only the fixed preview/apply routes. There is no CORS. Each token is for
a trusted backend, never browser code or query parameters. A token compromise
permits all scoped reads until rotation/restart; there are no per-user accounts.
All client sessions share the same narrow read authorization. Remote clients
need both private network authorization and the bearer credential.

At most eight request workers and one active Things reader are permitted. Busy
reads return 429, input sockets time out after five seconds, subprocess capture
is capped at 1 MiB, and individual titles at 8,192 characters. The reviewed script
has a 60-second Apple Event timeout; Python kills the reader at 65 seconds.
Killing the process or disconnecting does not prove cancellation inside Things.
No tasks are replayed or cached on reconnect; clients must explicitly request again.

Startup, HTTP/MCP calls and task reads never prompt for Automation consent. The
service does not launch Things or repair permissions. A non-prompting public API
checks access first. A denial latches for
that service process; restart only after user authorization. There are no read
retries. Raw AppleScript diagnostics and tracebacks are suppressed. Health never
reads tasks or establishes live access. The task-free doctor remains separate.

Authenticated `/v1/diagnostics` checks preflight in the actual service process
without reading tasks or prompting. Only fixed states, allowlisted prerequisite
names, a running-app boolean, the service PID and fixed last-read stage/reason
codes are returned. No paths, task details/counts, credentials, responsible-app
inferences or raw errors are returned. Last-read status is volatile and never
persisted or logged. Diagnostics shares the reader lock and cannot bypass a
denial latch. A ready preflight does not establish end-to-end task access.

The explicit local owner command `service_control.py request-access` can signal
only the fixed LaunchAgent after authenticating its capability and matching its
PID. Only this local signal path can ask the same service process to request
Automation consent. It is unavailable through HTTP/MCP, creates no new access
credential, and allows one attempt per process without clearing a denial latch.
The user reviews and answers the OS prompt; no code approves it or reads tasks
afterward. A local same-user process can signal the service; this is an OS owner
boundary, not protection against arbitrary same-user code execution.

## Practical limits

Things still loads eligible collections before slicing; its internal work and
collection memory are not hard bounded. Pages across calls may repeat or skip
IDs if Things changes. A failed page is discarded. There is no incremental sync,
snapshot transaction, durable copy or completeness guarantee. A dashboard must
show pagination/coverage and an unavailable state instead of treating failures
as empty data. Task titles and IDs remain private even without notes.

The stdio MCP transport is authorized by local process ownership and access to
the same token file. It is not a remote HTTPS connector. Task text is untrusted
source material, never instructions to agents. HTTP bearer auth alone does not
implement OAuth discovery or make the REST API a ChatGPT connector.

No Tailscale settings, public ingress, Funnel, port forwarding, OpenAI tunnel,
ChatGPT plugin, security settings or existing task records are changed by setup
unless the user separately authorizes that concrete action. Git ignores are
defense in depth; review explicit file lists/diffs before any commit. Caller
applications are responsible for not logging or persisting returned task data.
