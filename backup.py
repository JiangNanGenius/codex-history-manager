"""
backup.py - 备份系统
支持手动备份、自动定时备份、增量备份、备份还原
所有备份均为 zip 压缩格式
"""
import os
import shutil
import json
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Callable


class BackupManager:
    def __init__(self, config, db):
        self.config = config
        self.db = db
        self._timer: Optional[threading.Timer] = None
        self._running = False
        self._on_backup_done: Optional[Callable] = None
        self._lock = threading.Lock()

    def set_callback(self, callback: Callable):
        """设置备份完成后的回调函数（用于更新 GUI）"""
        self._on_backup_done = callback

    def get_backup_dir(self) -> Path:
        d = Path(self.config.get("backup_dir", str(Path.home() / "codex_backups")))
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ─────────────── 完整备份 ───────────────

    def do_full_backup(self, label: str = "manual") -> Dict:
        """
        完整备份：把 state_5.sqlite 打成 zip
        返回 {"success": bool, "path": str, "size_mb": float, "error": str}
        """
        with self._lock:
            db_path = self.config.get("db_path")
            if not os.path.exists(db_path):
                return {"success": False, "error": f"数据库不存在: {db_path}"}

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            zip_name = f"codex_backup_{label}_{ts}.zip"
            zip_path = self.get_backup_dir() / zip_name

            try:
                with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                    zf.write(db_path, arcname="state_5.sqlite")
                    # 附带一个元数据文件
                    meta = {
                        "backup_type": "full",
                        "timestamp": datetime.now().isoformat(),
                        "label": label,
                        "source_db": db_path,
                        "stats": self._get_stats_safe(),
                    }
                    zf.writestr("backup_meta.json", json.dumps(meta, ensure_ascii=False, indent=2))

                size_mb = zip_path.stat().st_size / (1024 * 1024)
                self._prune_old_backups()

                result = {"success": True, "path": str(zip_path), "size_mb": round(size_mb, 2)}
                if self._on_backup_done:
                    self._on_backup_done(result)
                return result

            except Exception as e:
                return {"success": False, "error": str(e)}

    # ─────────────── 增量备份 ───────────────

    def do_incremental_backup(self, since_iso: str = "") -> Dict:
        """
        增量备份：导出自 since_iso 以来变更的 threads
        如果 since_iso 为空，默认取最近一次完整备份的时间
        """
        with self._lock:
            if not since_iso:
                since_iso = self._get_last_backup_time()

            try:
                changed = self.db.get_threads_since(since_iso)
            except Exception as e:
                return {"success": False, "error": f"查询变更 threads 失败: {e}"}

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            zip_name = f"codex_incremental_{ts}.zip"
            zip_path = self.get_backup_dir() / zip_name

            try:
                with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                    # 写入变更的 threads JSON
                    zf.writestr("changed_threads.json", json.dumps(changed, ensure_ascii=False, indent=2))

                    meta = {
                        "backup_type": "incremental",
                        "timestamp": datetime.now().isoformat(),
                        "since": since_iso,
                        "changed_count": len(changed),
                    }
                    zf.writestr("backup_meta.json", json.dumps(meta, ensure_ascii=False, indent=2))

                size_mb = zip_path.stat().st_size / (1024 * 1024)
                self._prune_old_backups()

                result = {
                    "success": True,
                    "path": str(zip_path),
                    "size_mb": round(size_mb, 2),
                    "changed_count": len(changed),
                }
                if self._on_backup_done:
                    self._on_backup_done(result)
                return result

            except Exception as e:
                return {"success": False, "error": str(e)}

    # ─────────────── 备份列表 ───────────────

    def list_backups(self) -> List[Dict]:
        """列出所有备份文件"""
        backup_dir = self.get_backup_dir()
        backups = []
        for f in sorted(backup_dir.glob("codex_backup_*.zip"), reverse=True):
            info = {"path": str(f), "name": f.name, "size_mb": 0.0, "meta": {}}
            try:
                info["size_mb"] = round(f.stat().st_size / (1024 * 1024), 2)
                info["mtime"] = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                # 尝试读取 meta
                with zipfile.ZipFile(f, "r") as zf:
                    if "backup_meta.json" in zf.namelist():
                        info["meta"] = json.loads(zf.read("backup_meta.json"))
            except Exception:
                pass
            backups.append(info)

        # 也列出增量备份
        for f in sorted(backup_dir.glob("codex_incremental_*.zip"), reverse=True):
            info = {"path": str(f), "name": f.name, "size_mb": 0.0, "meta": {}, "type": "incremental"}
            try:
                info["size_mb"] = round(f.stat().st_size / (1024 * 1024), 2)
                info["mtime"] = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                with zipfile.ZipFile(f, "r") as zf:
                    if "backup_meta.json" in zf.namelist():
                        info["meta"] = json.loads(zf.read("backup_meta.json"))
            except Exception:
                pass
            backups.append(info)

        # 按修改时间排序
        backups.sort(key=lambda x: x.get("mtime", ""), reverse=True)
        return backups

    def restore_backup(self, backup_path: str) -> Dict:
        """
        从备份还原：先备份当前数据库，再用备份覆盖
        """
        if not os.path.exists(backup_path):
            return {"success": False, "error": "备份文件不存在"}

        db_path = self.config.get("db_path")

        # 先备份当前数据库（以防还原失败）
        pre_backup = self.do_full_backup(label="pre_restore")
        if not pre_backup["success"]:
            return {"success": False, "error": f"还原前备份失败: {pre_backup.get('error')}"}

        try:
            # 关闭现有连接
            self.db.close()

            with zipfile.ZipFile(backup_path, "r") as zf:
                if "state_5.sqlite" not in zf.namelist():
                    self.db.connect()
                    return {"success": False, "error": "备份文件不含 state_5.sqlite（可能是增量备份，无法还原）"}

                target = Path(db_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp_path = target.with_name(f".{target.name}.restore_tmp")
                with zf.open("state_5.sqlite", "r") as src, open(tmp_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                os.replace(tmp_path, target)

            # 重新连接
            self.db.connect()
            return {"success": True, "message": "还原成功，原数据库已备份到: " + pre_backup["path"]}

        except Exception as e:
            tmp_path = Path(db_path).with_name(f".{Path(db_path).name}.restore_tmp")
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
            # 尝试恢复连接
            try:
                self.db.connect()
            except Exception:
                pass
            return {"success": False, "error": str(e)}

    # ─────────────── 自动备份 ───────────────

    def start_auto_backup(self):
        """启动自动定时备份"""
        if self._running:
            return
        self._running = True
        self._schedule_next()

    def stop_auto_backup(self):
        """停止自动定时备份"""
        self._running = False
        if self._timer:
            self._timer.cancel()
            self._timer = None

    def _schedule_next(self):
        if not self._running:
            return
        interval_hours = self.config.get("backup_interval_hours", 6)
        interval_secs = interval_hours * 3600
        self._timer = threading.Timer(interval_secs, self._auto_backup_tick)
        self._timer.daemon = True
        self._timer.start()

    def _auto_backup_tick(self):
        """定时备份回调"""
        if self._running:
            self.do_incremental_backup()
            self._schedule_next()

    # ─────────────── 内部工具 ───────────────

    def _get_stats_safe(self) -> Dict:
        try:
            return self.db.get_stats()
        except Exception:
            return {}

    def _get_last_backup_time(self) -> str:
        """获取最近一次备份的时间戳（用于增量备份范围）"""
        backups = self.list_backups()
        for b in backups:
            meta = b.get("meta", {})
            ts = meta.get("timestamp", "")
            if ts:
                return ts
        # 没有备份记录，默认返回 24 小时前
        from datetime import timedelta
        return (datetime.now() - timedelta(hours=24)).isoformat()

    def _prune_old_backups(self):
        """删除超出数量限制的旧备份"""
        max_backups = self.config.get("max_backups", 20)
        backup_dir = self.get_backup_dir()
        all_zips = sorted(
            list(backup_dir.glob("codex_backup_*.zip")) +
            list(backup_dir.glob("codex_incremental_*.zip")),
            key=lambda f: f.stat().st_mtime
        )
        while len(all_zips) > max_backups:
            try:
                all_zips[0].unlink()
                all_zips.pop(0)
            except Exception:
                break
