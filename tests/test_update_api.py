import unittest
from unittest.mock import patch


class UpdateApiTest(unittest.TestCase):
    def test_update_check_endpoint_uses_update_manager(self):
        with patch("app.UpdateManager") as MockUpdateManager:
            manager = MockUpdateManager.return_value
            manager.check_latest.return_value = {
                "success": True,
                "current_version": "v2.2.2",
                "latest_version": "v2.2.3",
                "update_available": True,
            }

            from app import create_app

            flask_app = create_app()
            flask_app.config["TESTING"] = True
            response = flask_app.test_client().get("/api/updates/check")

            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.get_json()["update_available"])
            manager.check_latest.assert_called_once()

    def test_update_download_endpoint_returns_download_path(self):
        with patch("app.UpdateManager") as MockUpdateManager:
            manager = MockUpdateManager.return_value
            manager.download_latest.return_value = {
                "success": True,
                "downloaded_path": "C:/Updates/CodexHistoryManager.exe",
                "restart_required": True,
            }

            from app import create_app

            flask_app = create_app()
            flask_app.config["TESTING"] = True
            response = flask_app.test_client().post("/api/updates/download", json={})

            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertTrue(payload["success"])
            self.assertTrue(payload["restart_required"])
            manager.download_latest.assert_called_once()


if __name__ == "__main__":
    unittest.main()
