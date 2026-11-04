#!/usr/bin/env python3
"""Loopback reader; optional separately authorized, owner-enabled task changes."""
import argparse
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import stat
import threading
from urllib.parse import parse_qs, urlsplit

import things_readonly as adapter
from things_access import permission_status, write_permission_status
from things_previews import ChangeError

DEFAULT_TOKEN = Path.home() / "Library/Application Support/ThingsReadOnly/token"
DEFAULT_WRITE_TOKEN = DEFAULT_TOKEN.with_name("write-token")
DEFAULT_CHANGE_STATE = DEFAULT_TOKEN.parent / "changes"
TOKEN_PATTERN = r"[A-Za-z0-9_-]{43,128}"
PERMISSION_REASONS = {
    "granted": "ready", "consent_required": "automation_consent_required",
    "denied": "automation_denied", "app_not_running": "app_not_running",
    "unsupported": "automation_unsupported", "unavailable": "automation_unavailable",
}
READ_FAILURE_REASONS = {
    "Apple Events access denied (-1742/-1743); stopped without retry or settings changes": "automation_denied",
    "App launch/discovery failed (-10827); list access is unverified": "app_discovery_failed",
    "App or object resolution failed (-1728); this alone does not establish permission denial": "object_resolution_failed",
    "Apple Event timed out (-1712); no automatic retry": "apple_event_timeout",
    "Read exceeded the 65-second process deadline; stopped without retry": "reader_deadline",
    "Installed public scripting dictionary lacks required metadata; no read performed": "dictionary_unsupported",
    "Reader output exceeded memory limit; discarded": "reader_output_limit",
    "Could not execute or decode the Things reader": "reader_execution_failed",
    "Invalid JSON from reader; output discarded": "reader_invalid_json",
    "Invalid adapter response; output discarded": "reader_invalid_response",
}
CHECK_NAMES = ("python_3_10_or_newer", "macos", "system_osascript", "reader_source_present",
               "installed_app", "expected_version", "app_discovery")
SAFE_READ_REASONS = frozenset(PERMISSION_REASONS.values()) | frozenset(READ_FAILURE_REASONS.values()) | {
    "automation_denied_latched", "prerequisites_unavailable", "preflight_unavailable", "reader_failed"}


class ReadUnavailable(adapter.AdapterError):
    def __init__(self, reason):
        # Only fixed codes from this module reach HTTP; never exception text.
        self.reason = reason if type(reason) is str and reason in SAFE_READ_REASONS else "reader_failed"
        super().__init__("Things read unavailable; redacted reason available")


