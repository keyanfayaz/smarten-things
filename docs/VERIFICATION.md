# Verification

## Bounded authorization candidate — 2026-11-15

The local native authorization check stalled before any item request. Both the
Python binding and a small native C control using Apple's SDK declarations
stalled inside `AEDeterminePermissionToAutomateTarget` with prompting disabled.
Bundle-ID and verified-process-ID addressing, and a worker with a main run loop,
did not produce an authorization result within their diagnostic deadlines.
App discovery found one fully launched standard Things instance; session metadata
confirmed a logged-in, on-console session but did not establish screen-lock state.
This does not establish the cause of the earlier item-event
timeouts or project placement failure.

The source now gives non-prompting authorization checks a five-second caller
deadline in the same process. One outstanding daemon probe is permitted, without
automatic retry or queuing behind a stall. Late grants are discarded; late
denials still latch for their event type, preserving reads after a write denial.
The binding also matches the SDK's two-byte-packed `AEDesc` layout.

All 161 synthetic tests passed. In a separate read-only loopback instance,
diagnostics returned `automation_check_timed_out` in 5.013 seconds without reading
tasks. Health still responded in 0.001 seconds with writes disabled, and the
exact QA-project request failed closed with 503 in 0.001 seconds. No native task
read or mutation was attempted because authorization remained unknown. Native
create/edit/move/replay/reconciliation acceptance is still untested. The existing
private database/WAL backup hashes matched, and a reconstructed private copy
passed integrity checks before diagnostics. No runtime or production journal
was changed, and no branch was pushed.

## Recovery candidate — 2026-11-14

The 2.3.0 candidate adds exact-ID task moves and known-ID reconciliation through
the existing separately authorized preview/apply/status workflow. The local
synthetic suite passes 154 tests, including the real MCP/loopback HTTP boundary,
old-journal migration, stale task/destination/context checks, replay, wrong
destination, partial failure, atomic reconciliation failure, restart, and multiple
unresolved records. Reconciliation preserves the original uncertain row and never
calls the native writer. All three fixed AppleScripts compile against the
installed Things dictionary, whose project property supports assignment.

Task-free AppleScript/Foundation fixtures now exercise the production project
snapshot and move branches, replacing application calls with synthetic records.
They do not establish native Things placement. Exact-item reads timed out during
separate native investigation, so live task mutation acceptance remains blocked.
The original project-create placement implementation is unchanged; its documented
syntax is not evidence of the failure's cause. This candidate has not been deployed.

Before release, restore reliable native exact-item reads without permission
resets or hidden retries, then use only an isolated test area/project and its
known synthetic task IDs. Verify project create, project move, title/notes/both
dates and preserved fields through exact preview/approval/apply/read-back. Exercise
a deliberately uncertain synthetic record, verify the create block, perform a
separately approved correction, and reconcile its exact known ID once. Confirm
the original uncertain record survives and repeat apply cannot mutate again.
Live recovery of an existing personal task is a separate owner-approved action.

## Deployment checkpoint — 2026-11-04

