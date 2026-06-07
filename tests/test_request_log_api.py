import unittest
from unittest.mock import MagicMock, patch


class RequestLogApiTest(unittest.TestCase):
    def _app(self, store):
        with (
            patch("app.Config") as MockConfig,
            patch("app.CodexDB"),
            patch("app.BackupManager"),
            patch("app.TokenStats"),
            patch("app.ProviderRegistry"),
            patch("app.AutoApprovalModelReviewer"),
            patch("app.LocalProxyServer"),
            patch("app.AMRRegistry"),
            patch("app.QuotaManager"),
            patch("app.StartupManager"),
            patch("app.DiagnosticsCollector"),
        ):
            config = MagicMock()
            config.get.side_effect = lambda key, default=None: default
            config.get_all.return_value = {}
            config.is_write_locked.return_value = False
            config.write_lock_reason.return_value = ""
            MockConfig.return_value = config

            from app import create_app

            flask_app = create_app()
            flask_app.config["TESTING"] = True
            return flask_app

    def test_request_logs_api_passes_media_filters(self):
        store = MagicMock()
        store.read_entries.return_value = {"entries": [], "count": 0}
        app = self._app(store)

        with patch("app.RequestLogStore", return_value=store):
            response = app.test_client().get(
                "/api/request-logs?limit=25&media_kind=image&error_type=media_adapter_required&success=false"
            )

        self.assertEqual(response.status_code, 200)
        store.read_entries.assert_called_once_with(
            limit=25,
            provider_id="",
            endpoint="",
            media_kind="image",
            error_type="media_adapter_required",
            since="",
            success=False,
        )


if __name__ == "__main__":
    unittest.main()
