"""
startup_manager.py - Windows startup and elevation integration.

The module keeps OS mutation behind explicit confirmation. Status and preview
operations are read-only. Tests inject a fake command runner and temp startup
folder so no real Windows startup entry or scheduled task is created.
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional


STARTUP_CONFIRMATION = "MODIFY_WINDOWS_STARTUP"
STARTUP_MODES = {"disabled", "startup_folder", "scheduled_task_highest"}
PACKAGED_RELEASE_EXE_NAME = "CodexHistoryManager.exe"
STARTUP_CONFIG_KEYS = (
    "startup_enabled",
    "startup_mode",
    "startup_auto_elevate",
    "startup_task_name",
    "startup_shortcut_name",
    "startup_target_path",
    "startup_arguments",
)


@dataclass
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def _default_runner(args: List[str]) -> CommandResult:
    completed = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


class StartupManager:
    def __init__(
        self,
        *,
        app_name: str = "Codex Enhance Manager",
        startup_dir: Optional[Path] = None,
        runner: Optional[Callable[[List[str]], Any]] = None,
        platform_name: Optional[str] = None,
        module_dir: Optional[Path] = None,
    ):
        self.app_name = app_name
        self._startup_dir = startup_dir
        self._runner = runner or _default_runner
        self._platform_name = platform_name or platform.system()
        self._module_dir = Path(module_dir) if module_dir else Path(__file__).resolve().parent

    @property
    def is_windows(self) -> bool:
        return self._platform_name.lower().startswith("win")

    def normalize_settings(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        raw = settings or {}
        enabled = bool(raw.get("startup_enabled", False))
        mode = str(raw.get("startup_mode") or "disabled").strip() or "disabled"
        auto_elevate = bool(raw.get("startup_auto_elevate", False))

        if mode not in STARTUP_MODES:
            mode = "disabled"
        if auto_elevate and enabled:
            mode = "scheduled_task_highest"
        elif not enabled:
            mode = "disabled"
        elif mode == "disabled":
            mode = "startup_folder"

        target, arguments = self.resolve_target_and_arguments(raw)
        return {
            "startup_enabled": mode != "disabled",
            "startup_mode": mode,
            "startup_auto_elevate": mode == "scheduled_task_highest",
            "startup_task_name": self._safe_task_name(raw.get("startup_task_name")),
            "startup_shortcut_name": self._safe_cmd_name(raw.get("startup_shortcut_name")),
            "startup_target_path": target,
            "startup_arguments": arguments,
        }

    def resolve_target_and_arguments(self, settings: Dict[str, Any]) -> tuple[str, str]:
        target = str(settings.get("startup_target_path") or "").strip()
        arguments = str(settings.get("startup_arguments") or "").strip()
        if target:
            return str(Path(target).expanduser()), arguments

        if getattr(sys, "frozen", False):
            return sys.executable, arguments

        main_py = self._module_dir / "main.py"
        default_args = subprocess.list2cmdline([str(main_py)])
        return sys.executable, arguments or default_args

    def status(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        normalized = self.normalize_settings(settings)
        startup_path = self.startup_entry_path(normalized)
        task_name = normalized["startup_task_name"]
        task_status = self._task_exists(task_name) if self.is_windows else {"exists": False, "checked": False}
        target_diagnostics = self.target_diagnostics(
            normalized["startup_target_path"],
            normalized["startup_arguments"],
        )
        return {
            "supported": self.is_windows,
            "platform": self._platform_name,
            "configured": normalized,
            "target_diagnostics": target_diagnostics,
            "startup_folder": str(self.startup_dir()),
            "startup_entry_path": str(startup_path),
            "startup_entry_exists": startup_path.exists(),
            "task_name": task_name,
            "scheduled_task_exists": bool(task_status.get("exists")),
            "scheduled_task_checked": bool(task_status.get("checked")),
            "scheduled_task_error": task_status.get("error", ""),
            "required_confirmation": STARTUP_CONFIRMATION,
        }

    def preview(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        normalized = self.normalize_settings(settings)
        mode = normalized["startup_mode"]
        target = normalized["startup_target_path"]
        arguments = normalized["startup_arguments"]
        command_line = self.command_line(target, arguments)
        target_diagnostics = self.target_diagnostics(target, arguments)
        startup_path = self.startup_entry_path(normalized)
        task_name = normalized["startup_task_name"]
        actions: List[Dict[str, Any]] = []

        if mode == "disabled":
            actions.append({"kind": "startup_entry", "action": "remove", "path": str(startup_path)})
            actions.append({"kind": "scheduled_task", "action": "delete", "task_name": task_name})
        elif mode == "startup_folder":
            actions.append({"kind": "startup_entry", "action": "write_cmd", "path": str(startup_path), "command_line": command_line})
            actions.append({"kind": "scheduled_task", "action": "delete", "task_name": task_name})
        elif mode == "scheduled_task_highest":
            actions.append({"kind": "startup_entry", "action": "remove", "path": str(startup_path)})
            actions.append({"kind": "scheduled_task", "action": "create", "task_name": task_name, "argv": self.create_task_argv(task_name, command_line)})

        notes = [
            "Read-only status and preview do not mutate Windows startup state.",
            "Apply/remove requires typed confirmation and should be manually tested outside this Codex session.",
        ]
        if mode == "scheduled_task_highest":
            notes.append("Administrator startup uses Windows Task Scheduler with run level HIGHEST.")
            notes.append("Windows may require an administrator confirmation when creating or changing this task.")
        elif mode == "startup_folder":
            notes.append("Startup-folder mode runs for the current user at logon and cannot request administrator privileges.")
        if mode != "disabled":
            notes.extend(target_diagnostics.get("warnings", []))

        return {
            "supported": self.is_windows,
            "mode": mode,
            "enabled": mode != "disabled",
            "auto_elevate": normalized["startup_auto_elevate"],
            "elevation_method": "task_scheduler_highest" if mode == "scheduled_task_highest" else "none",
            "startup_folder_supports_elevation": False,
            "target": target,
            "arguments": arguments,
            "target_diagnostics": target_diagnostics,
            "command_line": command_line,
            "startup_folder": str(self.startup_dir()),
            "startup_entry_path": str(startup_path),
            "task_name": task_name,
            "required_confirmation": STARTUP_CONFIRMATION,
            "requires_manual_confirmation": True,
            "actions": actions,
            "rollback": [
                {"kind": "startup_entry", "action": "remove", "path": str(startup_path)},
                {"kind": "scheduled_task", "action": "delete", "task_name": task_name},
            ],
            "notes": notes,
        }

    def apply(self, settings: Dict[str, Any], confirmation: str = "") -> Dict[str, Any]:
        if confirmation != STARTUP_CONFIRMATION:
            return self._confirmation_error()
        preview = self.preview(settings)
        if not self.is_windows:
            return {
                "success": False,
                "error": "Windows startup integration is only supported on Windows.",
                "preview": preview,
            }

        results = self._execute_actions(preview["actions"])
        return {
            "success": all(item.get("success") for item in results),
            "results": results,
            "preview": preview,
        }

    def remove(self, settings: Dict[str, Any], confirmation: str = "") -> Dict[str, Any]:
        if confirmation != STARTUP_CONFIRMATION:
            return self._confirmation_error()
        normalized = self.normalize_settings({**settings, "startup_enabled": False, "startup_mode": "disabled"})
        preview = self.preview(normalized)
        if not self.is_windows:
            return {
                "success": False,
                "error": "Windows startup integration is only supported on Windows.",
                "preview": preview,
            }
        results = self._execute_actions(preview["actions"])
        return {
            "success": all(item.get("success") for item in results),
            "results": results,
            "preview": preview,
        }

    def startup_dir(self) -> Path:
        if self._startup_dir is not None:
            return Path(self._startup_dir)
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        return Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"

    def startup_entry_path(self, settings: Dict[str, Any]) -> Path:
        name = settings.get("startup_shortcut_name") or "CodexEnhanceManager.cmd"
        return self.startup_dir() / self._safe_cmd_name(name)

    def command_line(self, target: str, arguments: str = "") -> str:
        command = subprocess.list2cmdline([target])
        args = str(arguments or "").strip()
        return f"{command} {args}".strip()

    def target_diagnostics(self, target: str, arguments: str = "") -> Dict[str, Any]:
        target_text = str(target or "").strip()
        path = Path(target_text).expanduser() if target_text else None
        name = path.name if path is not None else ""
        suffix = path.suffix.lower() if path is not None else ""
        exists = bool(path and path.exists())
        is_exe = suffix == ".exe"
        name_matches_release = name.lower() == PACKAGED_RELEASE_EXE_NAME.lower()
        lower_name = name.lower()
        is_python_runtime = lower_name in {"python.exe", "pythonw.exe"} or lower_name.startswith("python")
        warnings: List[str] = []

        if not target_text:
            warnings.append("Startup target is empty; apply will use the detected runtime target.")
        elif not exists:
            warnings.append("Startup target does not exist yet; verify the packaged EXE path before applying.")
        if target_text and not is_exe:
            warnings.append("Startup target is not a Windows EXE; packaged release startup verification will not be covered.")
        elif is_python_runtime:
            warnings.append("Startup target is a Python runtime; use the packaged EXE for release/manual startup verification.")
        elif is_exe and not name_matches_release:
            warnings.append(f"Startup target EXE name differs from the standard release asset {PACKAGED_RELEASE_EXE_NAME}.")

        return {
            "target": str(path) if path is not None else "",
            "arguments": str(arguments or "").strip(),
            "target_exists": exists,
            "target_is_exe": is_exe,
            "target_name": name,
            "expected_release_exe_name": PACKAGED_RELEASE_EXE_NAME,
            "target_matches_release_exe_name": name_matches_release,
            "target_is_python_runtime": is_python_runtime,
            "release_startup_ready": exists and is_exe and not is_python_runtime,
            "warnings": warnings,
            "warning_count": len(warnings),
        }

    def create_task_argv(self, task_name: str, command_line: str) -> List[str]:
        return [
            "schtasks.exe",
            "/Create",
            "/TN",
            task_name,
            "/TR",
            command_line,
            "/SC",
            "ONLOGON",
            "/RL",
            "HIGHEST",
            "/F",
        ]

    def delete_task_argv(self, task_name: str) -> List[str]:
        return ["schtasks.exe", "/Delete", "/TN", task_name, "/F"]

    def query_task_argv(self, task_name: str) -> List[str]:
        return ["schtasks.exe", "/Query", "/TN", task_name, "/FO", "LIST"]

    def _execute_actions(self, actions: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        results = []
        for action in actions:
            kind = action.get("kind")
            operation = action.get("action")
            if kind == "startup_entry" and operation == "write_cmd":
                results.append(self._write_startup_cmd(Path(action["path"]), str(action.get("command_line") or "")))
            elif kind == "startup_entry" and operation == "remove":
                results.append(self._remove_file(Path(action["path"]), "startup_entry"))
            elif kind == "scheduled_task" and operation == "create":
                results.append(self._create_task(str(action["task_name"]), list(action["argv"])))
            elif kind == "scheduled_task" and operation == "delete":
                results.append(self._delete_task(str(action["task_name"])))
            else:
                results.append({"success": False, "kind": kind, "action": operation, "error": "Unknown startup action"})
        return results

    def _write_startup_cmd(self, path: Path, command_line: str) -> Dict[str, Any]:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"@echo off\r\nstart \"\" {command_line}\r\n", encoding="utf-8")
            return {"success": True, "kind": "startup_entry", "action": "write_cmd", "path": str(path)}
        except OSError as exc:
            return {"success": False, "kind": "startup_entry", "action": "write_cmd", "path": str(path), "error": str(exc)}

    def _remove_file(self, path: Path, kind: str) -> Dict[str, Any]:
        try:
            if not path.exists():
                return {"success": True, "kind": kind, "action": "remove", "path": str(path), "skipped": True}
            path.unlink()
            return {"success": True, "kind": kind, "action": "remove", "path": str(path)}
        except OSError as exc:
            return {"success": False, "kind": kind, "action": "remove", "path": str(path), "error": str(exc)}

    def _create_task(self, task_name: str, argv: List[str]) -> Dict[str, Any]:
        result = self._coerce_result(self._runner(argv))
        return {
            "success": result.returncode == 0,
            "kind": "scheduled_task",
            "action": "create",
            "task_name": task_name,
            "argv": argv,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    def _delete_task(self, task_name: str) -> Dict[str, Any]:
        exists = self._task_exists(task_name)
        if not exists.get("exists"):
            return {
                "success": True,
                "kind": "scheduled_task",
                "action": "delete",
                "task_name": task_name,
                "skipped": True,
                "error": exists.get("error", ""),
            }
        argv = self.delete_task_argv(task_name)
        result = self._coerce_result(self._runner(argv))
        return {
            "success": result.returncode == 0,
            "kind": "scheduled_task",
            "action": "delete",
            "task_name": task_name,
            "argv": argv,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    def _task_exists(self, task_name: str) -> Dict[str, Any]:
        if not self.is_windows:
            return {"exists": False, "checked": False}
        try:
            result = self._coerce_result(self._runner(self.query_task_argv(task_name)))
            return {
                "exists": result.returncode == 0,
                "checked": True,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        except Exception as exc:
            return {"exists": False, "checked": True, "error": str(exc)}

    def _coerce_result(self, value: Any) -> CommandResult:
        if isinstance(value, CommandResult):
            return value
        return CommandResult(
            returncode=int(getattr(value, "returncode", 1)),
            stdout=str(getattr(value, "stdout", "") or ""),
            stderr=str(getattr(value, "stderr", "") or ""),
        )

    def _confirmation_error(self) -> Dict[str, Any]:
        return {
            "success": False,
            "error": "Startup mutation confirmation required.",
            "required_confirmation": STARTUP_CONFIRMATION,
        }

    def _safe_cmd_name(self, value: Any) -> str:
        raw = Path(str(value or "CodexEnhanceManager.cmd").strip()).name
        if not raw:
            raw = "CodexEnhanceManager.cmd"
        cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in raw)
        if not cleaned.lower().endswith(".cmd"):
            cleaned += ".cmd"
        return cleaned

    def _safe_task_name(self, value: Any) -> str:
        raw = str(value or "CodexEnhanceManager").strip()
        cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", " ", "."} else "_" for ch in raw)
        cleaned = " ".join(cleaned.split())
        return cleaned or "CodexEnhanceManager"
