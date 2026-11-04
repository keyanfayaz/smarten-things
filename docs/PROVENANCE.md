# Source provenance

The original scan is retained in `historical/scan.applescript`. Its complete
source block was recovered from retained execution output after the temporary
original file was no longer available. Only the script text was extracted;
no task output, diagnostics containing task content, or private workflow
preferences are included. The trailing newline was normalized. An original
on-disk byte hash is unavailable, so byte-for-byte identity is not claimed.

This repository begins with an independent source snapshot. Private development
records and internal execution identifiers are outside its published history.
No unavailable script was invented.

## Original versus active adapter

The original scan reads area/project summaries and at most 30 Inbox/Today titles.
Its project loop does not filter closed project metadata. It is preserved for
provenance and is not invoked by the CLI or tests. **Do not run the historical
scan as the active adapter.**

`src/read.applescript` and `things_readonly.py` are newly written derivatives:
they retain the original native public-read approach and Foundation JSON encoding,
restrict reads to open projects and open items, add explicit pagination, validate
fields, enforce a process timeout, and suppress raw diagnostics. Later local development added public metadata, authenticated HTTP/MCP, scoped
preview/apply/status operations and local packaging. That source was recovered
into version control in November 2026. No task output, credential or journal
is included. The description/notes and deadline extension preserves this
public AppleScript approach; it does not read the database or private properties.

Earlier development recorded successful snapshots followed by app/object
resolution errors `-1728` and Launch Services error `-10827`. These errors are
not established permission denials, and older snapshots do not prove that the
active adapter works live in a new execution context. Live task access remains
unverified; this publication performs no live task reads.

The installed Things scripting dictionary was inspected as the local source
for public property and collection names. No dictionary or live database is
included. See the capability and security contract for the supported interface
and its limits.
