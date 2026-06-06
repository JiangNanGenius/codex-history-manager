"""
PyInstaller 打包脚本 - 将 Codex History Manager (Web 版) 打包为单文件 EXE
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


def build():
    """执行 PyInstaller 打包"""
    main_py = str(PROJECT_DIR / "main.py").replace("\\", "/")
    project_dir = str(PROJECT_DIR).replace("\\", "/")
    static_dir = str(PROJECT_DIR / "static").replace("\\", "/")

    spec_content = f'''# -*- mode: python ; coding: utf-8 -*-
a = Analysis(
    ["{main_py}"],
    pathex=["{project_dir}"],
    binaries=[],
    datas=[
        ("{static_dir}", "static"),
    ],
    hiddenimports=["flask", "sqlite3", "json", "threading", "datetime",
                   "os", "sys", "shutil", "zipfile", "tempfile", "subprocess",
                   "pathlib", "glob", "dataclasses", "typing", "tomllib",
                   "werkzeug", "jinja2", "markupsafe", "click", "itsdangerous",
                   "blinker"],
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

    # 执行 PyInstaller
    python_exe = r"C:\Users\zhaos\.workbuddy\binaries\python\versions\3.13.12\python.exe"

    # 确保 PyInstaller 已安装
    print("检查 PyInstaller...")
    try:
        subprocess.run([python_exe, "-m", "pip", "install", "pyinstaller"],
                      check=True, timeout=120, cwd=str(PROJECT_DIR))
    except subprocess.CalledProcessError as e:
        print(f"安装 PyInstaller 失败: {e}")
        return False

    # 打包
    print("开始打包...")
    pyinstaller_exe = str(Path(python_exe).parent / "Scripts" / "pyinstaller.exe")
    result = subprocess.run(
        [pyinstaller_exe, "--clean", "--noconfirm", str(spec_path)],
        capture_output=True, text=True, timeout=600, cwd=str(PROJECT_DIR)
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


if __name__ == "__main__":
    print("=" * 60)
    print("Codex History Manager (Web) - 打包构建")
    print("=" * 60)

    if build():
        copy_to_desktop()
        print("\n完成! 桌面已生成 CodexHistoryManager.exe")
    else:
        print("\n构建失败")
        sys.exit(1)
