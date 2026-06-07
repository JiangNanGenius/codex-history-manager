import unittest
from unittest.mock import MagicMock, patch


class ProviderHealthApiTest(unittest.TestCase):
    def _app_with_health_result(self, result):
        with (
            patch("app.Config") as MockConfig,
            patch("app.CodexDB"),
            patch("app.BackupManager"),
            patch("app.TokenStats"),
            patch("app.ProviderRegistry") as MockProviderRegistry,
            patch("app.AutoApprovalModelReviewer"),
            patch("app.LocalProxyServer"),
            patch("app.AMRRegistry"),
            patch("app.QuotaManager"),
            patch("app.StartupManager"),
            patch("app.DiagnosticsCollector") as MockDiagnosticsCollector,
        ):
            config = MagicMock()
            config.get.side_effect = lambda key, default=None: default
            config.get_all.return_value = {}
            config.is_write_locked.return_value = False
            config.write_lock_reason.return_value = ""
            MockConfig.return_value = config

            registry = MagicMock()
            MockProviderRegistry.return_value = registry

            diagnostics = MagicMock()
            diagnostics.check_provider_connectivity.return_value = result
            MockDiagnosticsCollector.return_value = diagnostics

            from app import create_app

            flask_app = create_app()
            flask_app.config["TESTING"] = True
            return flask_app, diagnostics

    def test_provider_health_check_returns_structured_failure_with_200(self):
        app, diagnostics = self._app_with_health_result({
            "success": False,
            "reachable": False,
            "provider_id": "p1",
            "error": "Could not connect to any tested endpoint.",
        })

        response = app.test_client().post("/api/providers/p1/health-check")

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertFalse(data["success"])
        self.assertFalse(data["reachable"])
        self.assertEqual(data["provider_id"], "p1")
        diagnostics.check_provider_connectivity.assert_called_once_with("p1")

    def test_diagnostics_provider_test_preserves_error_status(self):
        app, diagnostics = self._app_with_health_result({
            "success": False,
            "reachable": False,
            "provider_id": "p1",
            "error": "Could not connect to any tested endpoint.",
        })

        response = app.test_client().post("/api/diagnostics/test-provider/p1")

        self.assertEqual(response.status_code, 503)
        data = response.get_json()
        self.assertFalse(data["success"])
        diagnostics.check_provider_connectivity.assert_called_once_with("p1")


if __name__ == "__main__":
    unittest.main()
