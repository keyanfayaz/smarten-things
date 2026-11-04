# My Things Connection

A local bridge from Things 3 on macOS to a dashboard or assistant. Read task
pages through AppleScript, an authenticated loopback HTTP service, or an optional
stdio MCP facade. Python 3.10+; standard library only.

**Reads are the default.** The default MCP and companion plugin expose only
`read_things_page`. Optional task changes need a separate write credential, a
local owner enable action, an exact preview, and explicit per-item owner approval
in chat. Writes start disabled after every service start or restart; read
credentials never authorize a mutation. The host is trusted to obtain approval.

## Start here

1. Follow [local setup](docs/SETUP.md) with Things open in the logged-in macOS session.
2. Read the [security contract](docs/CONTRACT.md) before connecting a client.
3. Choose the [HTTP API](docs/API.md), [dashboard client](docs/DASHBOARD.md),
   or [local companion plugin](docs/PLUGIN.md).

The service binds to `127.0.0.1:8766` and requires bearer authentication.
Credentials stay outside Git. Local readiness does not mean ChatGPT is connected;
[remote access](docs/REMOTE.md) requires separate configuration.

## Verify without reading private tasks

```sh
python3 -B -m unittest discover -s tests -v
python3 -B things_readonly.py --doctor
```

Tests use synthetic data and mocked Things calls. The task-free doctor checks
app discovery and disk metadata; it does not establish Automation permission or
working reads. Live verification is separate and opt-in.

The [full guide](GUIDE.md) retains CLI commands, integration status, service
details, and known limits. See [optional task changes](docs/PREVIEWS.md),
[observed verification](docs/VERIFICATION.md), [rollout status](docs/ROLLOUT.md),
and [source provenance](docs/PROVENANCE.md). This extends the existing AppleScript
implementation; the historical scan remains inactive.
