"""
app.py - Flask Web 应用 + 所有 API 端点
提供 RESTful API 供前端 SPA 调用

设计意图：
  - 纯 API 后端：所有 HTML 渲染由前端 SPA（static/js/*.js）完成，
    Flask 只负责数据接口和静态文件服务。
  - 全局状态管理：create_app 内初始化 Config、CodexDB、BackupManager、
    TokenStats、ProviderRegistry 等实例，通过闭包在 API 端点间共享。
  - 异常捕获：每个端点统一用 try/except 包裹，返回 JSON 错误响应，
    防止后端崩溃导致前端收到 500 HTML 页面。

工程权衡：
  - 不使用 Blueprint：当前端点数量适中（~40 个），全部写在 create_app 内
    可读性尚可；若未来端点翻倍，建议拆分为 Blueprint。
  - _refresh_provider_registry_path：provider_store_path 可能在设置中被修改，
    每次访问 provider API 前刷新路径，保证一致性。
  - JSON_AS_ASCII = False：确保中文错误消息、模型名称等在前端正确显示，
    而非被转义为 \\uXXXX。

Windows 平台特殊性：
  - send_from_directory 服务静态文件：Windows 路径分隔符差异由 Flask/Pathlib
    自动处理，无需手动替换。
"""
import os
import json
import re
import shutil
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from flask import Flask, jsonify, request, send_from_directory

from config import Config, CONFIG_FILE
from db import CodexDB
from reader import read_messages, export_to_markdown, export_to_text, get_file_size_mb
from backup import BackupManager
from sync import full_sync, is_codex_running, kill_codex, start_codex, resolve_codex_home, CODEX_PLUS_PLUS_PATH
from auto_detect import detect_all
from token_stats import TokenStats
from codex_rollout_usage import get_codex_rollout_cache_stats
from providers import ProviderRegistry, DEFAULT_STORE_PATH
from amr_registry import AMRRegistry
from codex_config import (
    CodexConfigManager,
    backup_file,
    redact_auth_for_preview,
    save_config_toml,
    load_config_toml as _load_config_toml,
)
from proxy_server import LocalProxyServer
from domestic_responses import build_domestic_responses_probe_preview
from diagnostics import DiagnosticsCollector
from move_repair import MoveRepairManager
from guardrails import codex_mutation_error_payload, has_codex_mutation_confirmation
from app_paths import LEGACY_APP_DIR, LEGACY_CONFIG_FILE, app_data_dir, ensure_app_dirs, is_within
from currency import (
    build_rate_snapshot,
    convert_amount,
    normalize_currency_settings,
    preserve_redacted_currency_secret,
    redact_currency_settings,
    update_currency_config,
)
from costing import estimate_request_cost, pricing_preview_payload


UNINSTALL_CLEANUP_CONFIRMATION = "UNINSTALL_CLEANUP"


