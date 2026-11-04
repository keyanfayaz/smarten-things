# Source audit and staged rollout

## Findings

The repository is `keyanfayaz/things-applescript`. GitHub's original `84c2ccb`
baseline lagged behind the local implementation. The recovered code matches all
13 source files installed in the service runtime before the new field changes.
The original reader had 19 tests; the recovered service and write workflow had
118 passing synthetic tests.

The connected private ChatGPT plugin already exposed task creation, title and
literal start-day updates, plus preview/apply/status. Its “Read Only” display
name and description were stale. Prior live evidence covers one task creation;
it does not establish live date or title updates.

Descriptions in Things are the public AppleScript `notes` property. A deadline
is the public `due date` property; start-day scheduling uses `schedule ... for`.
The upstream documentation and installed scripting dictionary support these
operations. The missing description/deadline behavior was an integration scope
limit, rather than an unavailable Things API.

Source: [Things AppleScript commands](https://culturedcode.com/things/support/articles/4562654/).

## Implemented source changes

- Strict notes/deadline input validation, including multiline Unicode text.
- Creation and updates with notes, start day and deadline as separate fields.
- Empty notes clears notes; null deadline clears the deadline; omitted update
  fields are preserved. Clearing a start day remains unsupported.
- Exact-item schema 2 includes notes/deadline for complete preview, stale-state
  comparison and read-back. Paginated reads still exclude notes.
- The fixed native writer verifies all selected fields before mutation and the
  coordinator verifies changed and preserved fields afterward.
- The explicit write MCP catalogue advertises the new inputs. Existing default
  read-only discovery and separate write authorization remain intact.
- Local package display name becomes **My Things Connection**, version 2.2.0.
  The package's internal identifier is preserved. Its default launcher remains
  read only; the existing private cloud connection has separate configuration.
- GitHub CI covers portable tests and task-free native fixtures on macOS.

## Release sequence

1. Verify source, native compilation, synthetic integration and package output.
   Commit and push the recovered implementation and new fields to GitHub.
2. On the existing Things host, preserve a current backup and inspect pending or
   uncertain operations. Deploy the reviewed service, native scripts and MCP
   facade together, retaining credentials and the existing tunnel association.
   Restart leaves writes disabled; enable only the reviewed running process.
3. Perform a deliberate native live acceptance test using an exact preview and
   a fresh owner reply before each mutation. Verify notes, start day, deadline,
   untouched fields, stable ID and durable outcome. Do not retry uncertainty.
4. Refresh the existing private ChatGPT plugin's tool catalogue and rename its
   display title to **My Things Connection**. Keep its plugin/App identity,
   tunnel, audience and write-confirmation preference. The local package builder
   does not own that account-level display title or cached cloud tools.
5. Verify discovery and one approved end-to-end change from the intended ChatGPT
   clients, including a second device. Client refresh behavior must be observed;
   a Git push alone does not establish device rollout.

The November 4 deployment installed the coordinated runtime and restarted the
existing tunnel, then renamed and refreshed the same hosted plugin. See the
[deployment checkpoint](VERIFICATION.md) for observed read/discovery evidence.
Real notes/date mutations and cross-device acceptance remain pending. The
exact-item schema upgrade requires coordinated service/client rollout; old
previews must be regenerated. The system still does not support task
completion/deletion, bulk changes, recurrence, checklists or project moves
through its write tools.
