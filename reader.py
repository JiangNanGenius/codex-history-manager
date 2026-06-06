"""
reader.py - jsonl 文件读取器
关键设计：流式逐行读取，绝不一次性加载大文件到内存
支持 8GB+ 超大文件，只读取前 N 条有效消息
"""
import os
import json
from typing import List, Dict, Generator, Optional
from pathlib import Path


# 超大文件阈值（字节）
LARGE_FILE_THRESHOLD = 500 * 1024 * 1024  # 500MB


def get_file_size_mb(path: str) -> float:
    """获取文件大小（MB）"""
    try:
        return os.path.getsize(path) / (1024 * 1024)
    except Exception:
        return 0.0


def iter_jsonl_lines(path: str) -> Generator[Dict, None, None]:
    """
    流式逐行迭代 jsonl 文件
    每次只在内存中保留一行，完全避免大文件问题
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for raw_line in f:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    yield json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        raise
    except Exception as e:
        raise IOError(f"读取文件失败: {e}")


def read_messages(
    path: str,
    max_messages: int = 2000,
    large_file_limit: int = 500,
) -> Dict:
    """
    读取 jsonl 文件中的对话消息

    Codex jsonl 格式说明：
      每行结构 = {"timestamp": "...", "type": "response_item"|"event_msg"|..., "payload": {...}}
      - payload.role = "user" | "assistant" | "developer" | "function_call" | "function_call_output" 等
      - payload.content = 字符串 或 列表（[{"type":"input_text"/"output_text", "text":"..."}]）

    返回:
        {
            "messages": [...],
            "total_lines_read": int,
            "truncated": bool,
            "file_size_mb": float,
            "is_large_file": bool,
            "error": str or None,
        }
    """
    result = {
        "messages": [],
        "total_lines_read": 0,
        "truncated": False,
        "file_size_mb": 0.0,
        "is_large_file": False,
        "error": None,
    }

    if not path or not os.path.exists(path):
        result["error"] = f"文件不存在: {path}"
        return result

    size_mb = get_file_size_mb(path)
    result["file_size_mb"] = size_mb
    is_large = size_mb > large_file_limit
    result["is_large_file"] = is_large

    # 超大文件限制读取条数
    effective_max = max_messages if not is_large else min(max_messages, 500)

    # 需要显示的 role 白名单
    SHOW_ROLES = {"user", "assistant", "developer", "system", "tool"}

    messages = []
    lines_read = 0

    try:
        for obj in iter_jsonl_lines(path):
            lines_read += 1
            timestamp = obj.get("timestamp", "")

            # Codex 新格式：payload 包裹
            payload = obj.get("payload")
            if payload and isinstance(payload, dict):
                msg = _extract_message(payload, timestamp)
                if msg and msg["role"] in SHOW_ROLES:
                    messages.append(msg)
                    if len(messages) >= effective_max:
                        result["truncated"] = True
                        break
            else:
                # 兼容旧格式（直接有 role 字段）
                role = obj.get("role", "")
                if role in SHOW_ROLES:
                    msg = _extract_message(obj, timestamp)
                    if msg:
                        messages.append(msg)
                        if len(messages) >= effective_max:
                            result["truncated"] = True
                            break

    except Exception as e:
        result["error"] = str(e)

    result["messages"] = messages
    result["total_lines_read"] = lines_read
    return result


def _extract_message(payload: Dict, timestamp: str = "") -> Optional[Dict]:
    """从 payload 字典中提取消息内容（支持新旧两种格式）"""
    role = payload.get("role", payload.get("type", "unknown"))
    content = payload.get("content", "")
    ts = payload.get("timestamp", timestamp)

    # content 可能是字符串或列表（Codex 用 input_text / output_text）
    text = ""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                item_type = item.get("type", "")
                if item_type in ("text", "input_text", "output_text"):
                    parts.append(item.get("text", ""))
                elif item_type == "tool_result":
                    parts.append(f"[工具结果: {str(item.get('content', ''))[:200]}]")
                elif item_type == "tool_use":
                    parts.append(f"[调用工具: {item.get('name', '')}]")
                elif item_type == "input_image":
                    parts.append("[图片]")
                elif item_type == "refusal":
                    parts.append(f"[拒绝: {item.get('refusal', '')}]")
                else:
                    # 其他类型尝试提取 text
                    if "text" in item:
                        parts.append(item["text"])
            elif isinstance(item, str):
                parts.append(item)
        text = "\n".join(p for p in parts if p)
    elif isinstance(content, dict):
        text = str(content)

    if not text and role in ("function_call_output",):
        # 工具输出
        text = f"[工具输出: {str(payload.get('output', ''))[:300]}]"

    if not text:
        return None

    return {
        "role": role,
        "content": text,
        "timestamp": ts,
    }


def read_session_meta(path: str) -> Dict:
    """
    只读取 jsonl 文件的前几行，提取元数据摘要
    用于备份时快速获取会话摘要，不读取全文
    """
    meta = {
        "first_message": "",
        "message_count": 0,
        "file_size_mb": get_file_size_mb(path),
    }
    if not path or not os.path.exists(path):
        return meta

    count = 0
    first = ""
    try:
        for obj in iter_jsonl_lines(path):
            timestamp = obj.get("timestamp", "")
            # 支持 payload 包裹格式
            payload = obj.get("payload", obj)
            role = payload.get("role", "")
            content = payload.get("content", "")
            if role == "user" and not first:
                if isinstance(content, str):
                    first = content[:200]
                elif isinstance(content, list) and content:
                    item = content[0]
                    if isinstance(item, dict):
                        first = item.get("text", str(item))[:200]
                    else:
                        first = str(item)[:200]
            if role in ("user", "assistant"):
                count += 1
            if count >= 5 and first:
                break
    except Exception:
        pass

    meta["first_message"] = first
    meta["message_count"] = count
    return meta


def export_to_markdown(path: str, title: str = "", max_messages: int = 5000) -> str:
    """将 jsonl 导出为 Markdown 格式字符串"""
    data = read_messages(path, max_messages=max_messages, large_file_limit=99999)
    lines = [f"# {title or os.path.basename(path)}", ""]

    if data["is_large_file"]:
        lines.append(f"> ⚠️ 大文件 ({data['file_size_mb']:.0f} MB)，仅显示前 {len(data['messages'])} 条消息")
        lines.append("")

    for msg in data["messages"]:
        role = msg["role"]
        ts = msg.get("timestamp", "")
        ts_str = f" *{ts[:19]}*" if ts else ""
        content = msg["content"] or ""

        if role == "user":
            lines.append(f"**用户**{ts_str}")
        elif role == "assistant":
            lines.append(f"**助手**{ts_str}")
        else:
            lines.append(f"**{role}**{ts_str}")

        lines.append("")
        lines.append(content)
        lines.append("")
        lines.append("---")
        lines.append("")

    if data["truncated"]:
        lines.append(f"*（已截断，共读取 {data['total_lines_read']} 行）*")

    return "\n".join(lines)


def export_to_text(path: str, title: str = "", max_messages: int = 5000) -> str:
    """将 jsonl 导出为纯文本格式"""
    data = read_messages(path, max_messages=max_messages, large_file_limit=99999)
    lines = [f"=== {title or os.path.basename(path)} ===", ""]

    if data["is_large_file"]:
        lines.append(f"[大文件 {data['file_size_mb']:.0f} MB，仅显示前 {len(data['messages'])} 条]")
        lines.append("")

    for msg in data["messages"]:
        role = msg["role"].upper()
        ts = msg.get("timestamp", "")[:19]
        content = msg["content"] or ""
        lines.append(f"[{role}] {ts}")
        lines.append(content)
        lines.append("")

    return "\n".join(lines)
