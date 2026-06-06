"""
PyInstaller 打包脚本 - 将 Codex History Manager (PyWebView 桌面版) 打包为单文件 EXE
"""
import subprocess
import sys
import os
import shutil
from pathlib import Path

PROJECT_DIR = Path(__file__).parent
OUTPUT_DIR = PROJECT_DIR / "dist"
DESKTOP_DIR = Path("C:/Users/Public/Desktop")
EXE_NAME = "CodexHistoryManager.exe"

# 本地项目模块（必须作为 hiddenimports 加入，否则 PyInstaller 检测不到）
LOCAL_MODULES = [
    "config",
    "db",
    "reader",
    "backup",
    "sync",
    "auto_detect",
    "token_stats",
]

# PyWebView 平台相关 hidden imports（Windows 使用 EdgeChromium + WinForms）
WEBVIEW_IMPORTS = [
    "webview",
    "webview.http",
    "webview.guilib",
    "webview.platforms.winforms",
    "webview.platforms.edgechromium",
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


def build():
    """执行 PyInstaller 打包"""
    main_py = str(PROJECT_DIR / "main.py").replace("\\", "/")
    project_dir = str(PROJECT_DIR).replace("\\", "/")
    static_dir = str(PROJECT_DIR / "static").replace("\\", "/")

    all_hiddenimports = STDLIB_IMPORTS + FLASK_IMPORTS + WEBVIEW_IMPORTS + LOCAL_MODULES
    hiddenimports_str = ",\n                   ".join([f'"{m}"' for m in all_hiddenimports])

    spec_content = f'''# -*- mode: python ; coding: utf-8 -*-
a = Analysis(
    ["{main_py}"],
    pathex=["{project_dir}"],
    binaries=[],
    datas=[
        ("{static_dir}", "static"),
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
    deps = ["pyinstaller", "flask", "pywebview"]
    for dep in deps:
        try:
            __import__(dep.replace("pyinstaller", "PyInstaller").lower().replace("pyinstaller", "pyinstaller"))
        except ImportError:
            print(f"安装 {dep}...")
            try:
                subprocess.run(
                    [python_exe, "-m", "pip", "install", dep],
                    check=True, timeout=180, cwd=str(PROJECT_DIR)
                )
            except subprocess.CalledProcessError as e:
                print(f"安装 {dep} 失败: {e}")
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

    exe_path = PROJECT_DIR / "dist" / EXE_NAME
    if not exe_path.exists():
        print(f"EXE 未生成: {exe_path}")
        return False

    size_mb = exe_path.stat().st_size / (1024 * 1024)
    print(f"打包成功! EXE 大小: {size_mb:.1f} MB")
    return True


def copy_to_desktop():
    """复制 EXE 到公共桌面"""
    exe_path = PROJECT_DIR / "dist" / EXE_NAME
    if not exe_path.exists():
        print("源 EXE 不存在")
        return False

    DESKTOP_DIR.mkdir(parents=True, exist_ok=True)
    target = DESKTOP_DIR / EXE_NAME

    try:
        shutil.copy2(str(exe_path), str(target))
        print(f"已复制到桌面: {target}")
        size_mb = target.stat().st_size / (1024 * 1024)
        print(f"文件大小: {size_mb:.1f} MB")
        return True
    except Exception as e:
        print(f"复制到桌面失败: {e}")
        return False


def verify_exe():
    """验证 EXE 文件是否存在且大小合理"""
    target = DESKTOP_DIR / EXE_NAME
    if not target.exists():
        print(f"验证失败: 文件不存在 {target}")
        return False

    size_mb = target.stat().st_size / (1024 * 1024)
    if size_mb < 5:
        print(f"验证警告: 文件过小 ({size_mb:.1f} MB)，可能缺少依赖")
        return False

    print(f"验证通过: {target} ({size_mb:.1f} MB)")
    return True


if __name__ == "__main__":
    print("=" * 60)
    print("Codex History Manager (PyWebView) - 打包构建")
    print("=" * 60)

    success = build()
    if success:
        copy_to_desktop()
        if verify_exe():
            print("\n完成! 桌面已生成 CodexHistoryManager.exe")
        else:
            print("\n构建完成，但验证未通过")
    else:
        print("\n构建失败")
        sys.exit(1)
