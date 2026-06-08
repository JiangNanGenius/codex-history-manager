"""
Optional Windows desktop shortcuts for the portable desktop app.

The app is distributed as a green/portable EXE, so shortcut creation is a
user-triggered convenience action rather than an installer side effect.
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
START_CODEX_ARG = "--start-codex"
SHORTCUT_KIND_NORMAL = "normal"
SHORTCUT_KIND_START_CODEX = "start_codex"
SHORTCUT_KINDS = {SHORTCUT_KIND_NORMAL, SHORTCUT_KIND_START_CODEX}
NORMAL_SHORTCUT_NAME = "Codex Enhance Manager.lnk"
START_CODEX_SHORTCUT_NAME = "Codex Enhance Manager - Start Codex.lnk"


@dataclass
class ShortcutCommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def _default_runner(args: List[str]) -> ShortcutCommandResult:
    completed = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
        creationflags=CREATE_NO_WINDOW,
    )
    return ShortcutCommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


def _default_platform_name() -> str:
    if os.name == "nt":
        return "Windows"
    return platform.system()


def _desktop_dir() -> Path:
    candidates = []
    one_drive = os.environ.get("OneDrive")
    if one_drive:
        candidates.extend([Path(one_drive) / "Desktop", Path(one_drive) / "\u684c\u9762"])
    candidates.extend([
        Path.home() / "OneDrive" / "Desktop",
        Path.home() / "OneDrive" / "\u684c\u9762",
        Path.home() / "Desktop",
        Path.home() / "\u684c\u9762",
    ])
    for candidate in candidates:
        try:
            if candidate.exists() and candidate.is_dir():
                return candidate
        except OSError:
            continue
    return Path.home() / "Desktop"


def _module_dir() -> Path:
    return Path(__file__).resolve().parent


def _app_icon_path(module_dir: Path) -> str:
    for name in ("icon.ico", "icon.png"):
        path = module_dir / name
        if path.exists():
            return str(path)
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable))
    return ""


def _ps_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _shortcut_script(
    *,
    shortcut_path: Path,
    target_path: str,
    arguments: str,
    working_directory: str,
    icon_location: str,
    description: str,
) -> str:
    lines = [
        "$shell = New-Object -ComObject WScript.Shell",
        f"$shortcut = $shell.CreateShortcut({_ps_literal(str(shortcut_path))})",
        f"$shortcut.TargetPath = {_ps_literal(target_path)}",
        f"$shortcut.Arguments = {_ps_literal(arguments)}",
        f"$shortcut.WorkingDirectory = {_ps_literal(working_directory)}",
        f"$shortcut.Description = {_ps_literal(description)}",
    ]
    if icon_location:
        lines.append(f"$shortcut.IconLocation = {_ps_literal(icon_location)}")
    lines.append("$shortcut.Save()")
    return "; ".join(lines)


class DesktopShortcutManager:
    def __init__(
        self,
        *,
        desktop_dir: Optional[Path] = None,
        runner: Optional[Callable[[List[str]], Any]] = None,
        platform_name: Optional[str] = None,
        module_dir: Optional[Path] = None,
    ):
        self._desktop_dir = Path(desktop_dir) if desktop_dir else None
        self._runner = runner or _default_runner
        self._platform_name = platform_name or _default_platform_name()
        self._module_dir = Path(module_dir) if module_dir else _module_dir()

    @property
    def is_windows(self) -> bool:
        return self._platform_name.lower().startswith("win")

    def desktop_dir(self) -> Path:
        return self._desktop_dir or _desktop_dir()

    def resolve_target_and_arguments(self, extra_args: Optional[List[str]] = None) -> tuple[str, str, str]:
        extra_args = list(extra_args or [])
        if getattr(sys, "frozen", False):
            target = sys.executable
            base_args: List[str] = []
            workdir = str(Path(sys.executable).resolve().parent)
        else:
            target = sys.executable
            base_args = [str(self._module_dir / "main.py")]
            workdir = str(self._module_dir)
        args = subprocess.list2cmdline(base_args + extra_args)
        return target, args, workdir

    def shortcut_specs(self, *, normal: bool = True, start_codex: bool = True) -> List[Dict[str, Any]]:
        specs: List[Dict[str, Any]] = []
        desktop = self.desktop_dir()
        if normal:
            target, args, workdir = self.resolve_target_and_arguments([])
            specs.append({
                "kind": SHORTCUT_KIND_NORMAL,
                "path": str(desktop / NORMAL_SHORTCUT_NAME),
                "target_path": target,
                "arguments": args,
                "working_directory": workdir,
                "description": "Open Codex Enhance Manager.",
            })
        if start_codex:
            target, args, workdir = self.resolve_target_and_arguments([START_CODEX_ARG])
            specs.append({
                "kind": SHORTCUT_KIND_START_CODEX,
                "path": str(desktop / START_CODEX_SHORTCUT_NAME),
                "target_path": target,
                "arguments": args,
                "working_directory": workdir,
                "description": "Start Codex through Codex Enhance Manager.",
            })
        return specs

    def create_shortcuts(self, *, normal: bool = True, start_codex: bool = True) -> Dict[str, Any]:
        specs = self.shortcut_specs(normal=normal, start_codex=start_codex)
        if not specs:
            return {"success": False, "supported": self.is_windows, "error": "No shortcut kind selected.", "shortcuts": []}
        if not self.is_windows:
            return {
                "success": False,
                "supported": False,
                "error": "Desktop shortcut creation is only supported on Windows.",
                "shortcuts": specs,
            }

        desktop = self.desktop_dir()
        desktop.mkdir(parents=True, exist_ok=True)
        icon = _app_icon_path(self._module_dir)
        results = []
        for spec in specs:
            shortcut_path = Path(spec["path"])
            script = _shortcut_script(
                shortcut_path=shortcut_path,
                target_path=spec["target_path"],
                arguments=spec["arguments"],
                working_directory=spec["working_directory"],
                icon_location=icon,
                description=spec["description"],
            )
            command = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script]
            completed = self._runner(command)
            returncode = int(getattr(completed, "returncode", 1))
            results.append({
                **spec,
                "success": returncode == 0 and shortcut_path.exists(),
                "returncode": returncode,
                "stdout": str(getattr(completed, "stdout", "") or ""),
                "stderr": str(getattr(completed, "stderr", "") or ""),
            })

        return {
            "success": all(item.get("success") for item in results),
            "supported": True,
            "desktop_dir": str(desktop),
            "shortcuts": results,
        }