def create_app() -> Flask:
    """
    创建 Flask 应用实例。

    设计意图：
      - 工厂模式：便于测试时创建独立实例，避免全局 app 污染。
      - 静态文件直接服务：index.html 和 monitor.html 通过 send_from_directory
        提供，无需模板引擎。

    Returns:
        配置完成的 Flask 应用实例。
    """
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
    provider_registry = ProviderRegistry(config.get("provider_store_path", ""))
    proxy_server = LocalProxyServer(
        port=config.get("proxy_port", 8080),
        provider_store_path=config.get("provider_store_path", ""),
    )
    amr_registry = AMRRegistry()

    diagnostics_collector = DiagnosticsCollector(
        config=config,
        provider_registry=provider_registry,
        proxy_server=proxy_server,
        amr_registry=amr_registry,
    )

    def _refresh_provider_registry_path():
        provider_registry.store_path = Path(config.get("provider_store_path", "") or DEFAULT_STORE_PATH).expanduser()

    def _require_codex_mutation_confirmation(body: Dict, action: str):
        """Require a typed confirmation for endpoints that mutate Codex state."""
        if has_codex_mutation_confirmation(body):
            return None
        return jsonify(codex_mutation_error_payload(action)), 409

    @app.before_request
    def _block_writes_after_uninstall_cleanup():
        """After uninstall cleanup, keep the current process read-only."""
        if request.method in {"GET", "HEAD", "OPTIONS"}:
            return None
        if request.path == "/api/uninstall-cleanup/execute":
            return None
        if config.is_write_locked():
            return jsonify({
                "error": "Local writes are locked until restart.",
                "write_locked": True,
                "reason": config.write_lock_reason(),
            }), 423
        return None

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
            rollout_scan_fallback = str(request.args.get("rollout_scan_fallback", "")).lower() in {"1", "true", "yes"}
            rollout_limit = _clamp_int(request.args.get("rollout_limit", 200), 200, 1, 1000)
            data = token_stats.get_current_stats(
                start=start,
                end=end,
                granularity=granularity,
            )
            rollout_cache_data = get_codex_rollout_cache_stats(
                db_path=config.get("db_path", ""),
                sessions_dir=config.get("sessions_dir", "") if rollout_scan_fallback else "",
                start=start,
                end=end,
                limit=rollout_limit,
            )
            data["codex_rollout_scan_fallback"] = rollout_scan_fallback
            data["codex_rollout_scan_limit"] = rollout_limit
            cc_switch_db_path = config.get("cc_switch_db_path", "")
            data["cc_switch_db_configured"] = bool(cc_switch_db_path)
            data["cc_switch_db_path"] = cc_switch_db_path
            cc_cache_data = None
            if cc_switch_db_path:
                cc_cache_data = token_stats.get_cc_switch_cache_stats(
                    cc_switch_db_path=cc_switch_db_path,
                    start=start,
                    end=end,
                )
            else:
                data["cache_note"] = (
                    "未配置代理缓存数据库；缓存统计需要请求经过代理数据源，官方 API 和自定义 API 都可被统计。"
                )
            _merge_cache_usage_sources(data, rollout_cache_data, cc_cache_data)
            _attach_usage_source_summary(data, proxy_server.status())
            data.update(_resolve_current_context_window(config, provider_registry))
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
            return jsonify(redact_currency_settings(config.get_all()))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/settings", methods=["POST"])
    def save_settings():
        """保存设置"""
        try:
            data = request.get_json(silent=True) or {}
            data = preserve_redacted_currency_secret(data, config.get_all())
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

    @app.route("/api/settings/storage")
    def settings_storage():
        """返回配置、临时文件和导出目录位置。"""
        try:
            ensure_app_dirs()
            settings = config.get_all()
            return jsonify({
                "app_data_dir": str(app_data_dir()),
                "config_file": str(CONFIG_FILE),
                "legacy_config_file": str(LEGACY_CONFIG_FILE),
                "legacy_config_exists": LEGACY_CONFIG_FILE.exists(),
                "backup_dir": settings.get("backup_dir", ""),
                "provider_store_path": settings.get("provider_store_path", ""),
                "temp_dir": settings.get("temp_dir", ""),
                "diagnostics_dir": settings.get("diagnostics_dir", ""),
                "exports_dir": settings.get("exports_dir", ""),
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/settings/export")
    def export_settings():
        """导出当前本地配置。"""
        try:
            return jsonify({
                "schema": "codex_enhance_manager.settings.v1",
                "exported_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                "settings": redact_currency_settings(config.get_all()),
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/settings/import", methods=["POST"])
    def import_settings():
        """导入本地配置 JSON。不会写 Codex auth/config。"""
        try:
            body = request.get_json(silent=True) or {}
            imported = body.get("settings") if isinstance(body.get("settings"), dict) else body
            if not isinstance(imported, dict):
                return jsonify({"error": "Invalid settings payload"}), 400
            imported = preserve_redacted_currency_secret(imported, config.get_all())
            config.update(imported)
            ensure_app_dirs()
            return jsonify({"success": True, "settings": redact_currency_settings(config.get_all())})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/currency/settings")
    def currency_settings():
        """返回成本/币种设置，默认脱敏。"""
        try:
            return jsonify(redact_currency_settings(normalize_currency_settings(config.get_all())))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/currency/settings", methods=["POST"])
    def save_currency_settings():
        """保存成本/币种设置。只写本地 config，不写 Codex。"""
        try:
            body = request.get_json(silent=True) or {}
            update = update_currency_config(config.get_all(), body)
            config.update(update)
            return jsonify({"success": True, "settings": redact_currency_settings(normalize_currency_settings(config.get_all()))})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/currency/rate")
    def currency_rate():
        """返回汇率快照；不进行未复核在线抓取。"""
        try:
            from_currency = request.args.get("from", "")
            to_currency = request.args.get("to", "")
            return jsonify(build_rate_snapshot(config.get_all(), from_currency, to_currency))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/currency/convert", methods=["POST"])
    def currency_convert():
        """按当前汇率设置换算金额并返回本次使用的汇率快照。"""
        try:
            body = request.get_json(silent=True) or {}
            return jsonify(convert_amount(
                config.get_all(),
                body.get("amount", 0),
                body.get("from_currency") or body.get("from") or "",
                body.get("to_currency") or body.get("to") or config.get("display_currency", "USD"),
            ))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/cost/estimate", methods=["POST"])
    def cost_estimate():
        """估算单次请求成本；不写日志，不调用供应商。"""
        try:
            body = request.get_json(silent=True) or {}
            usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
            pricing = body.get("pricing") if isinstance(body.get("pricing"), dict) else {}
            provider_id = str(body.get("provider_id") or "")
            model_id = str(body.get("model_id") or "")
            native_currency = str(body.get("native_currency") or "")

            if provider_id:
                _refresh_provider_registry_path()
                provider = provider_registry.get_provider(provider_id, include_secrets=False)
                if not provider:
                    return jsonify({"error": "Provider not found"}), 404
                preview = pricing_preview_payload(provider, model_id=model_id)
                provider_pricing = preview.get("pricing") if isinstance(preview.get("pricing"), dict) else {}
                merged_pricing = dict(provider_pricing)
                merged_pricing.update(pricing)
                pricing = merged_pricing
                native_currency = native_currency or str(preview.get("native_currency") or "")

            return jsonify(estimate_request_cost(
                usage=usage,
                pricing=pricing,
                currency_settings=config.get_all(),
                native_currency=native_currency,
                display_currency=str(body.get("display_currency") or ""),
            ))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/cleanup/preview")
    def cleanup_preview():
        """预览可安全清理的本地缓存/临时目录。"""
        try:
            return jsonify({"targets": _cleanup_targets(config)})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/cleanup/execute", methods=["POST"])
    def cleanup_execute():
        """执行安全清理。只删除 allowlisted local temp/cache paths。"""
        try:
            body = request.get_json(silent=True) or {}
            if body.get("confirmation") != "CLEAN_LOCAL_CACHE":
                return jsonify({
                    "error": "Cleanup confirmation required.",
                    "required_confirmation": "CLEAN_LOCAL_CACHE",
                }), 409
            requested = set(body.get("targets") or [])
            results = []
            for target in _cleanup_targets(config):
                if requested and target["id"] not in requested:
                    continue
                results.append(_cleanup_target(target))
            ensure_app_dirs()
            return jsonify({"success": True, "results": results})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ─────────────── Provider Registry API ───────────────

    # ---------- Local uninstall cleanup API ----------

    @app.route("/api/uninstall-cleanup/status")
    def uninstall_cleanup_status():
        """Return current uninstall cleanup write-lock state."""
        return jsonify({
            "write_locked": config.is_write_locked(),
            "reason": config.write_lock_reason(),
            "required_confirmation": UNINSTALL_CLEANUP_CONFIRMATION,
        })

    @app.route("/api/uninstall-cleanup/preview")
    def uninstall_cleanup_preview():
        """Preview app-owned files that uninstall cleanup can remove."""
        try:
            return jsonify({
                "targets": _uninstall_cleanup_targets(config),
                "write_locked": config.is_write_locked(),
                "required_confirmation": UNINSTALL_CLEANUP_CONFIRMATION,
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/uninstall-cleanup/execute", methods=["POST"])
    def uninstall_cleanup_execute():
        """Remove app-owned local data and lock writes until process restart."""
        try:
            body = request.get_json(silent=True) or {}
            if body.get("confirmation") != UNINSTALL_CLEANUP_CONFIRMATION:
                return jsonify({
                    "error": "Uninstall cleanup confirmation required.",
                    "required_confirmation": UNINSTALL_CLEANUP_CONFIRMATION,
                }), 409

            reason = "Uninstall cleanup completed. Restart the app to enable writes again."
            config.lock_writes(reason)

            results = []
            for target in _uninstall_cleanup_targets(config):
                if not target.get("exists"):
                    results.append({"id": target["id"], "success": True, "skipped": True, "path": target["path"]})
                    continue
                results.append(_cleanup_target(target))
            return jsonify({
                "success": all(item.get("success") for item in results),
                "write_locked": True,
                "reason": reason,
                "results": results,
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ---------- Provider Registry API ----------

    @app.route("/api/providers")
    def list_providers():
        """读取本地 provider registry（默认脱敏）。"""
        try:
            _refresh_provider_registry_path()
            return jsonify(provider_registry.list_providers(include_secrets=False))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/providers", methods=["POST"])
    def create_provider():
        """创建 provider。只写本地 registry，不写 Codex 配置。"""
        try:
            _refresh_provider_registry_path()
            data = request.get_json(silent=True) or {}
            provider = provider_registry.create_provider(data)
            return jsonify({"success": True, "provider": provider})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/providers/<provider_id>")
    def get_provider(provider_id):
        """读取单个 provider（默认脱敏）。"""
        try:
            _refresh_provider_registry_path()
            provider = provider_registry.get_provider(provider_id, include_secrets=False)
            if not provider:
                return jsonify({"error": "Provider not found"}), 404
            return jsonify(provider)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/providers/<provider_id>", methods=["PUT", "PATCH"])
    def update_provider(provider_id):
        """更新 provider。只写本地 registry，不写 Codex 配置。"""
        try:
            _refresh_provider_registry_path()
            data = request.get_json(silent=True) or {}
            provider = provider_registry.update_provider(provider_id, data)
            if not provider:
                return jsonify({"error": "Provider not found"}), 404
            return jsonify({"success": True, "provider": provider})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/providers/<provider_id>", methods=["DELETE"])
    def delete_provider(provider_id):
        """删除本地 provider registry 记录。"""
        try:
            _refresh_provider_registry_path()
            deleted = provider_registry.delete_provider(provider_id)
            if not deleted:
                return jsonify({"error": "Provider not found"}), 404
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/provider-presets")
    def list_provider_presets():
        """读取内置 provider preset。"""
        try:
            return jsonify(provider_registry.list_presets())
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/providers/import-preset", methods=["POST"])
    def import_provider_preset():
        """从 preset 创建 provider，可带少量 override。"""
        try:
            _refresh_provider_registry_path()
            body = request.get_json(silent=True) or {}
            provider = provider_registry.import_preset(
                preset_id=body.get("preset_id", ""),
                overrides=body.get("overrides") if isinstance(body.get("overrides"), dict) else None,
            )
            return jsonify({"success": True, "provider": provider})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/providers/<provider_id>/test", methods=["POST"])
    def test_provider(provider_id):
        """本地 provider 配置校验；不做真实网络请求，不写 Codex。"""
        try:
            _refresh_provider_registry_path()
            return jsonify(provider_registry.test_provider(provider_id=provider_id))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/providers/test", methods=["POST"])
    def test_provider_payload():
        """校验未保存的 provider payload；不做真实网络请求，不写 Codex。"""
        try:
            body = request.get_json(silent=True) or {}
            return jsonify(provider_registry.test_provider(provider_data=body))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/providers/<provider_id>/responses-profile/probe-preview", methods=["GET", "POST"])
    def provider_responses_probe_preview(provider_id):
        """预览国内 Responses 请求；不联网、不写 Codex、不写 provider registry。"""
        try:
            _refresh_provider_registry_path()
            provider = provider_registry.get_provider(provider_id, include_secrets=True)
            if not provider:
                return jsonify({"error": "Provider not found"}), 404
            if request.method == "POST":
                body = request.get_json(silent=True) or {}
            else:
                body = {}
            request_json = body.get("request_json") if isinstance(body.get("request_json"), dict) else None
            compact = bool(body.get("compact", False))
            return jsonify(build_domestic_responses_probe_preview(provider, request_json=request_json, compact=compact))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/providers/responses-profile/probe-preview", methods=["POST"])
    def provider_draft_responses_probe_preview():
        """预览未保存 provider 草稿的国内 Responses 请求；纯 dry-run。"""
        try:
            body = request.get_json(silent=True) or {}
            provider = body.get("provider") if isinstance(body.get("provider"), dict) else body
            request_json = body.get("request_json") if isinstance(body.get("request_json"), dict) else None
            compact = bool(body.get("compact", False))
            return jsonify(build_domestic_responses_probe_preview(provider, request_json=request_json, compact=compact))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/providers/export")
    def export_provider_bundle():
        """导出脱敏 provider bundle，供诊断/备份使用。"""
        try:
            _refresh_provider_registry_path()
            return jsonify(provider_registry.export_bundle())
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/model-catalog/preview")
    def preview_model_catalog():
        """Unified Model Catalog 预览；不写 Codex model catalog。"""
        try:
            _refresh_provider_registry_path()
            focus_provider_id = request.args.get("focus_provider_id", "")
            return jsonify(provider_registry.preview_catalog(focus_provider_id=focus_provider_id))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/providers/<provider_id>/bulk-models", methods=["POST"])
    def bulk_update_provider_models(provider_id):
        """
        批量更新 provider 下的模型选择状态。
        支持：select_all、deselect_all、select_vision、select_low_cost、select_high_context。
        """
        try:
            _refresh_provider_registry_path()
            body = request.get_json(silent=True) or {}
            action = body.get("action", "")
            criteria = body.get("criteria")
            result = provider_registry.bulk_update_models(provider_id, action, criteria)
            if not result.get("success"):
                return jsonify(result), 404 if result.get("error") == "Provider not found" else 400
            return jsonify(result)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/providers/<provider_id>/visibility", methods=["POST"])
    def set_provider_visibility_api(provider_id):
        """设置 provider 的 catalog visibility。"""
        try:
            _refresh_provider_registry_path()
            body = request.get_json(silent=True) or {}
            visibility = body.get("visibility", "")
            result = provider_registry.set_provider_visibility(provider_id, visibility)
            if not result.get("success"):
                return jsonify(result), 404
            return jsonify(result)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/model-rotation/simulate", methods=["POST"])
    def simulate_model_rotation():
        """
        AMR 路由模拟：根据当前 provider registry 状态返回路由决策。

        设计意图：
          - 这是 Route Simulator UI 的后端支撑：用户选择 capability 和 model，
            后端基于当前 provider registry 动态构建 AMR 候选列表并执行路由。
          - 动态候选构建：将每个 provider 的每个启用模型转换为一个 candidate，
            always_visible 的 provider 优先级设为 1（最高），其余为 2。
          - 纯模拟：不触发真实网络请求，只返回路由决策和 explanation，
            供用户预览路由行为。

        边界条件：
          - 若无任何启用 provider/model，AMR route 会返回 "No candidates" 错误，
            前端展示即可，非 500 异常。
        """
        try:
            _refresh_provider_registry_path()
            body = request.get_json(silent=True) or {}
            capability = body.get("capability", "text")
            model = body.get("model", "")

            providers_data = provider_registry.list_providers(include_secrets=False)
            candidates = []
            for p in providers_data.get("providers", []):
                if not p.get("enabled"):
                    continue
                caps = p.get("capabilities", {})
                for m in p.get("models", []):
                    if not m.get("enabled", True):
                        continue
                    candidates.append({
                        "id": f"{p['id']}/{m['id']}",
                        "provider_id": p["id"],
                        "model_id": m["id"],
                        "priority": 1 if p.get("catalog_visibility") == "always_visible" else 2,
                        "capabilities": {
                            "text": caps.get("text", True),
                            "vision": caps.get("vision", False),
                            "tools": caps.get("tools", False),
                            "reasoning": caps.get("reasoning", False),
                            "images": caps.get("images", False),
                            "videos": caps.get("videos", False),
                        },
                        "context_window": m.get("context_window", 0),
                    })

            from model_rotation import AdaptiveModelRotation
            amr = AdaptiveModelRotation([{
                "id": "default",
                "name": "Default Group",
                "candidates": candidates,
            }])

            decision = amr.route(
                group_id="default",
                required_capabilities={capability},
            )
            return jsonify(decision)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ─────────────── AMR Registry API ───────────────

    @app.route("/api/amr/groups")
    def list_amr_groups():
        """列出所有 rotation groups。"""
        try:
            return jsonify(amr_registry.list_groups())
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/amr/groups", methods=["POST"])
    def create_amr_group():
        """创建 AMR rotation group。"""
        try:
            data = request.get_json(silent=True) or {}
            group = amr_registry.create_group(data)
            return jsonify({"success": True, "group": group})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/amr/groups/<group_id>")
    def get_amr_group(group_id):
        """读取单个 rotation group。"""
        try:
            group = amr_registry.get_group(group_id)
            if not group:
                return jsonify({"error": "Group not found"}), 404
            return jsonify(group)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/amr/groups/<group_id>", methods=["PUT", "PATCH"])
    def update_amr_group(group_id):
        """更新 AMR rotation group。"""
        try:
            data = request.get_json(silent=True) or {}
            group = amr_registry.update_group(group_id, data)
            if not group:
                return jsonify({"error": "Group not found"}), 404
            return jsonify({"success": True, "group": group})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/amr/groups/<group_id>", methods=["DELETE"])
    def delete_amr_group(group_id):
        """删除 AMR rotation group。"""
        try:
            deleted = amr_registry.delete_group(group_id)
            if not deleted:
                return jsonify({"error": "Group not found"}), 404
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/amr/sync-from-providers", methods=["POST"])
    def sync_amr_from_providers():
        """从当前 provider registry 同步生成 AMR 候选。"""
        try:
            _refresh_provider_registry_path()
            group = amr_registry.build_from_providers(provider_registry)
            return jsonify({"success": True, "group": group})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/amr/route", methods=["POST"])
    def amr_route():
        """执行 AMR 路由测试。"""
        try:
            body = request.get_json(silent=True) or {}
            group_id = body.get("group_id", "")
            if not group_id:
                return jsonify({"error": "group_id is required"}), 400
            capabilities = body.get("capabilities", ["text"])
            if isinstance(capabilities, list):
                capabilities = set(capabilities)
            context = int(body.get("context", 0))
            decision = amr_registry.route(
                group_id=group_id,
                request_capabilities=capabilities,
                required_context=context,
            )
            return jsonify(decision)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ─────────────── Codex Integration API ───────────────

    @app.route("/api/codex-integration/status")
    def codex_integration_status():
        """读取当前 Codex config/auth 状态，用于 Diff Preview 和诊断。"""
        try:
            mgr = CodexConfigManager()
            config_data = mgr.read_config()
            auth_data = mgr.read_auth()
            return jsonify({
                "config": config_data,
                "auth_redacted": redact_auth_for_preview(auth_data),
                "auth_mode": mgr.get_auth_mode(),
                "codex_home": str(mgr.codex_home),
                "config_path": str(mgr.config_path),
                "auth_path": str(mgr.auth_path),
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/codex-integration/preview", methods=["POST"])
    def codex_integration_preview():
        """预览写入 local proxy provider 后的 diff；不做任何写入。"""
        try:
            body = request.get_json(silent=True) or {}
            mgr = CodexConfigManager()
            preview = mgr.preview_write_provider(
                proxy_base_url=body.get("proxy_base_url", "http://localhost:8080/v1"),
                proxy_model=body.get("proxy_model", "auto"),
            )
            preview["auth_redacted"] = redact_auth_for_preview(mgr.read_auth())
            return jsonify(preview)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/codex-integration/apply", methods=["POST"])
    def codex_integration_apply():
        """应用 local proxy provider 配置到 Codex config.toml。保留官方登录态。"""
        try:
            body = request.get_json(silent=True) or {}
            denied = _require_codex_mutation_confirmation(body, "apply_codex_provider_config")
            if denied:
                return denied
            mgr = CodexConfigManager()
            result = mgr.write_provider_config(
                proxy_base_url=body.get("proxy_base_url", "http://localhost:8080/v1"),
                proxy_model=body.get("proxy_model", "auto"),
                preserve_official_auth=body.get("preserve_official_auth", True),
            )
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/codex-integration/backups")
    def codex_integration_backups():
        """列出 Codex config/auth 备份。"""
        try:
            mgr = CodexConfigManager()
            return jsonify({"backups": mgr.list_all_backups()})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/codex-integration/restore-config", methods=["POST"])
    def codex_integration_restore_config():
        """从备份恢复 config.toml。"""
        try:
            body = request.get_json(silent=True) or {}
            denied = _require_codex_mutation_confirmation(body, "restore_codex_config")
            if denied:
                return denied
            mgr = CodexConfigManager()
            result = mgr.restore_config(body.get("backup_path", ""))
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/codex-integration/restore-auth", methods=["POST"])
    def codex_integration_restore_auth():
        """从备份恢复 auth.json。"""
        try:
            body = request.get_json(silent=True) or {}
            denied = _require_codex_mutation_confirmation(body, "restore_codex_auth")
            if denied:
                return denied
            mgr = CodexConfigManager()
            result = mgr.restore_auth(body.get("backup_path", ""))
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/codex-integration/disable-proxy-provider", methods=["POST"])
    def codex_integration_disable_proxy_provider():
        """
        禁用本地代理 provider：从 Codex config.toml 中移除 codex_enhance_manager
        provider 配置，恢复到默认状态。保留官方登录态。
        """
        try:
            body = request.get_json(silent=True) or {}
            denied = _require_codex_mutation_confirmation(body, "disable_codex_proxy_provider")
            if denied:
                return denied
            mgr = CodexConfigManager()

            # 备份当前配置
            if mgr.config_path.exists():
                backup_file(str(mgr.config_path), mgr.backup_dir)

            current_config = mgr.read_config()
            # 移除 codex_enhance_manager 相关配置
            if current_config.get("model_provider") == "codex_enhance_manager":
                current_config["model_provider"] = ""
            if "codex_enhance_manager" in current_config.get("providers", {}):
                del current_config["providers"]["codex_enhance_manager"]

            save_config_toml(str(mgr.config_path), current_config)
            return jsonify({
                "success": True,
                "restart_required": True,
                "message": "本地代理 provider 已禁用。需要重启 Codex 使变更生效。",
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/codex-integration/restart-codex", methods=["POST"])
    def codex_integration_restart_codex():
        """
        重启 Codex：先 kill 再 start。同步当前 provider/model 配置后启动。
        """
        try:
            body = request.get_json(silent=True) or {}
            denied = _require_codex_mutation_confirmation(body, "restart_codex_process")
            if denied:
                return denied
            use_cpp = body.get("use_codex_plus_plus", config.get("use_codex_plus_plus", False))

            # 可选：先同步配置
            if body.get("sync_before_restart", True):
                sync_payload, sync_status = _run_sync_with_backup(backup_mgr)
                if sync_status >= 400:
                    return jsonify({"error": "同步失败，取消重启", "sync": sync_payload}), 500

            # Kill Codex
            kill_ok, kill_msg = kill_codex()

            # Start Codex
            start_ok, start_msg = start_codex(
                use_codex_plus_plus=use_cpp,
                codex_plus_plus_path=config.get("codex_plus_plus_path", ""),
                codex_cli_path=config.get("codex_cli_path", ""),
            )

            return jsonify({
                "success": start_ok,
                "killed": kill_ok,
                "kill_message": kill_msg,
                "start_message": start_msg,
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ─────────────── Local Proxy API ───────────────

    @app.route("/api/proxy/status")
    def proxy_status():
        """获取本地代理状态。"""
        try:
            return jsonify(proxy_server.status())
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/proxy/start", methods=["POST"])
    def proxy_start():
        """启动本地代理服务器。"""
        try:
            body = request.get_json(silent=True) or {}
            new_port = body.get("port")
            if new_port is not None:
                if isinstance(new_port, bool) or not isinstance(new_port, int) or new_port < 1 or new_port > 65535:
                    return jsonify({"error": "Invalid proxy port", "status": proxy_server.status()}), 400
                proxy_server.port = new_port
                config.set("proxy_port", new_port)
            new_store = body.get("provider_store_path")
            if new_store:
                proxy_server.provider_store_path = new_store
            ok = proxy_server.start()
            if ok:
                status = proxy_server.status()
                config.set("proxy_port", status.get("port", proxy_server.port))
                return jsonify({"success": True, "status": status})
            return jsonify({
                "error": "未能在配置端口及后续端口中找到可用代理端口",
                "status": proxy_server.status(),
            }), 409
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/proxy/stop", methods=["POST"])
    def proxy_stop():
        """停止本地代理服务器。"""
        try:
            proxy_server.stop()
            return jsonify({"success": True, "status": proxy_server.status()})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/proxy/test-route", methods=["POST"])
    def proxy_test_route():
        """
        测试代理路由：给定 model ID，返回会路由到哪个 provider，不触发真实网络请求。
        """
        try:
            body = request.get_json(silent=True) or {}
            model_id = body.get("model", "")
            from proxy_server import _resolve_provider_for_model
            provider = _resolve_provider_for_model(model_id)
            if provider:
                return jsonify({
                    "success": True,
                    "provider_id": provider.get("id"),
                    "display_name": provider.get("display_name"),
                    "base_url": provider.get("base_url"),
                    "api_format": provider.get("api_format"),
                    "short_alias": provider.get("short_alias"),
                })
            return jsonify({"success": False, "error": f"No provider found for model '{model_id}'"}), 404
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ─────────────── Move Repair API ───────────────

    @app.route("/api/move-repair/status/<thread_id>")
    def move_repair_status(thread_id):
        """读取 thread 元数据（SQLite + JSONL 合并视图）。"""
        try:
            mgr = MoveRepairManager()
            data = mgr.read_thread_metadata(thread_id)
            return jsonify({"success": True, "metadata": data})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/move-repair/dry-run", methods=["POST"])
    def move_repair_dry_run():
        """预演移动：验证 target_path 是否有效 Git 仓库，不修改数据。"""
        try:
            body = request.get_json(silent=True) or {}
            thread_id = body.get("thread_id", "")
            target_path = body.get("target_path", "")
            if not thread_id or not target_path:
                return jsonify({"error": "thread_id 和 target_path 必填"}), 400
            mgr = MoveRepairManager()
            result = mgr.dry_run_move(thread_id, target_path)
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/move-repair/execute", methods=["POST"])
    def move_repair_execute():
        """执行移动：原子更新 SQLite、JSONL、Index，失败自动回滚。"""
        try:
            body = request.get_json(silent=True) or {}
            thread_id = body.get("thread_id", "")
            target_path = body.get("target_path", "")
            if not thread_id or not target_path:
                return jsonify({"error": "thread_id 和 target_path 必填"}), 400
            mgr = MoveRepairManager()
            result = mgr.execute_move(thread_id, target_path)
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/move-repair/verify/<thread_id>")
    def move_repair_verify(thread_id):
        """一致性校验：检查三端 cwd 是否对齐且指向有效 Git 仓库。"""
        try:
            mgr = MoveRepairManager()
            result = mgr.verify_consistency(thread_id)
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/move-repair/repair-current", methods=["POST"])
    def move_repair_repair_current():
        """检测当前工作目录与 thread cwd 匹配关系，提供修复建议。"""
        try:
            mgr = MoveRepairManager()
            result = mgr.repair_current_thread()
            return jsonify(result)
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

    # ─────────────── 诊断 API ───────────────

    @app.route("/api/diagnostics")
    def get_diagnostics():
        """
        获取完整诊断信息。

        设计意图：
          - 默认返回脱敏数据，防止用户截图或分享时泄露 api_key。
          - 加 ?include_secrets=1 可返回完整版（需管理员权限前端校验）。
        """
        try:
            include_secrets = request.args.get("include_secrets", "0") == "1"
            if include_secrets:
                data = diagnostics_collector.collect_all()
            else:
                data = diagnostics_collector.collect_redacted()
            return jsonify(data)
        except Exception as e:
            diagnostics_collector.record_error("api.diagnostics", str(e))
            return jsonify({"error": str(e)}), 500

    @app.route("/api/diagnostics/export", methods=["POST"])
    def export_diagnostics():
        """
        导出安全诊断包（JSON 下载）。

        工程权衡：
          - 使用 POST 而非 GET：避免浏览器预加载或缓存意外触发下载。
          - 返回 Content-Type: application/json，前端可用 Blob + URL.createObjectURL
            模拟下载，无需后端发送 attachment 头。
        """
        try:
            bundle = diagnostics_collector.export_safe_bundle()
            return bundle, 200, {"Content-Type": "application/json; charset=utf-8"}
        except Exception as e:
            diagnostics_collector.record_error("api.diagnostics.export", str(e))
            return jsonify({"error": str(e)}), 500

    @app.route("/api/diagnostics/test-provider/<provider_id>", methods=["POST"])
    def test_provider_connectivity(provider_id):
        """
        测试单个 provider 的网络连通性。

        设计意图：
          - 与 /api/providers/<id>/test 区分：后者只做本地配置校验，
            本端点做真实网络探测（HEAD 请求）。
        """
        try:
            _refresh_provider_registry_path()
            result = diagnostics_collector.check_provider_connectivity(provider_id)
            status_code = 200 if result.get("success") else 503
            return jsonify(result), status_code
        except Exception as e:
            diagnostics_collector.record_error("api.diagnostics.test_provider", str(e))
            return jsonify({"error": str(e)}), 500

    @app.route("/api/diagnostics/system")
    def get_diagnostics_system():
        """返回系统环境信息（轻量子集，供快速排障）。"""
        try:
            data = diagnostics_collector.collect_all()
            return jsonify({"system": data.get("system", {})})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    return app


def _clamp_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


def _merge_cache_usage_sources(
    data: Dict[str, Any],
    rollout_cache_data: Dict[str, Any] | None,
    cc_cache_data: Dict[str, Any] | None,
) -> None:
    rollout_cache_data = rollout_cache_data or {}
    cc_cache_data = cc_cache_data or {}

    rollout_read = _safe_int(rollout_cache_data.get("cache_read_tokens"))
    rollout_creation = _safe_int(rollout_cache_data.get("cache_creation_tokens"))
    cc_read = _safe_int(cc_cache_data.get("cache_read_tokens"))
    cc_creation = _safe_int(cc_cache_data.get("cache_creation_tokens"))

    data["codex_rollout_cache_supported"] = bool(rollout_cache_data.get("cache_supported"))
    data["codex_rollout_cache_read_tokens"] = rollout_read
    data["codex_rollout_cache_creation_tokens"] = rollout_creation
    data["codex_rollout_cache_total_tokens"] = rollout_read + rollout_creation
    data["codex_rollout_files_scanned"] = _safe_int(rollout_cache_data.get("rollout_files_scanned"))
    data["codex_rollout_paths_discovered"] = _safe_int(rollout_cache_data.get("rollout_paths_discovered"))
    data["codex_rollout_token_count_events"] = _safe_int(rollout_cache_data.get("rollout_token_count_events"))
    data["codex_rollout_cache_field_events"] = _safe_int(rollout_cache_data.get("rollout_cache_field_events"))
    data["codex_rollout_usage_sources"] = rollout_cache_data.get("rollout_usage_sources", [])
    data["codex_rollout_cache_note"] = rollout_cache_data.get("cache_note", "")

    data["cc_switch_cache_supported"] = bool(cc_cache_data.get("cache_supported"))
    data["cc_switch_cache_read_tokens"] = cc_read
    data["cc_switch_cache_creation_tokens"] = cc_creation
    data["cc_switch_cache_total_tokens"] = cc_read + cc_creation
    data["cc_switch_cache_tables"] = cc_cache_data.get("cache_tables", [])
    data["cc_switch_cache_note"] = cc_cache_data.get("cache_note", "")
    data["cc_switch_cache_strategy"] = cc_cache_data.get("cache_strategy", "")
    data["cc_switch_cache_rollup_used"] = bool(cc_cache_data.get("cache_rollup_used"))

    data["cache_supported"] = data["codex_rollout_cache_supported"] or data["cc_switch_cache_supported"]
    data["cache_tables"] = data["cc_switch_cache_tables"]
    data["cache_sources"] = []
    data["cache_overlap_risk"] = False
    data["cache_merge_strategy"] = "none"

    if data["codex_rollout_cache_supported"]:
        data["cache_sources"].append("codex_rollout")
    if data["cc_switch_cache_supported"]:
        data["cache_sources"].append("cc_switch_db")

    if data["codex_rollout_cache_supported"]:
        data["cache_read_tokens"] = rollout_read
        data["cache_creation_tokens"] = rollout_creation
        data["cache_merge_strategy"] = "codex_rollout_primary"
        if data["cc_switch_cache_supported"]:
            data["cache_overlap_risk"] = True
            data["cache_merge_strategy"] = "codex_rollout_primary_cc_switch_separate"
    elif data["cc_switch_cache_supported"]:
        data["cache_read_tokens"] = cc_read
        data["cache_creation_tokens"] = cc_creation
        data["cache_merge_strategy"] = "cc_switch_db"
    else:
        data["cache_read_tokens"] = 0
        data["cache_creation_tokens"] = 0

    data["cache_total_tokens"] = data["cache_read_tokens"] + data["cache_creation_tokens"]

    notes = [
        note
        for note in (
            data.get("codex_rollout_cache_note"),
            data.get("cc_switch_cache_note"),
        )
        if note
    ]
    if not notes and not cc_cache_data:
        notes.append("No proxy cache database is configured.")
    data["cache_note"] = " ".join(notes)


def _attach_usage_source_summary(data: Dict[str, Any], proxy_status: Dict[str, Any] | None = None) -> None:
    proxy_status = proxy_status or {}
    rollout_discovered = _safe_int(data.get("codex_rollout_paths_discovered"))
    rollout_scanned = _safe_int(data.get("codex_rollout_files_scanned"))
    cc_configured = bool(data.get("cc_switch_db_configured"))
    cc_supported = bool(data.get("cc_switch_cache_supported"))
    cc_strategy = str(data.get("cc_switch_cache_strategy") or "")
    proxy_running = bool(proxy_status.get("running"))

    sources = [
        {
            "id": "codex_db",
            "label": "Codex DB",
            "badge": "Codex DB",
            "status": "active",
            "active": True,
            "kind": "total_tokens",
            "tooltip": (
                "Codex DB threads.tokens_used stores collapsed total tokens only; "
                "cache read/write details require Codex rollout or proxy/CC Switch sources."
            ),
        },
        {
            "id": "codex_rollout",
            "label": "Codex rollout",
            "badge": "rollout",
            "status": "active" if data.get("codex_rollout_cache_supported") else (
                "available" if rollout_discovered else "missing"
            ),
            "active": bool(data.get("codex_rollout_cache_supported")),
            "kind": "cache_tokens",
            "tooltip": (
                f"Scanned {rollout_scanned} of {rollout_discovered} discovered rollout files. "
                "Reads token_count events and maps cached_input_tokens to cache read tokens."
            ),
        },
        {
            "id": "local_proxy",
            "label": "Local proxy",
            "badge": "local proxy",
            "status": "running" if proxy_running else "stopped",
            "active": proxy_running,
            "kind": "proxy_runtime",
            "tooltip": (
                "Local proxy can observe routed requests. Request-log aggregation is a separate TODO; "
                "CC Switch/proxy DB fields are shown when configured."
            ),
        },
        {
            "id": "cc_switch_db",
            "label": "CC Switch DB",
            "badge": "CC Switch DB",
            "status": "active" if cc_supported else ("configured" if cc_configured else "not_configured"),
            "active": cc_supported,
            "kind": "cache_tokens",
            "tooltip": (
                (
                    f"{data.get('cc_switch_cache_note')} Strategy: {cc_strategy}."
                    if cc_strategy
                    else data.get("cc_switch_cache_note")
                )
                or "Configure a proxy cache database to read cache_read_tokens/cache_creation_tokens."
            ),
        },
    ]
    data["usage_sources"] = sources
    data["usage_source_badges"] = [
        {
            "id": source["id"],
            "label": source["badge"],
            "status": source["status"],
            "active": source["active"],
            "tooltip": source["tooltip"],
        }
        for source in sources
    ]


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


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


def _cleanup_targets(config: Config) -> list[Dict]:
    """Build allowlisted cleanup targets with size estimates."""
    root = app_data_dir()
    configured = config.get_all()
    candidates = [
        ("temp", Path(configured.get("temp_dir") or root / "temp"), root),
        ("diagnostics", Path(configured.get("diagnostics_dir") or root / "diagnostics"), root),
        ("exports", Path(configured.get("exports_dir") or root / "exports"), root),
        ("repo_pycache", Path.cwd() / "__pycache__", Path.cwd()),
        ("repo_pytest_cache", Path.cwd() / ".pytest_cache", Path.cwd()),
        ("tests_pycache", Path.cwd() / "tests" / "__pycache__", Path.cwd()),
    ]
    targets = []
    for target_id, path, safe_root in candidates:
        resolved = path.expanduser()
        safe = is_within(resolved, safe_root)
        targets.append({
            "id": target_id,
            "path": str(resolved),
            "exists": resolved.exists(),
            "safe": safe,
            "size_bytes": _path_size(resolved) if resolved.exists() and safe else 0,
            "kind": "directory" if resolved.is_dir() else "file",
        })
    return targets


def _uninstall_cleanup_targets(config: Config) -> list[Dict]:
    """Build app-owned uninstall cleanup targets.

    This intentionally excludes ~/.codex auth/config and any user-custom path
    outside the app-owned Documents folder.
    """
    root = app_data_dir()
    targets = [
        _cleanup_target_descriptor(
            "app_data_dir",
            root,
            safe=True,
            description="Documents app data directory",
            effect="Removes settings, provider registry, exports, diagnostics, temp files, and app backups.",
        ),
        _cleanup_target_descriptor(
            "legacy_config_file",
            LEGACY_CONFIG_FILE,
            safe=LEGACY_CONFIG_FILE == Path.home() / ".codex_gui_config.json",
            description="Legacy settings JSON",
            effect="Removes the old root-level settings file if it still exists.",
        ),
        _cleanup_target_descriptor(
            "legacy_app_dir",
            LEGACY_APP_DIR,
            safe=LEGACY_APP_DIR == Path.home() / ".codex_enhance_manager",
            description="Legacy app data directory",
            effect="Removes the old provider/cache directory if it still exists.",
        ),
    ]

    provider_store_raw = str(config.get("provider_store_path", "") or "").strip()
    provider_store = Path(provider_store_raw).expanduser() if provider_store_raw else None
    if provider_store and provider_store.exists() and not is_within(provider_store, root) and provider_store != LEGACY_APP_DIR / "providers.json":
        targets.append(_cleanup_target_descriptor(
            "external_provider_store",
            provider_store,
            safe=False,
            description="Custom provider registry outside app data",
            effect="Not removed automatically; export or remove it manually if desired.",
        ))
    return targets


def _cleanup_target_descriptor(target_id: str, path: Path, safe: bool, description: str, effect: str) -> Dict:
    resolved = path.expanduser()
    exists = resolved.exists()
    return {
        "id": target_id,
        "path": str(resolved),
        "exists": exists,
        "safe": safe,
        "size_bytes": _path_size(resolved) if exists and safe else 0,
        "kind": "directory" if resolved.is_dir() else "file",
        "description": description,
        "effect": effect,
    }


def _cleanup_target(target: Dict) -> Dict:
    path = Path(target["path"])
    if not target.get("safe"):
        return {"id": target["id"], "success": False, "error": "Target is outside cleanup allowlist"}
    if not path.exists():
        return {"id": target["id"], "success": True, "skipped": True, "path": str(path)}
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        return {"id": target["id"], "success": True, "path": str(path)}
    except Exception as exc:
        return {"id": target["id"], "success": False, "path": str(path), "error": str(exc)}


def _path_size(path: Path) -> int:
    try:
        if path.is_file():
            return path.stat().st_size
        total = 0
        for item in path.rglob("*"):
            if item.is_file():
                try:
                    total += item.stat().st_size
                except OSError:
                    pass
        return total
    except OSError:
        return 0


def _resolve_current_context_window(config: Config, provider_registry: ProviderRegistry) -> Dict:
    """Best-effort current model context window for monitor display."""
    result = {
        "current_model": "",
        "current_model_provider": "",
        "current_context_window": 0,
        "current_context_source": "",
    }
    try:
        codex_home = resolve_codex_home()
        config_data = _load_config_toml(str(codex_home / "config.toml"))
        model = str(config_data.get("model") or "")
        provider = str(config_data.get("model_provider") or "")
        result["current_model"] = model
        result["current_model_provider"] = provider
        if not model:
            return result

        provider_registry.store_path = Path(config.get("provider_store_path", "") or DEFAULT_STORE_PATH).expanduser()
        preview = provider_registry.preview_catalog(focus_provider_id="")
        for entry in preview.get("entries", []):
            if model in {entry.get("codex_model_id"), entry.get("upstream_model_id")}:
                result["current_context_window"] = int(entry.get("context_window") or 0)
                result["current_context_source"] = "provider_registry"
                return result
    except Exception as exc:
        result["current_context_error"] = str(exc)
    return result
