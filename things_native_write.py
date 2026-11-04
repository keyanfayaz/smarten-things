"""Fixed native Things operations. Called only after a durable reviewed claim."""
import ctypes
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET

import things_readonly as adapter
from things_changes import private_directory

SCRIPT = Path(__file__).resolve().parent / "src/write_task.applescript"


def local_context():
    """Read installed app metadata/system zone; no Apple Event or app launch."""
    version = adapter.check_environment()
    cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    cf.CFTimeZoneCopySystem.restype = ctypes.c_void_p
    cf.CFTimeZoneGetName.argtypes = [ctypes.c_void_p]
    cf.CFTimeZoneGetName.restype = ctypes.c_void_p
    cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long, ctypes.c_uint32]
    cf.CFStringGetCString.restype = ctypes.c_bool
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    zone = cf.CFTimeZoneCopySystem()
    try:
        buffer = ctypes.create_string_buffer(256)
        if not zone or not cf.CFStringGetCString(cf.CFTimeZoneGetName(zone), buffer, len(buffer), 0x08000100):
            raise ValueError
        name = buffer.value.decode('utf-8')
    finally:
        if zone: cf.CFRelease(zone)
    if not re.fullmatch(r"[A-Za-z0-9_+./-]{1,128}", name):
        raise ValueError("System zone unavailable")
    return {"app_version": version, "time_zone": name}


def write_capabilities():
    adapter.capabilities()
    root = ET.parse(adapter.APP / 'Contents/Resources/Things.sdef').getroot()
    commands = {row.get('name'): row for row in root.findall('.//command')}
    if commands.get('make') is None or commands.get('schedule') is None:
        raise adapter.AdapterError("Supported Things write commands unavailable")
    for field, kind in (("name", "text"), ("notes", "text"), ("due date", "date")):
        prop = root.find('.//class[@name="to do"]/property[@name="'+field+'"]')
        if prop is None or prop.get('access') == 'r' or prop.get('type') != kind:
            raise adapter.AdapterError("Supported Things task update unavailable")


class NativeWriter:
    def __init__(self, directory, runner=None):
        self.directory = private_directory(Path(directory) / 'requests')
        self.runner = runner or adapter.bounded_run

    def __call__(self, bound):
        write_capabilities()
        fd, name = tempfile.mkstemp(prefix='operation-', suffix='.json', dir=self.directory)
        path = Path(name)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as output:
                json.dump(bound, output, ensure_ascii=False, allow_nan=False)
                output.flush(); os.fsync(output.fileno())
            # No title, token or payload in process arguments; no shell.
            result = self.runner(['/usr/bin/osascript', str(SCRIPT), str(path)],
                                 capture_output=True, text=True, encoding='utf-8', timeout=65, check=False)
            if result.returncode:
                raise adapter.AdapterError("Native write outcome unavailable; no retry")
            data = json.loads(result.stdout)
            if type(data) is not dict or set(data) != {'outcome', 'id'} or data['outcome'] not in {'applied', 'not_started', 'uncertain'}:
                raise ValueError
            return data
        except (OSError, ValueError, UnicodeError, subprocess.TimeoutExpired):
            raise adapter.AdapterError("Native write outcome unavailable; no retry") from None
        finally:
            path.unlink(missing_ok=True)
