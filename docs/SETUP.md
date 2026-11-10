# Setup, startup and troubleshooting

## Prerequisites

Use the Mac containing the Things library, in a signed-in interactive desktop
session. Python 3.10+, `/usr/bin/osascript`, and Things 3 at
`/Applications/Things3.app` are required. No pip packages, database or cloud
service is needed. Moving the code to another Mac does not move its library,
Automation permission, credential or login session.

```sh
git clone https://github.com/keyanfayaz/smarten-things.git
cd smarten-things
python3 -B -m unittest discover -s tests -v
python3 -B things_readonly.py --doctor
```

Doctor reads Info.plist and NSWorkspace metadata only. It does not launch,
register or repair Things or test task access. In a sandbox, it may not see the
interactive application's registration. Run in the intended user session; do
not reset Launch Services or change macOS security settings to make it pass.

Optional `--config .things-readonly.ini` loads only explicit CLI page-limit and
installed-version settings; see `config.example.ini`. It cannot add commands,
change app paths, grant access or configure remote exposure.

## Automation consent and first read

Open Things yourself on that Mac. The executing process needs **Automation →
Things** Apple Events access. macOS may attribute this to the terminal, Codex,
or Python host, depending on how it was launched. This OS permission grants
app control; read-only behavior comes from this adapter's code, not from macOS.
No Accessibility, Full Disk Access or database access is requested.

```sh
# Non-prompting public Apple Events API; never reads task data:
python3 -B things_access.py
# Only after agreeing to the specific Automation request:
python3 -B things_access.py --request
# Approve the macOS prompt yourself. Then perform one bounded, redacted read:
python3 -B verify_live.py
```

`verify_live.py` reports only fixed pass/unavailable labels; no IDs, titles,
project names, dates or task counts are printed. `--extended` checks at most one
item each in Inbox, Today, Upcoming, the open-project catalog and the first
open project's tasks. It stops at the first failure. Do not run these real reads
in CI. Only explicit `--request` or the local `request-access` action below can
request consent. If denied, stop;
permission changes belong to the user. No reset/repair/retry command is provided.

The daemon never requests consent automatically. Launchd may have a different
responsible identity from Terminal/Codex. A Terminal read is not proof of a daemon
read: use the local daemon consent step below, then verify HTTP separately.

Before requesting any new permission, use the authenticated task-free diagnostics
from the installed runtime (or this checkout after updating the runtime):

```python
from dashboard_client import ThingsClient
from things_service import DEFAULT_TOKEN
report = ThingsClient(DEFAULT_TOKEN).get('/v1/diagnostics')
print(report)  # Fixed redacted status only; no tasks, credential or paths.
```

This preflight executes inside the service process, unlike `things_access.py`
launched by Terminal/Codex. Diagnose its reported cause before proposing a grant.
The service PID identifies which process supplied the evidence; diagnostics do
not identify macOS's responsible app. Do not assume that a separate helper using
the same Python binary has the daemon's Automation identity. If consent is
required, obtain separate user approval for the correct host-to-Things grant;
this endpoint never requests it. If attribution remains unknown, leave it unknown.
`ready` means prerequisites passed, not that task access succeeded.

### Explicit local consent for the running LaunchAgent

After the user agrees to review a new Automation request, run locally:

```sh
python3 -B service_control.py request-access
```

This owner command authenticates the task-free diagnostics, verifies that its PID
matches the fixed LaunchAgent, and sends only that job `SIGUSR1`. An old daemon
without this capability is refused before any signal. The signal handler only
sets a flag. The service then performs its non-prompting check and, only for
`consent_required`, requests consent in a thread of the same service process.
No subprocess/probe identity is substituted. The signal uses local OS process
ownership; possession of the HTTP token cannot trigger it. Same-user arbitrary
code execution remains outside this boundary. No new socket, credential or
persistent access is created, and no HTTP/MCP consent endpoint exists.

Only the user may choose in the macOS prompt. Verify its displayed requesting
app and target Things; the PID alone is not a displayed identity. The OS grant
allows app control, while the adapter's code and tools remain strictly read-only.
Do not automatically approve or change privacy settings. No task read occurs
as part of consent, even after approval. Run one separate redacted HTTP read
only after consent succeeds and the user has approved that verification.

The service allows at most one local attempt per process. Denial latches and
stops further requests; there is no reset/retry action. Do not restart to bypass
a denial. Health remains task-free during a prompt; task reads return busy.
Diagnostics expose only `local_consent` support/state/attempted flags and a fixed
pending status; they never initiate consent. A busy reader causes the local
signal action to be dropped without scheduling a future prompt. A signal-sent
message proves dispatch only; inspect the actual OS prompt and subsequent
service diagnostic rather than assuming consent or data access succeeded.

