"""
app.py - Flask Web 应用 + 所有 API 端点
提供 RESTful API 供前端 SPA 调用
"""
import os
import json
import re
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict

from flask import Flask, jsonify, request, send_from_directory

from config import Config
from db import CodexDB
from reader import read_messages, export_to_markdown, export_to_text, get_file_size_mb
from backup import BackupManager
from sync import full_sync, load_config_toml, is_codex_running, kill_codex, start_codex, resolve_codex_home, CODEX_PLUS_PLUS_PATH
from auto_detect import detect_all
from token_stats import TokenStats


def create_app() -> Flask:
    """创建 Flask 应用实例"""
    app = Flask(
        __name__,
        static_folder="static",
        static_url_path="",
    )
    app.config["JSON_AS_ASCII"] = False

    # 全局状态
    config = Config()
    db = CodexDB(config.get("db_path"))
    backup_mgr = BackupManager(config, db)
    token_stats = TokenStats(config.get("db_path"))

    # 启动自动备份
    if config.get("auto_backup"):
        backup_mgr.start_auto_backup()

    # 尝试连接数据库
    try:
        db.connect()
    except Exception:
        pass

    # ─────────────── 页面路由 ───────────────

    @app.route("/")
    def index():
        """返回 SPA 主页面"""
        return send_from_directory("static", "index.html")

    @app.route("/monitor")
    def monitor():
        """返回桌面 Token 悬浮监控窗页面"""
        return send_from_directory("static", "monitor.html")

    # ─────────────── 会话 API ───────────────

    @app.route("/api/sessions")
    def list_sessions():
        """获取会话列表（支持搜索、分页、排序）"""
        try:
            page = _clamp_int(request.args.get("page", 0), 0, 0, 100000)
            page_size = _clamp_int(request.args.get("page_size", config.get("page_size", 50)), 50, 10, 200)
            search = request.args.get("search", "")
            filter_mode = request.args.get("filter", "all")
            source = request.args.get("source", "all")
            sort_by = request.args.get("sort_by", "created_at_ms")
            sort_order = request.args.get("sort_order", "desc")
            model_filter = request.args.get("model", "")
            provider_filter = request.args.get("provider", "")

            rows, total = db.list_threads(
                filter_mode=filter_mode,
                search=search,
                page=page,
                page_size=page_size,
                source_filter=source,
                sort_by=sort_by,
                sort_order=sort_order,
                model_filter=model_filter,
                provider_filter=provider_filter,
            )
            return jsonify({"sessions": rows, "total": total, "page": page, "page_size": page_size})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/sessions/<session_id>")
    def get_session(session_id):
        """获取会话详情（含消息内容）"""
        try:
            thread = db.get_thread(session_id)
            if not thread:
                return jsonify({"error": "会话不存在"}), 404

            rollout_path = thread.get("rollout_path") or ""

            # 如果没有 rollout_path，尝试从 sessions 目录推断
            if not rollout_path or not os.path.exists(rollout_path):
                rollout_path = _find_file_for_thread(thread, config)

            if rollout_path and os.path.exists(rollout_path):
                max_msgs = config.get("max_lines_large_file", 2000)
                large_thresh = config.get("large_file_threshold_mb", 500)
                data = read_messages(rollout_path, max_messages=max_msgs, large_file_limit=large_thresh)
                thread["messages"] = data.get("messages", [])
                thread["message_count"] = len(data.get("messages", []))
                thread["is_large_file"] = data.get("is_large_file", False)
                thread["truncated"] = data.get("truncated", False)
                thread["file_size_mb"] = data.get("file_size_mb", 0)
            else:
                thread["messages"] = []
                thread["message_count"] = 0
                thread["file_not_found"] = True

            return jsonify(thread)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/sessions/<session_id>/archive", methods=["POST"])
    def archive_session(session_id):
        """归档会话"""
        try:
            ok = db.set_archived(session_id, 1)
            if ok:
                return jsonify({"success": True, "message": "已归档"})
            return jsonify({"error": "操作失败"}), 500
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/sessions/<session_id>/unarchive", methods=["POST"])
    def unarchive_session(session_id):
        """取消归档"""
        try:
            ok = db.set_archived(session_id, 0)
            if ok:
                return jsonify({"success": True, "message": "已取消归档"})
            return jsonify({"error": "操作失败"}), 500
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/sessions/<session_id>/export/<fmt>")
    def export_session(session_id, fmt):
        """导出会话为 md/json/txt"""
        try:
            thread = db.get_thread(session_id)
            if not thread:
                return jsonify({"error": "会话不存在"}), 404

            rollout_path = thread.get("rollout_path") or ""
            if not rollout_path or not os.path.exists(rollout_path):
                rollout_path = _find_file_for_thread(thread, config)

            if not rollout_path or not os.path.exists(rollout_path):
                return jsonify({"error": "找不到 jsonl 文件"}), 404

            title = thread.get("title") or "会话"
            safe_title = _safe_export_filename(title)

            if fmt == "md":
                content = export_to_markdown(rollout_path, title=title)
                return jsonify({"content": content, "filename": f"{safe_title}.md", "format": "markdown"})
            elif fmt == "txt":
                content = export_to_text(rollout_path, title=title)
                return jsonify({"content": content, "filename": f"{safe_title}.txt", "format": "text"})
            elif fmt == "json":
                data = read_messages(rollout_path, max_messages=99999, large_file_limit=99999)
                output = {"thread": thread, "messages": data["messages"]}
                return jsonify({"content": json.dumps(output, ensure_ascii=False, indent=2), "filename": f"{safe_title}.json", "format": "json"})
            else:
                return jsonify({"error": f"不支持的格式: {fmt}"}), 400
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ─────────────── Token 统计 API ───────────────

    @app.route("/api/token/current")
    def get_current_tokens():
        """获取轻量当前 Token 统计（用量追踪/实时面板轮询使用）。"""
        try:
            token_stats.db_path = config.get("db_path")
            start = request.args.get("start", "")
            end = request.args.get("end", "")
            granularity = request.args.get("granularity", "total")
            data = token_stats.get_current_stats(
                start=start,
                end=end,
                granularity=granularity,
            )
            cc_switch_db_path = config.get("cc_switch_db_path", "")
            data["cc_switch_db_configured"] = bool(cc_switch_db_path)
            data["cc_switch_db_path"] = cc_switch_db_path
            if cc_switch_db_path:
                cache_data = token_stats.get_cc_switch_cache_stats(
                    cc_switch_db_path=cc_switch_db_path,
                    start=start,
                    end=end,
                )
                data.update(cache_data)
            else:
                data["cache_note"] = (
                    "未配置代理缓存数据库；缓存统计需要请求经过代理数据源，官方 API 和自定义 API 都可被统计。"
                )
            return jsonify(data)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/stats/overview")
    def stats_overview():
        """Token 总览"""
        try:
            token_stats.db_path = config.get("db_path")
            data = token_stats.get_overview()
            return jsonify(data)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/stats/by-model")
    def stats_by_model():
        """按模型分组统计"""
        try:
            token_stats.db_path = config.get("db_path")
            data = token_stats.get_by_model()
            return jsonify(data)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/stats/by-provider")
    def stats_by_provider():
        """按提供商分组统计"""
        try:
            token_stats.db_path = config.get("db_path")
            data = token_stats.get_by_provider()
            return jsonify(data)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/stats/daily-trend")
    def stats_daily_trend():
        """每日趋势"""
        try:
            days = _clamp_int(request.args.get("days", 30), 30, 1, 365)
            token_stats.db_path = config.get("db_path")
            data = token_stats.get_daily_trend(days=days)
            return jsonify(data)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/stats/top-sessions")
    def stats_top_sessions():
        """最耗 Token 会话排行"""
        try:
            limit = _clamp_int(request.args.get("limit", 20), 20, 1, 100)
            token_stats.db_path = config.get("db_path")
            data = token_stats.get_top_sessions(limit=limit)
            return jsonify(data)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/stats/hourly")
    def stats_hourly():
        """每小时使用分布"""
        try:
            token_stats.db_path = config.get("db_path")
            data = token_stats.get_hourly_distribution()
            return jsonify(data)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ─────────────── 同步 API ───────────────

    @app.route("/api/sync/preview", methods=["POST"])
    def sync_preview():
        """预览同步变更（Dry Run）"""
        try:
            body = request.get_json(silent=True) or {}
            target_provider = body.get("target_provider", "")
            target_model = body.get("target_model", "")

            stats = full_sync(
                target_provider=target_provider,
                target_model=target_model,
                dry_run=True,
            )
            return jsonify(_sync_stats_to_dict(stats))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/sync/execute", methods=["POST"])
    def sync_execute():
        """执行同步"""
        try:
            body = request.get_json(silent=True) or {}
            target_provider = body.get("target_provider", "")
            target_model = body.get("target_model", "")

            payload, status = _run_sync_with_backup(
                backup_mgr,
                target_provider=target_provider,
                target_model=target_model,
            )
            if status >= 400:
                return jsonify(payload), status

            # 同步后刷新数据库连接
            try:
                db.close()
                db.db_path = config.get("db_path")
                db.connect()
            except Exception:
                pass

            return jsonify(payload)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/sync/status")
    def sync_status():
        """获取同步状态（provider 分布等）"""
        try:
            codex_home = resolve_codex_home()
            config_data = load_config_toml(str(codex_home / "config.toml"))
            provider_dist = db.get_provider_distribution()
            running, pids = is_codex_running()

            return jsonify({
                "current_provider": config_data.get("model_provider", ""),
                "current_model": config_data.get("model", ""),
                "provider_distribution": provider_dist,
                "codex_running": running,
                "codex_pids": pids,
                "use_codex_plus_plus": config.get("use_codex_plus_plus", False),
                "codex_plus_plus_path": config.get("codex_plus_plus_path", CODEX_PLUS_PLUS_PATH),
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/codex/status")
    def codex_status():
        """获取 Codex 进程状态"""
        try:
            running, pids = is_codex_running()
            return jsonify({"running": running, "pids": pids})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/codex/kill", methods=["POST"])
    def codex_kill():
        """关闭 Codex 进程"""
        try:
            ok, msg = kill_codex()
            return jsonify({"success": ok, "message": msg})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/codex/start", methods=["POST"])
    def codex_start():
        """启动 Codex/Codex++（启动前自动同步当前 provider/model）。"""
        try:
            body = request.get_json(silent=True) or {}
            use_cpp = body.get("use_codex_plus_plus", config.get("use_codex_plus_plus", False))
            sync_payload, sync_status = _run_sync_with_backup(backup_mgr)
            if sync_status >= 400:
                return jsonify(sync_payload), sync_status
            ok, msg = start_codex(
                use_codex_plus_plus=use_cpp,
                codex_plus_plus_path=config.get("codex_plus_plus_path", ""),
                codex_cli_path=config.get("codex_cli_path", ""),
            )
            return jsonify({"success": ok, "message": msg, "sync": sync_payload})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ─────────────── 备份 API ───────────────

    @app.route("/api/backups")
    def list_backups():
        """获取备份列表"""
        try:
            backups = backup_mgr.list_backups()
            return jsonify(backups)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/backups/create", methods=["POST"])
    def create_backup():
        """创建完整备份"""
        try:
            result = backup_mgr.do_full_backup(label="manual")
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/backups/<path:backup_id>/restore", methods=["POST"])
    def restore_backup(backup_id):
        """还原备份"""
        try:
            backup_path = _resolve_backup_path(backup_id, backup_mgr.get_backup_dir())
            if not backup_path or not os.path.exists(backup_path):
                return jsonify({"error": "备份文件不存在"}), 404
            result = backup_mgr.restore_backup(str(backup_path))
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/backups/incremental", methods=["POST"])
    def create_incremental_backup():
        """创建增量备份"""
        try:
            result = backup_mgr.do_incremental_backup()
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ─────────────── 设置 API ───────────────

    @app.route("/api/settings")
    def get_settings():
        """读取设置"""
        try:
            return jsonify(config.get_all())
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/settings", methods=["POST"])
    def save_settings():
        """保存设置"""
        try:
            data = request.get_json(silent=True) or {}
            config.update(data)

            # 重新连接数据库（路径可能变了）
            if "db_path" in data:
                try:
                    db.close()
                    db.db_path = data["db_path"]
                    db.connect()
                    token_stats.db_path = data["db_path"]
                except Exception as e:
                    return jsonify({"success": True, "warning": f"数据库重连失败: {e}"})

            # 自动备份开关
            if "auto_backup" in data:
                if data["auto_backup"]:
                    backup_mgr.start_auto_backup()
                else:
                    backup_mgr.stop_auto_backup()

            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/settings/reset", methods=["POST"])
    def reset_settings():
        """重置为默认"""
        try:
            config.reset_defaults()
            # 重连数据库
            try:
                db.close()
                db.db_path = config.get("db_path")
                db.connect()
                token_stats.db_path = config.get("db_path")
            except Exception:
                pass
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ─────────────── 自动检测 API ───────────────

    @app.route("/api/detect")
    def detect_paths():
        """检测所有路径"""
        try:
            results = detect_all()
            return jsonify(results)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ─────────────── 过滤器选项 API ───────────────

    @app.route("/api/filters")
    def get_filters():
        """获取过滤选项（model列表、provider列表）"""
        try:
            models = db.get_model_list()
            providers = db.get_provider_list()
            return jsonify({"models": models, "providers": providers})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/health")
    def health():
        """轻量健康检查，供前端和打包后排障使用。"""
        settings = config.get_all()
        db_path = settings.get("db_path", "")
        sessions_dir = settings.get("sessions_dir", "")
        return jsonify({
            "ok": True,
            "db_path_configured": bool(db_path),
            "db_path_exists": bool(db_path and os.path.exists(db_path)),
            "sessions_dir_configured": bool(sessions_dir),
            "sessions_dir_exists": bool(sessions_dir and os.path.isdir(sessions_dir)),
            "auto_backup": bool(settings.get("auto_backup")),
        })

    return app


def _clamp_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


def _safe_export_filename(title: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(title or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return (cleaned or "session")[:80]


def _resolve_backup_path(backup_id: str, backup_dir: Path) -> Path | None:
    if not backup_id or "/" in backup_id or "\\" in backup_id or backup_id.startswith("."):
        return None
    base = backup_dir.resolve()
    candidate = (base / backup_id).resolve()
    if candidate.parent != base:
        return None
    return candidate


def _sync_stats_to_dict(stats) -> Dict:
    return {
        "db_threads_seen": stats.db_threads_seen,
        "db_threads_updated": stats.db_threads_updated,
        "rollout_files_seen": stats.rollout_files_seen,
        "rollout_files_updated": stats.rollout_files_updated,
        "index_rows_seen": stats.index_rows_seen,
        "index_rows_updated": stats.index_rows_updated,
        "malformed_lines": stats.malformed_lines,
        "errors": stats.errors,
        "changed": stats.changed,
    }


def _run_sync_with_backup(backup_mgr: BackupManager, target_provider: str = "", target_model: str = "") -> tuple[Dict, int]:
    preview = full_sync(
        target_provider=target_provider,
        target_model=target_model,
        dry_run=True,
    )
    if not preview.changed:
        payload = _sync_stats_to_dict(preview)
        payload["backup_path"] = ""
        payload["skipped_backup"] = True
        return payload, 200

    pre_backup = backup_mgr.do_full_backup(label="pre_sync")
    if not pre_backup.get("success"):
        return {"error": f"同步前数据库备份失败: {pre_backup.get('error', 'unknown')}"}, 500

    stats = full_sync(
        target_provider=target_provider,
        target_model=target_model,
        dry_run=False,
    )
    payload = _sync_stats_to_dict(stats)
    payload["backup_path"] = pre_backup.get("path", "")
    return payload, 200


def _find_file_for_thread(thread: Dict, config: Config) -> str:
    """根据 thread_id 和 archived 状态推断文件路径"""
    import glob as glob_module
    tid = thread.get("id", "")
    archived = thread.get("archived", 0)
    base_dir = config.get("archived_dir") if archived else config.get("sessions_dir")

    if not base_dir:
        return ""

    patterns = [
        os.path.join(base_dir, f"*{tid}*.jsonl"),
        os.path.join(base_dir, "**", f"*{tid}*.jsonl"),
    ]
    for pat in patterns:
        files = glob_module.glob(pat, recursive=True)
        if files:
            return files[0]
    return ""
