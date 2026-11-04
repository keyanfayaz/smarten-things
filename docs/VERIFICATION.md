# Verification

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

The new notes/deadline implementation has not been installed in the running
service or accepted through the private ChatGPT plugin. A native live test must
show an exact proposal and receive a fresh explicit owner reply before mutation.
Then check the stable ID, all changed/preserved fields and durable outcome.
A successful synthetic fixture is not a live Things write test.

A staged rollout must refresh the service and MCP facade together because
exact-item schema 2 adds notes/deadline. Refresh the existing private plugin's
tool catalogue and display name afterward, preserving its identity and connection.
Validate behavior separately in the intended ChatGPT clients. No automatic
retry or rollback of an uncertain task mutation is justified by these checks.