Implementation references: [Python signal-handler constraints](https://docs.python.org/3/library/signal.html#signals-and-threads)
and the [server service-actions hook](https://docs.python.org/3/library/socketserver.html#socketserver.BaseServer.service_actions).

## Foreground service

```sh
python3 -B things_service.py --init-token
python3 -B things_service.py
```

Initialization creates only a cryptographically random credential in
`~/Library/Application Support/ThingsReadOnly/token`, mode 600, directory 700.
It never prints the secret or overwrites an existing file. Do not pass the value
on the command line or copy it into a browser. A custom `--token-file` is allowed
for controlled testing; its parent must be owner-only at creation. No task data
is persisted. All routes require the credential, including health.

The default listener is only `127.0.0.1:8766`. `--port` can select another local
port. There is intentionally no LAN/all-interface bind option. Ctrl-C stops a
foreground process. Do not run it concurrently with a LaunchAgent on the same
port. If startup fails, check the app/dictionary, token permissions and port;
raw exception data and task context are suppressed.

Task-free authenticated health check:

```python
from dashboard_client import ThingsClient
from things_service import DEFAULT_TOKEN
assert ThingsClient(DEFAULT_TOKEN).get('/v1/health')['read_only'] is True
```

Avoid printing data from task routes. Health reports process health only.

## User LaunchAgent

```sh
python3 -B service_control.py install
python3 -B service_control.py stop
python3 -B service_control.py start
python3 -B service_control.py restart
# After reviewing/testing changes in this checkout:
python3 -B service_control.py update
```

Install writes `~/Library/LaunchAgents/dev.keyanfayaz.things-readonly.plist` and
starts only that service in the current GUI domain. It copies an explicit small
allowlist of reviewed source files to
`~/Library/Application Support/ThingsReadOnly/app`. No Git repository, private
configuration, source task data or exports are copied. This stable runtime
avoids depending on Documents/Desktop privacy access or cloud file hydration.
The executable path is absolute; relocate/reinstall Python deliberately if it
changes. The installed runtime is a snapshot: `update` refreshes it after review,
preserving the credential, then restarts this service. Editing the checkout alone
does not change the running installation.

The agent is restricted to Aqua sessions, starts at login and restarts on failure
with a 30-second throttle. Logs go to `/dev/null` so task data cannot enter a
launchd application log. No idle Things polling occurs. The host must remain
awake, signed in, with Things open for reads. Logout stops GUI-session availability;
a LaunchDaemon or headless SSH login cannot substitute for an interactive Things
session. This setup does not change sleep, auto-login, app-login or security settings.

To disable future startup, stop the service, then move its exact plist out of
`~/Library/LaunchAgents` yourself. Keep the credential if returning later. Removing
runtime/credential files is a separate deliberate cleanup; no source tasks are
involved. Never delete the Things library or dashboard database.

After authorization, test the actual daemon without printing data:

```sh
python3 -B verify_live.py --http
```

A failed/denied read is not retried. Denial latches for the lifetime of the
service; after the user authorizes access, restart once and explicitly verify.
No private output should be copied to bug reports, tests or commits.

## Rotation

Stop the service and move the existing credential to a private owner-only backup
outside Git. Run `--init-token` again, securely update any authorized backend
copies, and start the service. The server reads its token at startup; old tokens
stop working after restart. Avoid retaining unneeded old credentials. The
installer never rotates or overwrites a token automatically.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Doctor sees no registered/running app | Check the intended GUI session and open Things yourself; sandbox metadata can differ |
| `app_not_running` | Open Things in the signed-in desktop session; do not auto-launch from a daemon |
| `consent_required` | Let the user approve the specific Automation request interactively |
| `denied`, -1742/-1743 | Stop; user controls permission; no retries/resets/security changes |
| -10827 | App launch/discovery failed; it is not proof of a permission denial |
| -1728 | App/object lookup failed; Upcoming or a removed project can be unavailable; do not return invented empty data |
| Timeout/-1712 | Stop this read; confirm Things is responsive before a new explicit request |
| 401 | Check the backend credential file; no token in URL/browser/shell |
| 403 | Origin requests are forbidden; use backend-to-backend access and exact approved Host |
| 429 | Another bounded read is active; let it finish, then explicitly refresh |
| 503 | Check user session, Things, dictionary and Automation; do not interpret as zero tasks |
| HTTP health fails | Inspect only this LaunchAgent's state/exit code and port; runtime/Python paths must exist |
| Dictionary mismatch | Stop and review the installed public dictionary; never fall back to private JSON/database |

See [API](API.md), [dashboard integration](DASHBOARD.md), and [remote access](REMOTE.md).
