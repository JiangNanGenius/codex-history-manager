import unittest
from unittest.mock import MagicMock, patch

from codex_injector import _handle_bridge_request, _parse_ws_url, build_injection_script


class CodexInjectorTest(unittest.TestCase):
    def test_build_injection_script_contains_menu_and_backend_status(self):
        script = build_injection_script("http://127.0.0.1:51234")

        self.assertIn("codex-enhance-manager-menu", script)
        self.assertIn("Codex Enhance", script)
        self.assertIn("codex-enhance-manager-v3", script)
        self.assertIn("bottom: 16px", script)
        self.assertIn("class=\"cem-launch\"", script)
        self.assertIn("http://127.0.0.1:51234", script)
        self.assertIn("/api/codex-injection/status", script)
        self.assertIn("/api/codex-injection/quick-settings", script)
        self.assertIn("Usage Panel", script)
        self.assertIn("data-cem-version", script)
        self.assertIn('data-cem-stat="tokens"', script)
        self.assertIn('data-cem-refresh', script)
        self.assertIn('data-cem-toggle="codex_injection_enabled"', script)
        self.assertIn('data-cem-toggle="plugin_unlock_enabled"', script)
        self.assertIn("plugin_unlock_forced_off", script)
        self.assertIn("bridgeResult.data || bridgeResult", script)
        self.assertIn("${rootId}", script)

    def test_build_injection_script_contains_marketplace_and_usage_alert_patches(self):
        script = build_injection_script("http://127.0.0.1:51234")

        self.assertIn("hideOfficialUsageAlert", script)
        self.assertIn("data-cem-hidden-usage-alert", script)
        self.assertIn("patchPluginMarketplaceParams", script)
        self.assertIn("delete next.marketplaceKinds", script)
        self.assertIn("forcePluginInstall", script)
        self.assertIn("cem-force-install-unlocked", script)
        self.assertIn("pluginEntryUnlock", script)

    def test_parse_ws_url(self):
        host, port, path = _parse_ws_url("ws://127.0.0.1:51236/devtools/page/abc")

        self.assertEqual(host, "127.0.0.1")
        self.assertEqual(port, 51236)
        self.assertEqual(path, "/devtools/page/abc")

    def test_bridge_request_uses_injected_backend_url(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{"success": true, "backend_url": "http://127.0.0.1:57321"}'

        with patch("codex_injector.urllib.request.urlopen", return_value=response) as urlopen:
            result = _handle_bridge_request(
                {"path": "/api/codex-injection/status", "options": {"method": "GET"}},
                backend_url="http://127.0.0.1:57321",
            )

        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:57321/api/codex-injection/status")
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["backend_url"], "http://127.0.0.1:57321")


if __name__ == "__main__":
    unittest.main()
