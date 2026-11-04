# Local, Tailscale and ChatGPT access

These are three different capabilities. The installed HTTP service listens only
on loopback. Nothing in installation publishes it, registers a ChatGPT plugin,
changes Tailscale, starts a tunnel or sends tasks to a cloud service.

The owner's existing private Secure MCP Tunnel and ChatGPT plugin were separately
configured and verified with live reads. On November 4, 2026, their coordinated
runtime was updated and the same hosted plugin renamed **My Things Connection**.
See [current verification](VERIFICATION.md) for the observed checkpoint and
remaining live-write/device acceptance. The setup guidance below describes
requirements for a new installation, rather than the current owner's status.

| Path | Who can reach it | Status/requirements |
| --- | --- | --- |
| Local HTTP | Trusted backend on this Mac + bearer token | Implemented; no browser/CORS access |
| Tailscale private HTTPS | Authorized tailnet device + bearer token | Optional, not configured here |
| Local MCP stdio | Trusted same-user MCP client + token file | Implemented; not automatically registered |
| ChatGPT Secure MCP Tunnel | Authorized OpenAI workspace/user via an approved tunnel | Supported path documented; account availability and tool calls unverified |
| Public HTTPS MCP + OAuth | Authorized ChatGPT user after explicit public deployment approval | Future alternative; not implemented or exposed |

## Tailscale for private dashboard access

Prefer **Tailscale Serve** for a dashboard backend on another authorized device.
Serve can proxy a loopback service over tailnet HTTPS. Keep bearer auth, restrict
TCP 443 to the intended user/devices with Tailscale grants/ACLs, and add the exact
private HTTPS authority to the service's `--allowed-host` arguments. Default
Host checks deliberately reject arbitrary proxies. Leave the backend bound to
127.0.0.1. Use a backend client with HTTPS verification; do not disable TLS checks.

After reviewing the tailnet policy and explicitly approving the configuration,
a possible command is `tailscale serve --bg --https=443 http://127.0.0.1:8766`.
Do not run this blindly: inspect existing Serve routing first to avoid changing
other services. `--bg` persists; remove only this mapping when done. No Serve or
Funnel commands are executed by this setup. **Funnel makes a service public** and
requires separate explicit approval.

Source: [Tailscale Serve command](https://tailscale.com/docs/reference/tailscale-cli/serve).

A tailnet-only URL does not make the service reachable from ChatGPT's cloud.
Tailscale authentication is a network boundary, not a replacement for application
read authorization. The existing unauthenticated dashboard demo must not be
served remotely with private tasks added to it.

## Supported private ChatGPT path

As checked on 2026-10-24, OpenAI documents **Secure MCP Tunnel**. A client on the
Mac can connect outbound to OpenAI and forward to a private stdio/HTTP MCP server
without public ingress. This is a separate OpenAI connection; cloud ChatGPT does
not gain direct localhost/tailnet access.

Conditional setup: select the intended Platform organization and ChatGPT workspace;
verify tunnel Read/Manage/Use permissions and workspace associations; provision a
restricted runtime credential; run the official tunnel client's doctor against
`things_mcp.py`; add the custom MCP plugin using Connection: Tunnel; configure
supported authentication; make one redacted representative read. Availability
on this account is unverified. Do not call it connected before that call succeeds.

The tunnel and task transfer require user approval before activation here. Keep
runtime credentials outside Git, reuse approved credentials only after confirmation,
and never put them in this repository. No OpenAI credential or tunnel is created.
Source: [OpenAI Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels).

## Authenticated public HTTPS alternative

If the private tunnel is unavailable, a remote ChatGPT MCP connection would need
a deliberately hosted HTTPS **MCP** endpoint and compatible OAuth authorization.
The current REST service's static bearer token and GET routes are not an OAuth
MCP connector. A future HTTPS transport should validate token issuer, audience,
expiry and `things:read` scope, expose required discovery metadata, and use an
established identity provider. Keep the existing read allowlist behind it.
Public ingress, OAuth infrastructure and task transfer require explicit approval;
none are implemented or configured by this local setup.

Source: [OpenAI plugin authentication](https://developers.openai.com/plugins/build/auth).
The [MCP transport specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
distinguishes stdio and Streamable HTTP. The local facade uses stdio only.

## Privacy and proof

Neither network isolation nor read-only annotations guarantee content is safe for
an agent to obey. Treat titles as untrusted data. Authorized clients receive
private task data; their tracing, storage and model retention must be considered
before connecting. The local code keeps task results only transiently in memory.
Remote readiness needs evidence of TLS, identity/network policy, application
authorization and a successful tool read. A running Tailscale app, a healthy
localhost endpoint or local MCP discovery proves only that individual layer.
