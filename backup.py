"""
backup.py - 备份系统
支持手动备份、自动定时备份、增量备份、备份还原
所有备份均为 zip 压缩格式

设计意图：
  - state_5.sqlite 是 Codex 的核心数据文件（会话、消息、token 统计），
    任何同步或配置变更前都应先备份，以便出错时恢复。
  - 增量备份导出变更的 threads JSON，而非复制整个 sqlite：
    速度快、体积小，适合高频自动备份场景。
  - 自动备份使用 threading.Timer 而非 cron/scheduler：降低依赖，
    在打包为单文件 exe 后仍然可用。

工程权衡：
  - zip 格式：跨平台、易人工检查、可直接用系统自带工具解压。
  - 线程锁（threading.Lock）：防止手动备份和自动备份并发执行导致
    数据库文件在 zip 中处于不一致状态（SQLite 虽然读取可并发，但
    WAL  checkpoint 可能产生短暂不一致）。
  - 备份前不关闭 DB 连接：SQLite 在 WAL 模式下支持热备份，
    但为了更安全的还原，restore_backup 时会先关闭连接。

Windows 平台特殊性：
  - os.replace 在 Windows 上要求目标文件不存在或被关闭；restore_backup
    中先关闭 DB 连接再替换，避免 PermissionError。
  - threading.Timer 的 daemon=True 确保主进程退出时定时器不会阻止程序结束。
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
        完整备份：把 state_5.sqlite 打成 zip。

        设计意图：
          - 完整备份是「恢复点」：还原时必须使用完整备份，增量备份只能作为辅助。
          - 附带 backup_meta.json：记录备份类型、时间戳、标签、来源路径和 DB 统计，
            便于备份管理和问题排查。

        边界条件：
          - 数据库不存在时立即返回错误，不生成空 zip。
          - 备份完成后调用 _prune_old_backups 清理过期备份，防止磁盘无限增长。

        Args:
            label: 备份标签，如 "manual"、"pre_sync"、"pre_restore"，用于区分场景。

        Returns:
            {"success": bool, "path": str, "size_mb": float, "error": str}
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
        增量备份：导出自 since_iso 以来变更的 threads。

        设计意图：
          - 增量备份不复制 sqlite 文件本身，而是导出变更的 threads 记录为 JSON，
            体积极小（通常 KB 级），适合高频自动备份（如每 6 小时一次）。
          - since_iso 为空时自动取最近备份时间：用户无需手动指定范围，降低使用门槛。

        工程权衡：
          - 增量备份**不能**用于还原：它只包含变更记录，不包含完整 DB 结构。
            还原时必须使用完整备份。这是设计上的有意限制，避免用户误操作。
          - 若 DB 查询失败（如表结构变更导致 SQL 错误），返回错误而非空备份，
            防止用户误以为「无变更」而遗漏数据。

        Args:
            since_iso: ISO 格式时间戳，作为变更查询的起始点。空字符串则自动推断。

        Returns:
            {"success": bool, "path": str, "size_mb": float, "changed_count": int, "error": str}
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
        从备份还原数据库。

        设计意图：
          - 「先备份再还原」：即使目标备份损坏或用户选错文件，当前数据库
            仍有 pre_restore 完整备份可回退。
          - 关闭 DB 连接后再替换文件：Windows 上若文件被进程占用，
            os.replace 会抛出 PermissionError；关闭连接释放句柄后再操作。

        边界条件：
          - 仅支持完整备份还原：若 zip 中不含 state_5.sqlite（如增量备份），
            立即返回错误并恢复 DB 连接。
          - 临时文件 + os.replace：保证替换操作的原子性，防止替换过程中
            进程崩溃导致数据库处于半写状态。
          - 任何异常后尝试恢复 DB 连接：即使还原失败，也尽量让应用继续运行，
            而非处于「无 DB 连接」的不可用状态。

        Args:
            backup_path: 备份 zip 文件的绝对路径。

        Returns:
            {"success": bool, "message": str, "error": str}
        """
        if not os.path.exists(backup_path):
            return {"success": False, "error": "备份文件不存在"}

        db_path = self.config.get("db_path")
        if not db_path:
            return {"success": False, "error": "db_path 未配置"}

        # 先备份当前数据库（以防还原失败）
        pre_backup = self.do_full_backup(label="pre_restore")
        if not pre_backup["success"]:
            return {"success": False, "error": f"还原前备份失败: {pre_backup.get('error')}"}

        target = Path(db_path)
        tmp_path = target.with_name(f".{target.name}.restore_tmp")
        try:
            with self._lock:
                # 关闭现有连接
                self.db.close()

                with zipfile.ZipFile(backup_path, "r") as zf:
                    if "state_5.sqlite" not in zf.namelist():
                        self.db.connect()
                        return {"success": False, "error": "备份文件不含 state_5.sqlite（可能是增量备份，无法还原）"}

                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open("state_5.sqlite", "r") as src, open(tmp_path, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    os.replace(tmp_path, target)

                # 重新连接
                self.db.connect()
            return {"success": True, "message": "还原成功，原数据库已备份到: " + pre_backup["path"]}

        except Exception as e:
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
        """
        删除超出数量限制的旧备份。

        设计意图：
          - 防止备份目录无限增长，尤其自动备份开启后可能产生大量 zip。
          - max_backups 默认 20：在「保留足够历史」与「节省磁盘」之间折中。
            用户可在设置中调整。

        工程权衡：
          - 按修改时间排序，删除最旧的：简单、可预测。
          - 单个文件删除失败（如被防病毒软件扫描中）不阻断其余清理，
            用 try/except 包裹并静默继续。
          - 完整备份和增量备份统一计数：因为增量备份通常很小，
            不单独限制类型比例，保持逻辑简单。
        """
        max_backups = self.config.get("max_backups", 20)
        backup_dir = self.get_backup_dir()
        all_zips = sorted(
            list(backup_dir.glob("codex_backup_*.zip")) +
            list(backup_dir.glob("codex_incremental_*.zip")),
            key=lambda f: f.stat().st_mtime
        )
        while len(all_zips) > max_backups:
            target = all_zips.pop(0)
            try:
                target.unlink()
            except Exception:
                # 单个文件删除失败（如权限占用）不阻断其余清理
                pass