def load_token(path):
    """No symlinks, broad permissions or token in argv/environment/logs."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "r", encoding="ascii") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError
            value = source.read(130).strip()
        if not re.fullmatch(TOKEN_PATTERN, value):
            raise ValueError
    except (OSError, ValueError, UnicodeError):
        raise adapter.AdapterError("Token unavailable: use an owner-only regular token file (mode 600)") from None
    return value


def init_token(path):
    path = Path(path).absolute()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.parent.stat()
    if path.parent.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise adapter.AdapterError("Token directory must be owner-only (mode 700)")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as target:
        target.write(secrets.token_urlsafe(32) + "\n")


class LiveReader:
    def __init__(self):
        self.lock = threading.Lock()
        self.denied = False
        self.write_denied = False
        self.last_read = None
        self.consent_state = "not_requested"
        self.consent_attempted = False

    def begin_local_consent(self):
        """Only the local signal dispatcher calls this; never an HTTP handler."""
        if self.denied or self.consent_attempted:
            return False
        if not self.lock.acquire(blocking=False):
            self.consent_state = "reader_busy"
            return False
        if self.denied or self.consent_attempted:
            self.lock.release()
            return False
        self.consent_attempted = True
        self.consent_state = "checking"
        try:
            threading.Thread(target=self._local_consent_worker, daemon=True).start()
        except Exception:
            self.consent_state = "unavailable"
            self.lock.release()
        return True

    def _local_consent_worker(self):
        # This is a thread of the service, not a separate probe process. The
        # reader lock prevents task reads while the user reviews the OS prompt.
        try:
            status = permission_status()
            if status == "consent_required":
                self.consent_state = "awaiting_user"
                status = permission_status(request=True)
            self.consent_state = status if status in PERMISSION_REASONS else "unavailable"
            self.denied = self.denied or self.consent_state == "denied"
        except Exception:
            self.consent_state = "unavailable"
        finally:
            self.lock.release()

    def _diagnostic_result(self, preflight):
        return {"diagnostic_schema_version": 1, "probe_context": "service_process",
                "service_pid": os.getpid(), "task_read_performed": False,
                "denial_latched": self.denied, "preflight": preflight,
                "last_read": dict(self.last_read) if self.last_read else None,
                "local_consent": {"supported": True, "state": self.consent_state,
                                  "attempted": self.consent_attempted}}

    def _preflight(self):
        result = {"automation": "not_probed", "reason": "automation_denied_latched",
                  "failed_checks": [], "app_running": None}
        if self.denied:
            return result  # Diagnostics must not bypass or clear a denial latch.
        try:
            status = permission_status()  # Default request=False: never consent UI.
            if status not in PERMISSION_REASONS:
                status = "unavailable"
        except Exception:
            status = "unavailable"
        result.update(automation=status, reason=PERMISSION_REASONS[status])
        if status != "granted":
            return result
        try:
            report = adapter.diagnose(adapter.load_config())
            checks = report["checks"]
            result["failed_checks"] = [name for name in CHECK_NAMES if checks.get(name, {}).get("ok") is not True]
            discovery = checks.get("app_discovery", {})
            count = discovery.get("visible_running_instances")
            result["app_running"] = count > 0 if type(count) is int and count >= 0 else None
            if result["failed_checks"] or report.get("prerequisites_met") is not True:
                result["reason"] = "prerequisites_unavailable"
            elif result["app_running"] is not True:
                result["reason"] = "app_not_running"
        except Exception:
            result["reason"] = "preflight_unavailable"
        return result

    def diagnostics(self):
        """Probe in this service process; no task read, prompt, raw text or path."""
        if not self.lock.acquire(blocking=False):
            if self.consent_state in {"checking", "awaiting_user"}:
                return self._diagnostic_result({"automation": "not_probed", "reason": "local_consent_pending",
                                                "failed_checks": [], "app_running": None})
            raise BusyError
        try:
            preflight = self._preflight()
            self.denied = self.denied or preflight["automation"] == "denied"
            return self._diagnostic_result(preflight)
        finally:
            self.lock.release()

    def __call__(self, args):
        return self._perform_read(lambda: adapter.read_page(args))

    def lookup(self, kind, item_id):
        return self._perform_read(lambda: adapter.read_item(kind, item_id))

    def perform_change(self, action):
        """Same reader serialization and non-prompting app gate; no consent UI."""
        if not self.lock.acquire(blocking=False):
            raise BusyError
        try:
            if self.write_denied:
                raise ReadUnavailable('automation_denied')
            preflight = self._preflight()
            if preflight["reason"] != "ready":
                self.denied = self.denied or preflight["automation"] == "denied"
                raise ReadUnavailable(preflight["reason"])
            status = write_permission_status()
            if status != "granted":
                self.write_denied = self.write_denied or status == "denied"
                raise ReadUnavailable(PERMISSION_REASONS.get(status, "automation_unavailable"))
            return action()
        finally:
            self.lock.release()

    def _perform_read(self, action):
        if not self.lock.acquire(blocking=False):
            raise BusyError
        try:
            preflight = self._preflight()
            if preflight["reason"] != "ready":
                self.denied = self.denied or preflight["automation"] == "denied"
                self.last_read = {"stage": "preflight", "reason": preflight["reason"]}
                raise ReadUnavailable(preflight["reason"])
            try:
                page = action()
            except adapter.AdapterError as error:
                reason = READ_FAILURE_REASONS.get(str(error), "reader_failed")
                self.denied = self.denied or reason == "automation_denied"
                self.last_read = {"stage": "reader", "reason": reason}
                raise ReadUnavailable(reason) from None
            except Exception:
                self.last_read = {"stage": "reader", "reason": "internal_error"}
                raise
            self.last_read = {"stage": "reader", "reason": "succeeded"}
            return page
        finally:
            self.lock.release()


class BusyError(Exception):
    pass


def page_arguments(path, query):
    params = parse_qs(query, keep_blank_values=True, max_num_fields=4)
    if set(params) - {"offset", "limit"} or any(len(v) != 1 for v in params.values()):
        raise ValueError
    offset = params.get("offset", ["0"])[0]
    limit = params.get("limit", ["30"])[0]
    if not re.fullmatch(r"[0-9]{1,7}", offset) or not re.fullmatch(r"[0-9]{1,2}", limit):
        raise ValueError
    if not 0 <= int(offset) <= 1000000 or not 1 <= int(limit) <= 50:
        raise ValueError
    argv = ["--metadata", "--offset", offset, "--limit", limit]
    if path in {"/v1/lists/" + name for name in adapter.LIST_IDS}:
        argv += ["--list", path.rsplit("/", 1)[1]]
    elif path == "/v1/projects":
        argv += ["--projects"]
    elif re.fullmatch(r"/v1/projects/[A-Za-z0-9_-]{1,128}/tasks", path):
        argv += ["--project", path.split("/")[3]]
    else:
        raise LookupError
    return adapter.arguments(argv)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 8

    def __init__(self, address, token, reader=None, allowed_hosts=(), *, write_token=None, changes=None):
        if address[0] != "127.0.0.1":
            raise ValueError("Only IPv4 loopback is allowed")
        if write_token is not None and hmac.compare_digest(token, write_token):
            raise ValueError("Read and write capabilities must be separate")
        self.token = token
        self.reader = reader or LiveReader()
        self.slots = threading.BoundedSemaphore(8)
        self.local_consent_requested = False
        self.local_write_enable_requested = False
        self.write_token, self.changes = write_token, changes
        self.write_enable_reason = "not_enabled"
        super().__init__(address, Handler)
        port = self.server_address[1]
        self.allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}", *allowed_hosts}

    def service_actions(self):
        super().service_actions()
        if self.local_consent_requested:
            self.local_consent_requested = False
            self.reader.begin_local_consent()
        if self.local_write_enable_requested:
            self.local_write_enable_requested = False
            self.enable_changes()

    def enable_changes(self):
        # Only the fixed local owner signal calls this. Never invoked by an
        # HTTP/MCP request, file discovery, startup or a remembered approval.
        if self.changes is not None:
            return
        journal = None
        try:
            from things_changes import ChangeService, Journal
            from things_native_write import NativeWriter, local_context
            token = load_token(DEFAULT_WRITE_TOKEN)
            if hmac.compare_digest(token, self.token):
                raise ValueError
            journal = Journal(DEFAULT_CHANGE_STATE)
            changes = ChangeService(journal, hashlib.sha256(token.encode('ascii')).hexdigest(),
                                    self.reader.lookup, adapter.read_item, NativeWriter(DEFAULT_CHANGE_STATE),
                                    self.reader.perform_change, local_context)
            self.write_token, self.changes = token, changes
            self.write_enable_reason = "enabled_for_current_process"
        except Exception:
            if journal is not None: journal.close()
            self.write_enable_reason = "local_write_setup_failed"

    def server_close(self):
        super().server_close()
        if self.changes is not None and hasattr(self.changes, 'journal'):
            self.changes.journal.close()

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        # No tracebacks: they can capture private arguments or raw subprocess data.
        pass


class Handler(BaseHTTPRequestHandler):
    server_version = "ThingsReadOnly"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *args):
        pass

    def send_error(self, code, message=None, explain=None):
        # The base handler otherwise embeds the submitted request in HTML.
        self.respond(code, {"error": "request_rejected"})

    def respond(self, code, data):
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        if len(payload) > adapter.MAX_OUTPUT:
            code, payload = 502, b'{"error":"response_too_large"}'
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            if code == 401:
                self.send_header("WWW-Authenticate", 'Bearer realm="things-readonly"')
            self.end_headers()
            self.wfile.write(payload)
        except (OSError, socket.timeout):
            pass
        finally:
            self.close_connection = True

    def authorized(self, scope="read"):
        hosts = self.headers.get_all("Host", [])
        origins = self.headers.get_all("Origin", [])
        if len(hosts) != 1 or hosts[0] not in self.server.allowed_hosts or origins:
            self.respond(403, {"error": "host_or_origin_rejected"})
            return False
        auth = self.headers.get_all("Authorization", [])
        value = auth[0] if len(auth) == 1 else ""
        expected_token = self.server.write_token if scope == "write" else self.server.token
        if expected_token is None or not hmac.compare_digest(value.encode("utf-8"), ("Bearer " + expected_token).encode("ascii")):
            self.respond(401, {"error": "unauthorized"})
            return False
        return True

    def do_GET(self):
        change_status = self.path.startswith('/v1/change-previews/')
        if not self.authorized("write" if change_status else "read"):
            return
        if self.headers.get("Transfer-Encoding") or self.headers.get("Content-Length", "0") != "0":
            return self.respond(400, {"error": "get_body_rejected"})
        try:
            if len(self.path) > 512:
                raise ValueError
            url = urlsplit(self.path)
            if url.scheme or url.netloc or url.fragment:
                raise ValueError
            if url.path == "/v1/health" and not url.query:
                return self.respond(200, {"service": "things-readonly", "read_only": self.server.changes is None, "task_access": "not_probed"})
            if url.path == "/v1/write-capability" and not url.query:
                return self.respond(200, {"supported": True, "enabled": self.server.changes is not None,
                                         "service_pid": os.getpid(), "reason": self.server.write_enable_reason,
                                         "write_denied_latched": bool(getattr(self.server.reader, 'write_denied', False)),
                                         "startup_default": "disabled"})
            status_match = re.fullmatch(r"/v1/change-previews/([A-Za-z0-9_-]{1,128})", url.path)
            if status_match and not url.query and self.server.changes is not None:
                try:
                    return self.respond(200, self.server.changes.status(status_match[1]))
                except ChangeError as error:
                    return self.respond(404, {"error": error.reason})
            if url.path == "/v1/capabilities" and not url.query:
                return self.respond(200, adapter.capabilities())
            if url.path == "/v1/diagnostics" and not url.query:
                return self.respond(200, self.server.reader.diagnostics())
            item_match = re.fullmatch(r"/v1/items/(task|project)/([A-Za-z0-9_-]{1,128})", url.path)
            if item_match and not url.query:
                kind, item_id = item_match.groups()
                item = self.server.reader.lookup(kind, item_id)
                return self.respond(200, adapter.validate_item(item, kind, item_id))
            args = page_arguments(url.path, url.query)
            page = self.server.reader(args)
            kind, container = adapter.target_for(args)
            adapter.validate_page(page, kind, container, args.offset, args.limit, metadata=True)
            self.respond(200, page)
        except LookupError:
            self.respond(404, {"error": "unsupported_read"})
        except ValueError:
            self.respond(400, {"error": "invalid_request"})
        except BusyError:
            self.respond(429, {"error": "reader_busy"})
        except ReadUnavailable as error:
            self.respond(503, {"error": "things_unavailable", "reason": error.reason,
                               "action": "check_interactive_session_and_automation"})
        except adapter.AdapterError:
            # Detailed raw diagnostics and task context never cross this boundary.
            self.respond(503, {"error": "things_unavailable", "action": "check_interactive_session_and_automation"})
        except Exception:
            self.respond(500, {"error": "internal_error"})

    def reject_write(self):
        if self.authorized():
            self.respond(405, {"error": "read_only"})

    def do_POST(self):
        if self.path != '/v1/change-previews' and re.fullmatch(r'/v1/change-previews/[A-Za-z0-9_-]{1,128}/apply', self.path) is None:
            return self.reject_write()
        if not self.authorized("write"):
            return
        if self.server.changes is None:
            return self.respond(403, {"error": "write_capability_disabled"})
        try:
            lengths = self.headers.get_all('Content-Length', [])
            if (self.headers.get('Transfer-Encoding') or len(lengths) != 1
                    or not re.fullmatch(r'[0-9]{1,5}', lengths[0]) or not 1 <= int(lengths[0]) <= 65536
                    or self.headers.get('Content-Type', '').lower() != 'application/json'):
                raise ValueError
            def pairs(entries):
                result = {}
                for key, value in entries:
                    if key in result: raise ValueError
                    result[key] = value
                return result
            raw = self.rfile.read(int(lengths[0]))
            if len(raw) != int(lengths[0]): raise ValueError
            def reject_constant(value): raise ValueError
            request = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=reject_constant)
            if self.path == '/v1/change-previews':
                return self.respond(200, self.server.changes.preview(request))
            key = self.path.split('/')[3]
            return self.respond(200, self.server.changes.apply(key, request))
        except ChangeError as error:
            code = 400 if error.reason.startswith('invalid_') else 429 if error.reason == 'preview_capacity' else 409
            self.respond(code, {"error": error.reason})
        except BusyError:
            self.respond(429, {"error": "reader_busy"})
        except ReadUnavailable as error:
            self.respond(503, {"error": "things_unavailable", "reason": error.reason})
        except (ValueError, UnicodeError, socket.timeout):
            self.respond(400, {"error": "invalid_request"})
        except adapter.AdapterError:
            self.respond(503, {"error": "things_unavailable"})
        except Exception:
            self.respond(500, {"error": "internal_error"})

    do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_HEAD = do_TRACE = do_CONNECT = reject_write


def install_local_consent_signal(server):
    def queue_local_consent(signum, frame):
        # No locks, I/O, threads or Apple Events in the signal handler.
        server.local_consent_requested = True
    return signal.signal(signal.SIGUSR1, queue_local_consent)


def install_local_write_signal(server):
    def queue_write_enable(signum, frame):
        server.local_write_enable_requested = True
    return signal.signal(signal.SIGUSR2, queue_write_enable)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token-file", type=Path, default=DEFAULT_TOKEN)
    parser.add_argument("--port", type=adapter.bounded_int(1024, 65535), default=8766)
    parser.add_argument("--allowed-host", action="append", default=[], help="Exact private proxy Host value; no wildcards")
    parser.add_argument("--init-token", action="store_true", help="Create a credential without printing it; never overwrite")
    args = parser.parse_args(argv)
    try:
        if args.init_token:
            init_token(args.token_file)
            print("Owner-only credential created; value suppressed")
            return 0
        token = load_token(args.token_file)
        adapter.check_environment()
        adapter.capabilities()
        server = Server(("127.0.0.1", args.port), token, allowed_hosts=args.allowed_host)
        print(f"Things read-only service listening on 127.0.0.1:{args.port}; task logging disabled", flush=True)
        with server:
            previous = install_local_consent_signal(server)
            previous_write = install_local_write_signal(server)
            try:
                server.serve_forever(poll_interval=0.5)
            except KeyboardInterrupt:
                pass
            finally:
                signal.signal(signal.SIGUSR1, previous)
                signal.signal(signal.SIGUSR2, previous_write)
        return 0
    except (adapter.AdapterError, OSError, ValueError):
        print("Service setup failed; check token permissions, dictionary, app installation and port", file=__import__('sys').stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
