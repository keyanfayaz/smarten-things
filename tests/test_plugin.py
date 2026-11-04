"""Plugin distribution tests; synthetic discovery only, no Things/API access."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import build_plugin


class PluginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.marketplace = Path(self.temp.name) / "marketplace with spaces"
        self.plugin = build_plugin.build(self.marketplace)

    def read(self, relative):
        return json.loads((self.plugin / relative).read_text())

    def test_metadata_has_read_capability_and_no_hooks_or_secrets(self):
        manifest = self.read("plugin.json")
        overlay = manifest["extensions"]["com.openai"]
        self.assertTrue(overlay["requires_local_executor"])
        self.assertEqual(overlay["interface"]["capabilities"], ["Read"])
        self.assertEqual(overlay["interface"]["displayName"], 'My Things Connection')
        self.assertEqual(manifest['name'], 'things-readonly')
        self.assertEqual(manifest['version'], '2.2.0')
        self.assertNotIn("hooks", overlay)
        self.assertNotIn("onboardingSkill", overlay)
        self.assertNotIn("skills", manifest)
        config = self.read("mcp.json")
        self.assertEqual(config, self.read(".mcp.json"))
        entry = config["mcpServers"]["things"]
        self.assertEqual(entry, {"type": "stdio", "command": "/bin/sh",
            "args": ["${PLUGIN_ROOT}/scripts/launch_mcp.sh"], "cwd": "${PLUGIN_ROOT}"})
        self.assertNotIn("env", entry)
        self.assertNotIn("url", entry)

    def test_marketplace_relative_path_and_compatibility_reference(self):
        catalog = json.loads((self.marketplace / ".agents/plugins/marketplace.json").read_text())
        entry = catalog["plugins"][0]
        self.assertEqual((self.marketplace / entry["source"]["path"]).resolve(), self.plugin.resolve())
        self.assertEqual(entry["policy"]["installation"], "AVAILABLE")
        reference = self.read(".codex-plugin/plugin.json")["mcpServers"]
        self.assertTrue((self.plugin / reference).is_file())

    def test_only_explicit_source_files_are_distributed(self):
        expected = {"server/" + name for name in build_plugin.SOURCES}
        expected |= {"docs/" + name for name in build_plugin.DOCS}
        expected |= {"plugin.json", "mcp.json", ".mcp.json", ".codex-plugin/plugin.json", "scripts/launch_mcp.sh", "README.md"}
        actual = {str(path.relative_to(self.plugin)) for path in self.plugin.rglob("*") if path.is_file()}
        self.assertEqual(actual, expected)
        for name in build_plugin.SOURCES:
            self.assertEqual((self.plugin / "server" / name).read_bytes(), (build_plugin.ROOT / name).read_bytes())

    def test_never_overwrites_existing_build(self):
        original = (self.plugin / "plugin.json").read_bytes()
        with self.assertRaises(FileExistsError):
            build_plugin.build(self.marketplace)
        self.assertEqual((self.plugin / "plugin.json").read_bytes(), original)

    def test_relocated_cached_copy_runs_discovery_with_no_host_runtime(self):
        cached = Path(self.temp.name) / "plugin cache" / "installed plugin"
        shutil.copytree(self.plugin, cached)
        shutil.rmtree(self.marketplace)
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
        result = subprocess.run(["/bin/sh", str(cached / "scripts/launch_mcp.sh")],
            cwd=self.temp.name, input="".join(json.dumps(request) + "\n" for request in requests),
            text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        messages = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["result"]["serverInfo"]["name"], "things-readonly")
        tools = messages[1]["result"]["tools"]
        self.assertEqual([tool["name"] for tool in tools], ["read_things_page"])
        self.assertTrue(tools[0]["annotations"]["readOnlyHint"])
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
