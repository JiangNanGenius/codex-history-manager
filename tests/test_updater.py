import json
import tempfile
import unittest
from pathlib import Path

from updater import UpdateManager, compare_versions, safe_path_part


class FakeResponse:
    def __init__(self, body: bytes):
        self.body = body
        self.offset = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, size=-1):
        if size is None or size < 0:
            chunk = self.body[self.offset:]
            self.offset = len(self.body)
            return chunk
        chunk = self.body[self.offset:self.offset + size]
        self.offset += len(chunk)
        return chunk


class FakeOpener:
    def __init__(self, releases, downloads=None):
        self.releases = releases
        self.downloads = downloads or {}
        self.urls = []

    def __call__(self, request, timeout=0):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        self.urls.append(url)
        if url.endswith("/releases"):
            return FakeResponse(json.dumps(self.releases).encode("utf-8"))
        return FakeResponse(self.downloads[url])


class UpdateManagerTest(unittest.TestCase):
    def test_compare_versions_handles_v_prefix(self):
        self.assertGreater(compare_versions("v2.3.0", "v2.2.9"), 0)
        self.assertEqual(compare_versions("2.2.1", "v2.2.1"), 0)
        self.assertLess(compare_versions("v2.1.9", "v2.2.0"), 0)

    def test_check_latest_reports_update_and_exe_asset(self):
        opener = FakeOpener([
            {
                "tag_name": "v2.2.3",
                "name": "Codex History Manager v2.2.3",
                "html_url": "https://github.example/release",
                "published_at": "2026-06-08T00:00:00Z",
                "assets": [
                    {
                        "name": "CodexHistoryManager.exe",
                        "browser_download_url": "https://downloads.example/app.exe",
                        "size": 1234,
                        "digest": "sha256:test",
                    }
                ],
            }
        ])
        manager = UpdateManager(
            current_version="v2.2.2",
            releases_api_url="https://api.example/releases",
            repository_url="https://github.example/repo",
            opener=opener,
        )

        result = manager.check_latest()

        self.assertTrue(result["success"])
        self.assertTrue(result["update_available"])
        self.assertEqual(result["latest_version"], "v2.2.3")
        self.assertEqual(result["release"]["asset"]["name"], "CodexHistoryManager.exe")

    def test_check_latest_skips_prerelease_by_default(self):
        opener = FakeOpener([
            {"tag_name": "v3.0.0-beta", "prerelease": True, "assets": []},
            {"tag_name": "v2.2.2", "prerelease": False, "assets": []},
        ])
        manager = UpdateManager(current_version="v2.2.1", releases_api_url="https://api.example/releases", opener=opener)

        result = manager.check_latest()

        self.assertEqual(result["latest_version"], "v2.2.2")

    def test_download_latest_writes_exe_under_version_folder(self):
        download_url = "https://downloads.example/CodexHistoryManager.exe"
        opener = FakeOpener(
            [
                {
                    "tag_name": "v2.2.3",
                    "assets": [
                        {
                            "name": "CodexHistoryManager.exe",
                            "browser_download_url": download_url,
                            "size": 7,
                        }
                    ],
                }
            ],
            {download_url: b"EXE-DAT"},
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = UpdateManager(
                current_version="v2.2.2",
                releases_api_url="https://api.example/releases",
                download_dir=Path(tmpdir),
                opener=opener,
            )

            result = manager.download_latest()

            self.assertTrue(result["success"])
            path = Path(result["downloaded_path"])
            self.assertEqual(path.name, "CodexHistoryManager.exe")
            self.assertEqual(path.parent.name, "v2.2.3")
            self.assertEqual(path.read_bytes(), b"EXE-DAT")

    def test_safe_path_part_removes_unsafe_characters(self):
        self.assertEqual(safe_path_part("v2.2.3 / latest"), "v2.2.3-latest")


if __name__ == "__main__":
    unittest.main()
