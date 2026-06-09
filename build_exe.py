"""
PyInstaller 打包脚本 - 将 Codex History Manager (PyWebView 桌面版) 打包为单文件 EXE
"""
import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
import os
import shutil
from pathlib import Path


def _configure_utf8_stdio() -> None:
    """Keep Windows CI logs from crashing on non-ASCII build messages."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_configure_utf8_stdio()


PROJECT_DIR = Path(__file__).parent
OUTPUT_DIR = PROJECT_DIR / "dist"
DESKTOP_DIR = Path("C:/Users/Public/Desktop")
USER_DESKTOP_DIR = Path.home() / "OneDrive" / "桌面"
if not USER_DESKTOP_DIR.exists():
    USER_DESKTOP_DIR = Path.home() / "Desktop"
EXE_NAME = "CodexHistoryManager.exe"
RELEASE_MANIFEST_NAME = "release-manifest.json"
LAST_COPIED_EXE: Path | None = None

BUILD_DEPENDENCIES = [
    ("pyinstaller", "PyInstaller"),
    ("flask", "flask"),
    ("pywebview", "webview"),
    ("pystray", "pystray"),
    ("Pillow", "PIL"),
]

# 本地项目模块（必须作为 hiddenimports 加入，否则 PyInstaller 检测不到）
LOCAL_MODULES = [
    "anthropic_adapter",
    "app_paths",
    "approval_broker",
    "auto_approval_runtime",
    "auto_detect",
    "backup",
    "capabilities",
    "codex_approval_bridge",
    "codex_config",
    "codex_permissions",
    "codex_rollout_usage",
    "config",
    "costing",
    "currency",
    "db",
    "desktop_shortcuts",
    "diagnostics",
    "domestic_responses",
    "guardrails",
    "main",
    "media_adapters",
    "media_proxy",
    "model_catalog",
    "model_rotation",
    "move_repair",
    "providers",
    "provider_routing",
    "proxy_server",
    "quota",
    "reader",
    "reasoning_policy",
    "request_capabilities",
    "request_logs",
    "responses_adapter",
    "startup_manager",
    "sync",
    "token_stats",
    "app",
    "app_version",
    "amr_registry",
    "updater",
]

# PyWebView 平台相关 hidden imports（Windows 使用 EdgeChromium + WinForms）
WEBVIEW_IMPORTS = [
    "webview",
    "webview.http",
    "webview.guilib",
    "webview.platforms.winforms",
    "webview.platforms.edgechromium",
]

TRAY_IMPORTS = [
    "pystray",
    "PIL",
    "PIL.Image",
    "PIL.ImageDraw",
    "PIL.IcoImagePlugin",
    "PIL.PngImagePlugin",
]

# Flask / Werkzeug 生态 hidden imports
FLASK_IMPORTS = [
    "flask",
    "werkzeug",
    "jinja2",
    "markupsafe",
    "click",
    "itsdangerous",
    "blinker",
]

# 标准库常用 hidden imports（保险起见）
STDLIB_IMPORTS = [
    "sqlite3",
    "json",
    "threading",
    "datetime",
    "os",
    "sys",
    "shutil",
    "zipfile",
    "tempfile",
    "subprocess",
    "pathlib",
    "glob",
    "dataclasses",
    "typing",
    "tomllib",
    "time",
    "calendar",
    "inspect",
    "uuid",
    "html",
    "http",
    "socketserver",
    "logging",
    "logging.handlers",
    "encodings",
    "encodings.utf_8",
    "encodings.cp1252",
    "encodings.mbcs",
    "zoneinfo",
]


def release_exe_path() -> Path:
    """Return the canonical EXE path that must be attached to GitHub Releases."""
    return OUTPUT_DIR / EXE_NAME


def sha256_file(path: Path) -> str:
    """Hash a release asset without loading the whole EXE into memory."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _project_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_DIR.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def release_asset_info(path: Path | None = None) -> dict:
    """Build the manifest record for the packaged EXE release asset."""
    target = path or release_exe_path()
    stat = target.stat()
    return {
        "name": target.name,
        "path": _project_relative(target),
        "size_bytes": stat.st_size,
        "size_mb": round(stat.st_size / (1024 * 1024), 2),
        "sha256": sha256_file(target),
        "required_for_github_release": True,
    }


