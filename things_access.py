"""Non-prompting Apple Events permission gate; explicit --request permits OS UI."""
import argparse
import ctypes
import json
import platform
import threading


def _native_permission_status(request=False, *, event_class=b"core", event_id=b"getd"):
    if platform.system() != "Darwin":
        return "unsupported"
    try:
        carbon = ctypes.CDLL('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')

        class Desc(ctypes.Structure):
            # Apple's AEDataModel.h declares AEDesc under #pragma pack(push, 2).
            _pack_ = 2
            _fields_ = [("descriptorType", ctypes.c_uint32), ("dataHandle", ctypes.c_void_p)]

        carbon.AECreateDesc.argtypes = [ctypes.c_uint32, ctypes.c_void_p, ctypes.c_ssize_t, ctypes.POINTER(Desc)]
        carbon.AECreateDesc.restype = ctypes.c_int32
        carbon.AEDeterminePermissionToAutomateTarget.argtypes = [ctypes.POINTER(Desc), ctypes.c_uint32, ctypes.c_uint32, ctypes.c_bool]
        carbon.AEDeterminePermissionToAutomateTarget.restype = ctypes.c_int32
        carbon.AEDisposeDesc.argtypes = [ctypes.POINTER(Desc)]
        target = Desc()
        bundle = b"com.culturedcode.ThingsMac"
        fourcc = lambda value: int.from_bytes(value, 'big')
        if carbon.AECreateDesc(fourcc(b"bund"), bundle, len(bundle), ctypes.byref(target)):
            return "unavailable"
        try:
            result = carbon.AEDeterminePermissionToAutomateTarget(ctypes.byref(target), fourcc(event_class), fourcc(event_id), request)
        finally:
            carbon.AEDisposeDesc(ctypes.byref(target))
        return {0: "granted", -1744: "consent_required", -1743: "denied", -600: "app_not_running"}.get(result, "unavailable")
    except (OSError, AttributeError):
        return "unavailable"


class _PermissionProbe:
    """Bound a non-prompting check in this process without retrying a hung call."""

    def __init__(self, checker, timeout=5):
        self.checker, self.timeout = checker, timeout
        self.lock = threading.Lock()
        self.active = None
        self.denied = set()

    def check(self, request=False, *, event_class=b"core", event_id=b"getd"):
        key = (event_class, event_id)
        with self.lock:
            if key in self.denied:
                return "denied"
            if self.active is not None:
                return "timed_out" if self.active["timed_out"] else "unavailable"
            row = {"done": threading.Event(), "result": "unavailable", "timed_out": False}
            self.active = row

            def probe():
                try:
                    result = self.checker(request, event_class=event_class, event_id=event_id)
                except Exception:
                    result = "unavailable"
                with self.lock:
                    # A late denial must still latch, even after the caller's deadline.
                    if result == "denied":
                        self.denied.add(key)
                    row["result"] = result
                    self.active = None
                    row["done"].set()

            try:
                threading.Thread(target=probe, daemon=True).start()
            except Exception:
                self.active = None
                return "unavailable"
        # Only the existing explicit local owner flow may wait for a consent UI.
        if not row["done"].wait(None if request else self.timeout):
            with self.lock:
                row["timed_out"] = True
            return "timed_out"
        return row["result"]


_permission_probe = _PermissionProbe(_native_permission_status)


def permission_status(request=False, *, event_class=b"core", event_id=b"getd"):
    """Check in this process; fail closed within five seconds without OS UI."""
    return _permission_probe.check(request, event_class=event_class, event_id=event_id)


def write_permission_status():
    """Fixed non-prompting checks; no HTTP/MCP caller can request OS consent."""
    for event_class, event_id in [(b"core", b"crel"), (b"core", b"setd"), (b"THGS", b"schd")]:
        status = permission_status(False, event_class=event_class, event_id=event_id)
        if status != "granted":
            return status
    return "granted"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", action="store_true", help="Explicitly allow the macOS Automation consent prompt")
    args = parser.parse_args()
    status = permission_status(args.request)
    print(json.dumps({"apple_events_access": status, "tasks_read": False}))
    raise SystemExit(0 if status == "granted" else 1)
