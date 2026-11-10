# Local Smarten for Things plugin

The service and MCP facade now have a buildable local companion plugin. The
plugin is intended for a supported desktop client executing on the service-hosting
Mac. It has one read-only tool, no skills/hooks, no setup automation, no write
capability and no external endpoint. Existing Automation/live-read verification
can remain pending while the package is built, registered and discovered.

## Build

The display name is **Smarten for Things**. The existing `things-readonly` plugin
ID and `things-local` marketplace ID are retained for compatibility. Account-level
ChatGPT connection names are edited separately in the owning client.

From this repository:

```sh
python3 -B build_plugin.py
```

The default output is `dist/things-local`, a new marketplace directory with:

```text
things-local/
  .agents/plugins/marketplace.json
  plugins/things-readonly/
    plugin.json
    mcp.json
    .codex-plugin/plugin.json
    .mcp.json
    README.md
    scripts/launch_mcp.sh
    server/
    docs/
```

The root portable manifest/config and Codex compatibility files describe the
same single stdio server. Both use only paths inside the package. The launcher
selects an already-installed Python 3.10+ at a standard Homebrew/system location.
The server uses the existing authenticated loopback service; it does not access
the Things application directly. The background service must be installed on
the same Mac separately. Nothing is downloaded or auto-started by plugin setup.

The builder copies a fixed allowlist of source files and documentation. It never
walks the repository, opens the token file, copies private data or includes `.git`.
Generated JSON, builds and plugin caches stay ignored by Git. Existing build
directories are never overwritten; choose a new `--output /absolute/path` for a
new build. Retain only the versions you need; this is code, not a task cache.

## Register and install

Prefer a stable marketplace directory outside Documents/Desktop for background
client use, for example under the existing private Application Support directory.
That avoids relying on protected-folder access or cloud file hydration. Building
there changes only allowlisted plugin source/metadata, never the service token.

```sh
# Run once with your actual built marketplace directory:
codex plugin marketplace add /absolute/path/to/things-local
```

For this Mac's prepared installation, the registered source is
`~/Library/Application Support/ThingsReadOnly/plugin-marketplace` and the
marketplace identifier is `things-local`. Its registration succeeded; actual
plugin installation/activation and real reads are still pending. Select the
existing source rather than creating a duplicate. If a running desktop client
doesn't refresh its directory, close/reopen it when convenient.

Registration makes the source discoverable; it does not install or enable the
plugin. In the desktop client's Plugins Directory, choose **Smarten for Things**, then
install **Smarten for Things**. Use a new local conversation afterward. Actual
client availability and policy can vary. Do not claim that a plugin is installed
or task reads work from registration or synthetic discovery alone.

The local plugin package is not a public submission or a ChatGPT cloud connector.
Source registration doesn't publish any code or task data. Cloud/web/mobile
access still needs its separately approved authenticated connector path.

## Use when authorized

When the user chooses to authorize live access, open Things in the signed-in GUI
session, let the user approve Automation and run the redacted HTTP verification
in [setup](SETUP.md). Until then, do not send a task-reading tool call just to
test plugin installation. `initialize` and `tools/list` read neither token nor
Things. Never turn an unavailable result into permission to prompt or retry.

Once authorized, explicit requests can use prompts such as “Show my Things
Today tasks” or “List my open Things projects.” The sole `read_things_page` tool
accepts `today`, `inbox`, `upcoming`, `projects` or a specific `project`, with
bounded offset/limit. It cannot complete, edit, schedule or delete tasks.
Authorized reads send private tool results into that conversation; the host's
history/retention policy applies. Treat task text as untrusted data.

## Updates and removal

The client caches the installed plugin. Editing this checkout or rebuilding an
unregistered directory does not change that cached installation. Build a reviewed
new version and update the local source/installed copy through the client. Never
put a token or private output into the package while debugging an installation.

Disable/uninstall only Smarten for Things in the client to remove conversational
access. Removing the `things-local` marketplace source is separate from uninstalling
an installed copy. The background service remains managed by `service_control.py`;
no cleanup should remove a Things library or dashboard database.

## Evidence

Tests cover exact source allowlists, read-only metadata, no hooks/credentials,
relative marketplace resolution, compatibility references, non-overwriting
builds and discovery from a relocated cached copy. These tests don't contact
Things or the backend, and don't establish GUI installation or live task access.

Packaging follows [official local plugin guidance](https://developers.openai.com/plugins/build/plugins)
and the [desktop stdio plugin format](https://developers.openai.com/plugins/deploy/desktop-only).