def write_release_manifest(path: Path | None = None, smoke_tested: bool = False) -> Path | None:
    """Write a small manifest that CI can upload beside the EXE."""
    target = path or release_exe_path()
    if not target.exists():
        print(f"Release manifest failed: EXE does not exist: {target}")
        return None
    manifest = {
        "release_assets": [release_asset_info(target)],
        "release_rule": "GitHub Releases must include the packaged Windows EXE; source archives alone are not enough.",
        "smoke_test": {
            "required_for_github_release": True,
            "passed": bool(smoke_tested),
            "command": f"{EXE_NAME} --smoke-test",
            "covers": [
                "entrypoint imports",
                "WebView window options",
                "Flask app factory",
                "static HTML assets",
                "redacted diagnostics API",
            ],
        },
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = OUTPUT_DIR / RELEASE_MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Release manifest written: {manifest_path}")
    return manifest_path


def build():
    """执行 PyInstaller 打包"""
    main_py = str(PROJECT_DIR / "main.py").replace("\\", "/")
    project_dir = str(PROJECT_DIR).replace("\\", "/")
    static_dir = str(PROJECT_DIR / "static").replace("\\", "/")

    all_hiddenimports = STDLIB_IMPORTS + FLASK_IMPORTS + WEBVIEW_IMPORTS + TRAY_IMPORTS + LOCAL_MODULES
    hiddenimports_str = ",\n                   ".join([f'"{m}"' for m in all_hiddenimports])

    icon_path = str(PROJECT_DIR / "icon.ico").replace("\\", "/")
    icon_arg = f'icon="{icon_path}",' if Path(icon_path).exists() else ''
    data_entries = [f'("{static_dir}", "static"),']
    for asset_name in ("icon.ico", "icon.png"):
        asset_path = PROJECT_DIR / asset_name
        if asset_path.exists():
            asset_path_str = str(asset_path).replace("\\", "/")
            data_entries.append(f'("{asset_path_str}", "."),')
    datas_str = "\n        ".join(data_entries)

    spec_content = f'''# -*- mode: python ; coding: utf-8 -*-
a = Analysis(
    ["{main_py}"],
    pathex=["{project_dir}"],
    binaries=[],
    datas=[
        {datas_str}
    ],
    hiddenimports=[
        {hiddenimports_str}
    ],
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="{EXE_NAME}",
    {icon_arg}
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
'''

    spec_path = PROJECT_DIR / "codex_gui.spec"
    spec_path.write_text(spec_content, encoding="utf-8")
    print(f"Spec 文件已生成: {spec_path}")

    # 清理旧的构建
    for d in ["build", "dist"]:
        p = PROJECT_DIR / d
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)

    # 使用当前 Python 解释器
    python_exe = sys.executable
    print(f"使用 Python: {python_exe}")

    # 确保依赖已安装
    print("检查并安装依赖...")
    for package_name, import_name in BUILD_DEPENDENCIES:
        if importlib.util.find_spec(import_name) is None:
            print(f"Installing {package_name}...")
            try:
                subprocess.run(
                    [python_exe, "-m", "pip", "install", package_name],
                    check=True, timeout=180, cwd=str(PROJECT_DIR)
                )
            except subprocess.CalledProcessError as e:
                print(f"Installing {package_name} failed: {e}")
                return False

    # 打包
    print("开始打包...")
    pyinstaller_exe = str(Path(python_exe).parent / "Scripts" / "pyinstaller.exe")
    if not Path(pyinstaller_exe).exists():
        # fallback: use module invocation
        cmd = [python_exe, "-m", "PyInstaller", "--clean", "--noconfirm", str(spec_path)]
    else:
        cmd = [pyinstaller_exe, "--clean", "--noconfirm", str(spec_path)]

    result = subprocess.run(
        cmd,
        capture_output=True, text=True, timeout=900, cwd=str(PROJECT_DIR)
    )
    print(result.stdout)
    if result.returncode != 0:
        print("打包失败:")
        print(result.stderr)
        return False

    exe_path = release_exe_path()
    if not exe_path.exists():
        print(f"EXE 未生成: {exe_path}")
        return False

    size_mb = exe_path.stat().st_size / (1024 * 1024)
    print(f"打包成功! EXE 大小: {size_mb:.1f} MB")
    return True


