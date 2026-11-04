"""Non-prompting Apple Events permission gate; explicit --request permits OS UI."""
import argparse
import ctypes
import json
import platform


def permission_status(request=False, *, event_class=b"core", event_id=b"getd"):
    if platform.system() != "Darwin":
        return "unsupported"
    try:
        carbon = ctypes.CDLL('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')

        class Desc(ctypes.Structure):
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
