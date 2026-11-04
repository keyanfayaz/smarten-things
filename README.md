# My Things Connection

A small macOS Things 3 reader, authenticated loopback HTTP service, backend
dashboard client, and optional local stdio MCP facade. Python 3.10+ and the
standard library only. The existing AppleScript implementation is extended;
the historical scan remains inactive.

Start with [setup](docs/SETUP.md), the [security contract](docs/CONTRACT.md),
[HTTP API](docs/API.md), and [dashboard integration](docs/DASHBOARD.md).
See [remote access and ChatGPT](docs/REMOTE.md) for the distinct network paths.
For an installable local companion plugin, see [plugin setup](docs/PLUGIN.md).
See [the source audit and staged rollout](docs/ROLLOUT.md) for recovered work,
new field support and the remaining live acceptance steps.

The optional [individual task-change workflow](docs/PREVIEWS.md) supports task
creation and title, description/notes, start-day and deadline updates after exact preview and explicit per-item
owner approval in chat. It trusts the authenticated assistant/host to obtain
that approval; it does not independently prove a human reply. A separate local
write credential and a local owner enable action are required. Writes start
disabled on every service start/restart. The default MCP and local companion
plugin still advertise only `read_things_page`; existing read credentials never
authorize a mutation. No live change is implied by deployment or passing tests.

## CLI

```sh
python3 -B -m unittest discover -s tests -v
python3 -B things_readonly.py --doctor
# Following commands return private task data. Never paste their output into logs/Git/CI.
python3 -B things_readonly.py --list today --limit 10
python3 -B things_readonly.py --list inbox --metadata --limit 10
python3 -B things_readonly.py --list upcoming --metadata --limit 10
python3 -B things_readonly.py --projects --metadata --limit 10
python3 -B things_readonly.py --project PROJECT_ID --metadata --limit 10
```

Schema 1 remains the default CLI contract. `--metadata` selects schema 2; the HTTP
service always uses schema 2. Pages contain at most 50 open items (default 30).
Offsets are 0–1,000,000. There is no implicit crawl or export. Optional INI settings
remain supported; see `config.example.ini`. No arguments prints usage and fails.

The task-free doctor checks disk metadata and app discovery. A successful doctor
does **not** establish Automation permission or working reads. Things must be
open in the logged-in desktop session. [Automation steps](docs/SETUP.md) explain
the permission boundary; no security settings are reset or repaired.

## Local service

```sh
# Creates only a random owner-only credential; never prints it or overwrites one.
python3 -B things_service.py --init-token
python3 -B things_service.py
```

The service binds only `127.0.0.1:8766`; all routes require bearer authentication.
Fixed GET reads use the original read credential. Optional task changes require
the separate locally enabled write capability. No browser access/CORS,
database, exports, background scans, or task logs. Credentials stay in
`~/Library/Application Support/ThingsReadOnly/token` (600, parent 700), outside Git.
Use the backend client rather than putting a token in browser JavaScript.

Optional login startup: `python3 -B service_control.py install`. This writes a
user LaunchAgent and starts it in the current GUI domain. It never opens Things
or requests Automation permission. [Startup/shutdown details](docs/SETUP.md).

## Integration status

The backend client and deterministic daily review helper are ready to integrate.
They accept Things pages and separately supplied Linear records, preserve source
identity, deduplicate by ID, and never merge equal titles. Explicit cross-source
links can combine known duplicates without changing either source. Modification
data supports **review candidates**, not claims of meaningful project progress.

`things_mcp.py` is an optional stdio MCP server that reads through the authenticated
HTTP service. It does not create an HTTP `/mcp` endpoint or register itself with
any application. Local readiness does not mean ChatGPT is connected. Tailscale
Serve and any OpenAI Secure MCP Tunnel require separate configuration; no tunnel
or public exposure is enabled by this repository.

`python3 -B build_plugin.py` builds a local marketplace in `dist/things-local`.
It bundles only reviewed code, documentation, read-only plugin metadata and a
stdio launcher. It never copies the credential or private data, registers or
enables the plugin, requests Automation permission, or calls Things. The package
can be installed by a supported local desktop client after source registration;
the hosted ChatGPT connection remains separate. See [plugin setup](docs/PLUGIN.md).

## Verification and limits

```sh
python3 -B -m unittest discover -s tests -v
python3 -m py_compile things_readonly.py things_service.py things_access.py dashboard_client.py things_mcp.py service_control.py verify_live.py
osacompile -o /tmp/things-readonly.scpt src/read.applescript
# Only after Things is open and Automation is authorized; prints no task content:
python3 -B verify_live.py
```

Tests use synthetic data, real ephemeral localhost HTTP sockets and mocked Things
calls. `verify_live.py` is a separate opt-in real read. See
[observed verification](docs/VERIFICATION.md) and [source provenance](docs/PROVENANCE.md).

Read-only is enforced by the reviewed interface and code, not a read-only macOS
permission. A user/process that can change the code is outside that boundary.
Things loads collections before slicing; collection memory/work is not bounded
by the emitted page size. Captured subprocess output is capped at 1 MiB and has
a 65-second deadline; Apple Events time out after 60 seconds. Pages are not
atomic snapshots: deduplicate IDs and show partial coverage. Upcoming uses a
historical built-in list identifier and can be unavailable on other versions.
General When dates, priorities, recurrence and progress history are unsupported.