def copy_to_desktop():
    """复制 EXE 到公共桌面"""
    global LAST_COPIED_EXE
    exe_path = release_exe_path()
    if not exe_path.exists():
        print("源 EXE 不存在")
        return False

    targets = [DESKTOP_DIR / EXE_NAME, USER_DESKTOP_DIR / EXE_NAME]

    for target in targets:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(exe_path), str(target))
            LAST_COPIED_EXE = target
            print(f"已复制到桌面: {target}")
            size_mb = target.stat().st_size / (1024 * 1024)
            print(f"文件大小: {size_mb:.1f} MB")
            return True
        except Exception as e:
            print(f"复制到 {target} 失败: {e}")

    return False


def verify_exe(path: Path | None = None, min_size_mb: float = 5.0):
    """验证 EXE 文件是否存在且大小合理"""
    target = path or LAST_COPIED_EXE or release_exe_path()
    if not target.exists():
        print(f"验证失败: 文件不存在 {target}")
        return False

    size_mb = target.stat().st_size / (1024 * 1024)
    if size_mb < min_size_mb:
        print(f"验证警告: 文件过小 ({size_mb:.1f} MB)，可能缺少依赖")
        return False

    print(f"验证通过: {target} ({size_mb:.1f} MB)")
    return True


def smoke_test_exe(path: Path | None = None, timeout_seconds: int = 45):
    """Launch the packaged EXE in smoke-test mode and require a clean exit."""
    target = path or release_exe_path()
    if not target.exists():
        print(f"Smoke test failed: EXE does not exist: {target}")
        return False

    print(f"Running packaged EXE smoke test: {target} --smoke-test")
    try:
        result = subprocess.run(
            [str(target), "--smoke-test"],
            cwd=str(PROJECT_DIR),
            timeout=max(1, int(timeout_seconds)),
        )
    except subprocess.TimeoutExpired:
        print(f"Smoke test failed: timed out after {timeout_seconds} seconds")
        return False
    except Exception as e:
        print(f"Smoke test failed: {e}")
        return False

    if result.returncode != 0:
        print(f"Smoke test failed: exit code {result.returncode}")
        return False

    print("Smoke test passed.")
    return True


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Build and verify the Windows release EXE.")
    parser.add_argument(
        "--no-desktop-copy",
        action="store_true",
        help="Skip copying the EXE to desktop. Use this in CI/release workflows.",
    )
    parser.add_argument(
        "--write-release-manifest",
        action="store_true",
        help=f"Write dist/{RELEASE_MANIFEST_NAME} with size and sha256 for release upload.",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Only verify the existing dist EXE and optionally write the release manifest.",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Run the packaged EXE with --smoke-test after file verification.",
    )
    parser.add_argument(
        "--smoke-timeout-seconds",
        type=int,
        default=45,
        help="Maximum seconds to wait for the packaged EXE smoke test.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()
    print("=" * 60)
    print("Codex History Manager (PyWebView) - 打包构建")
    print("=" * 60)

    if args.verify_only:
        success = verify_exe(release_exe_path())
        smoke_ok = False
        if success and args.smoke_test:
            smoke_ok = smoke_test_exe(release_exe_path(), args.smoke_timeout_seconds)
            success = smoke_ok
        if success and args.write_release_manifest:
            success = write_release_manifest(release_exe_path(), smoke_tested=smoke_ok) is not None
        sys.exit(0 if success else 1)

    success = build()
    if success:
        if not args.no_desktop_copy:
            copy_to_desktop()
        target = LAST_COPIED_EXE or release_exe_path()
        if verify_exe(target):
            smoke_ok = False
            if args.smoke_test:
                smoke_ok = smoke_test_exe(release_exe_path(), args.smoke_timeout_seconds)
                if not smoke_ok:
                    print("\n构建完成，但 packaged EXE smoke test 未通过")
                    sys.exit(1)
            manifest_ok = True
            if args.write_release_manifest:
                manifest_ok = write_release_manifest(release_exe_path(), smoke_tested=smoke_ok) is not None
            if not manifest_ok:
                print("\n构建完成，但 release manifest 生成失败")
                sys.exit(1)
            if args.no_desktop_copy:
                print(f"\n完成! Release EXE 已生成: {release_exe_path()}")
            else:
                print("\n完成! 桌面已生成 CodexHistoryManager.exe")
        else:
            print("\n构建完成，但验证未通过")
            sys.exit(1)
    else:
        print("\n构建失败")
        sys.exit(1)
