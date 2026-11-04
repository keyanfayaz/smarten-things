#!/bin/sh
# Select an existing supported Python; never install runtimes or request consent.
set -eu
plugin_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
for python_path in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
    if [ -x "$python_path" ] && "$python_path" -B -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
        exec "$python_path" -B "$plugin_dir/server/things_mcp.py"
    fi
done
printf '%s\n' 'My Things Connection requires an existing Python 3.10+ installation.' >&2
exit 1