Source commit `463569b` passed both GitHub Actions jobs:
[portable suite and macOS native fixtures](https://github.com/keyanfayaz/smarten-things/actions/runs/41854377988).
The local run passed 133 tests. The working source and all 13 installed runtime
files matched after deployment.

A fresh complete native Things bundle was copied while Things was closed,
following the vendor's backup procedure. A separate offline SQLite backup of
that copy passed `integrity_check`. No task rows or live database were queried,
and no restore drill was performed. The prior runtime, LaunchAgent and tunnel
profiles were retained in a private rollback directory outside Git.

The existing service was restarted with its LaunchAgent definition and both
credentials unchanged. Writes started disabled and were explicitly enabled for
the reviewed process. The existing managed tunnel was restarted with the same
tunnel ID, runtime key reference and profile configuration. Its status reported
running, healthy and ready; a successful cloud tool call separately verified
the transport.

Observed live checks:

- A bounded HTTP Inbox read passed without printing task content.
- A real ChatGPT plugin Inbox read returned one schema-2 item successfully.
- The updated exact-item reader returned schema 2 with notes and deadline for
  one currently open Inbox item; only pass/schema flags were printed.
- The historical integration-test task ID was unavailable. It was not retried,
  retargeted or modified, and its current state was not inferred.
- Installed stdio discovery advertised server version 2.2, the same four tools,
  and notes/deadline inputs in both preview and apply.
- The existing hosted plugin was renamed **My Things Connection**, its stale
  read-only description was corrected, and its tools were refreshed. The hosted
  catalogue visibly showed one write tool and three read tools, including the
  updated preview description mentioning notes and deadlines.
- The hosted App/plugin identity, connection and existing permission preference
  were retained. No Things task was created or modified by this deployment.

The active chat's callable schema can retain earlier metadata. A fresh
conversation is required for acceptance of the refreshed inputs. Another-device
acceptance and real notes/start-day/deadline writes remain unverified; each live
mutation still requires the existing exact-preview/fresh-owner-approval flow.

## November 2026 source recovery and field extension

The GitHub baseline at `84c2ccb` contained only the original reader and 19 tests.
The later authenticated service, MCP facade, local packaging and individual
preview/apply/status implementation existed in a local development checkout.
Before the extension, all 13 installed Python/AppleScript runtime files matched
that recovered source byte for byte. The existing 118 tests passed locally.

The notes/deadline extension is verified by synthetic tests using mocked Things
records, ephemeral authenticated loopback HTTP, stdio discovery and task-free
AppleScript/Foundation fixtures. Native fixtures run the production field logic
with the Things application boundary replaced by local synthetic records.
No fixture sends a Things Apple Event or reads a live library.

The final local run passed **133 tests**. Python syntax checks, all three native
script compilations and the local plugin build passed.

Coverage includes:

- Creation and updates with multiline Unicode notes, start day and deadline.
- Empty notes, explicit null deadlines and preservation of omitted fields.
- Invalid dates/text/size/encoding and unexpected-field rejection.
- Binding notes and deadlines to the preview, digest and stale-state checks.
- Exact-ID read-back and uncertain outcomes when fields do not match.
- MCP preview/apply/status through the real synthetic HTTP service.
- Expiry, replay, concurrency, principal isolation and durable crash recovery.
- Separate read/write credentials, startup-disabled writes and permission gates.
- Default read-only discovery and relocated plugin packaging.

The native read and write scripts compile against the installed Things public
scripting dictionary. Compilation is task-free. The restricted execution sandbox
can report misleading AppleScript terminology errors; compilation in the intended
macOS user context succeeds without executing the script.

GitHub Actions runs the complete synthetic suite on Linux, plus native synthetic
fixtures on macOS. These jobs do not have access to a Things library or credentials.
See the commit's actual Actions result for remote status; local results alone do
not establish that remote CI passed.

## Earlier live acceptance, October 2026

Earlier private verification recorded one successful Inbox task creation via the
private ChatGPT plugin: exact preview, fresh owner approval from iOS, one Apply,
exact-ID read-back and a separate durable status check. This establishes the
accepted assistant/host-mediated approval model for that creation only.
Private task/proposal identifiers, titles, process IDs and backup paths are
intentionally omitted from the public source record.

The installed private plugin advertised read, preview, apply and status tools.
The existing local companion package advertised read only. These are separate
connections, and a source edit or Git push does not update either cached install.

## Remaining live checks

The service and private-plugin metadata were updated at the deployment
checkpoint above. A native live write test must
show an exact proposal and receive a fresh explicit owner reply before mutation.
Then check the stable ID, all changed/preserved fields and durable outcome.
A successful synthetic fixture is not a live Things write test.

A future update must refresh the service and MCP facade together because
exact-item schema 2 adds notes/deadline. Refresh the existing private plugin's
tool catalogue after server metadata changes, preserving its identity and connection.
Validate refreshed behavior separately in the intended ChatGPT clients. No automatic
retry or rollback of an uncertain task mutation is justified by these checks.
