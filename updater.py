"""GitHub Releases update checks and safe EXE downloads."""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from app_paths import app_data_path, ensure_app_dirs
from app_version import APP_RELEASES_API_URL, APP_REPOSITORY_URL, APP_VERSION


WINDOWS_EXE_ASSET = "CodexHistoryManager.exe"
MAX_UPDATE_DOWNLOAD_BYTES = 300 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 20
USER_AGENT = "Codex-Enhance-Manager-Updater/1.0"


@dataclass
class ReleaseAsset:
    name: str
    url: str
    size: int
    digest: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "url": self.url,
            "size": self.size,
            "size_mb": round(self.size / (1024 * 1024), 2) if self.size else 0,
            "digest": self.digest,
        }


class UpdateManager:
    """Check GitHub Releases and download update assets to local storage."""

    def __init__(
        self,
        current_version: str = APP_VERSION,
        releases_api_url: str = APP_RELEASES_API_URL,
        repository_url: str = APP_REPOSITORY_URL,
        download_dir: Optional[Path] = None,
        opener: Any = None,
    ):
        self.current_version = normalize_version_tag(current_version)
        self.releases_api_url = releases_api_url.rstrip("/")
        self.repository_url = repository_url.rstrip("/")
        self.download_dir = download_dir or app_data_path("updates")
        self.opener = opener or urllib.request.urlopen

    def check_latest(self, include_prerelease: bool = False) -> Dict[str, Any]:
        """Return latest release metadata and whether an update is available."""
        releases = self._fetch_releases()
        release = self._select_latest_release(releases, include_prerelease=include_prerelease)
        if not release:
            return {
                "success": True,
                "current_version": self.current_version,
                "update_available": False,
                "message": "No release found.",
                "repository_url": self.repository_url,
                "release": None,
            }

        latest_version = normalize_version_tag(str(release.get("tag_name") or release.get("name") or ""))
        asset = self._find_windows_asset(release)
        update_available = compare_versions(latest_version, self.current_version) > 0
        return {
            "success": True,
            "current_version": self.current_version,
            "latest_version": latest_version,
            "update_available": update_available,
            "repository_url": self.repository_url,
            "release": {
                "name": release.get("name") or latest_version,
                "tag_name": latest_version,
                "url": release.get("html_url") or f"{self.repository_url}/releases/tag/{latest_version}",
                "published_at": release.get("published_at") or "",
                "prerelease": bool(release.get("prerelease")),
                "body": release.get("body") or "",
                "asset": asset.to_dict() if asset else None,
            },
        }

    def download_latest(self, include_prerelease: bool = False) -> Dict[str, Any]:
        """Download the latest Windows EXE asset without replacing the running app."""
        check = self.check_latest(include_prerelease=include_prerelease)
        release = check.get("release") or {}
        asset_data = release.get("asset") or {}
        if not asset_data.get("url"):
            return {
                "success": False,
                "error": "No Windows EXE asset found in the latest release.",
                "check": check,
            }

        ensure_app_dirs([self.download_dir])
        tag = safe_path_part(release.get("tag_name") or check.get("latest_version") or "latest")
        target_dir = self.download_dir / tag
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / WINDOWS_EXE_ASSET
        downloaded = self._download_file(asset_data["url"], target_path, expected_size=int(asset_data.get("size") or 0))
        return {
            "success": True,
            "downloaded_path": str(downloaded),
            "restart_required": True,
            "manual_install_required": True,
            "message": "Download finished. Close the app and run the downloaded EXE to update.",
            "check": check,
        }

    def _fetch_releases(self) -> list[Dict[str, Any]]:
        request = urllib.request.Request(
            self.releases_api_url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": USER_AGENT,
            },
        )
        with self.opener(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
            payload = response.read().decode("utf-8", errors="replace")
        data = json.loads(payload or "[]")
        if isinstance(data, dict):
            data = [data]
        return [item for item in data if isinstance(item, dict)]

    @staticmethod
    def _select_latest_release(releases: Iterable[Dict[str, Any]], include_prerelease: bool = False) -> Optional[Dict[str, Any]]:
        for release in releases:
            if release.get("draft"):
                continue
            if release.get("prerelease") and not include_prerelease:
                continue
            return release
        return None

    @staticmethod
    def _find_windows_asset(release: Dict[str, Any]) -> Optional[ReleaseAsset]:
        assets = release.get("assets") if isinstance(release.get("assets"), list) else []
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            name = str(asset.get("name") or "")
            if name.lower() != WINDOWS_EXE_ASSET.lower():
                continue
            url = str(asset.get("browser_download_url") or "")
            if not url:
                continue
            return ReleaseAsset(
                name=name,
                url=url,
                size=int(asset.get("size") or 0),
                digest=str(asset.get("digest") or ""),
            )
        return None

    def _download_file(self, url: str, target_path: Path, expected_size: int = 0) -> Path:
        if expected_size and expected_size > MAX_UPDATE_DOWNLOAD_BYTES:
            raise ValueError("Update asset is too large.")
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        tmp_fd, tmp_name = tempfile.mkstemp(prefix=target_path.name + ".", suffix=".tmp", dir=str(target_path.parent))
        os.close(tmp_fd)
        tmp_path = Path(tmp_name)
        total = 0
        try:
            with self.opener(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response, open(tmp_path, "wb") as out:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_UPDATE_DOWNLOAD_BYTES:
                        raise ValueError("Update asset is too large.")
                    out.write(chunk)
            if expected_size and total != expected_size:
                raise ValueError(f"Downloaded size mismatch: expected {expected_size}, got {total}.")
            shutil.move(str(tmp_path), str(target_path))
            return target_path
        finally:
            try:
                Path(tmp_name).unlink(missing_ok=True)
            except Exception:
                pass


def normalize_version_tag(value: str) -> str:
    text = str(value or "").strip()
    return text if text.startswith("v") else f"v{text or '0.0.0'}"


def compare_versions(left: str, right: str) -> int:
    left_parts = version_sort_key(left)
    right_parts = version_sort_key(right)
    if left_parts > right_parts:
        return 1
    if left_parts < right_parts:
        return -1
    return 0


def version_sort_key(value: str) -> tuple[int, int, int, tuple[int, ...]]:
    text = normalize_version_tag(value).lstrip("vV")
    numbers = [int(part) for part in re.findall(r"\d+", text)]
    padded = (numbers + [0, 0, 0])[:3]
    extras = tuple(numbers[3:])
    return padded[0], padded[1], padded[2], extras


def safe_path_part(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip())
    return cleaned.strip(".-") or "latest"
