"""Codex Desktop enhancement injection through Chromium DevTools Protocol.

The approach mirrors Codex++ at a smaller, app-owned scale: launch Codex with a
remote debugging port, discover renderer targets, and inject a script with CDP.
No Codex installation files are modified.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import time
import urllib.request
from typing import Any, Dict, List, Optional


DEFAULT_CDP_PORT = 51236
DEFAULT_BACKEND_PORT = 51234


def backend_url_from_env(default_port: int = DEFAULT_BACKEND_PORT) -> str:
    port = os.environ.get("CODEX_ENHANCE_MANAGER_PORT") or str(default_port)
    try:
        port_int = int(port)
    except (TypeError, ValueError):
        port_int = default_port
    return f"http://127.0.0.1:{port_int}"


def build_injection_script(backend_url: str = "") -> str:
    backend = (backend_url or backend_url_from_env()).rstrip("/")
    payload = {
        "backend": backend,
        "marker": "codex-enhance-manager-v1",
    }
    config_json = json.dumps(payload, ensure_ascii=False)
    return f"""
(() => {{
  const config = {config_json};
  if (window.__codexEnhanceManagerInjected === config.marker) return;
  window.__codexEnhanceManagerInjected = config.marker;

  const rootId = 'codex-enhance-manager-menu';
  const existing = document.getElementById(rootId);
  if (existing) existing.remove();

  const style = document.createElement('style');
  style.textContent = `
    #${{rootId}} {{
      position: fixed;
      top: 10px;
      right: 12px;
      z-index: 2147483647;
      font: 12px/1.4 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: #e5e7eb;
    }}
    #${{rootId}} button {{
      border: 1px solid rgba(148, 163, 184, .35);
      background: rgba(15, 23, 42, .92);
      color: #f8fafc;
      border-radius: 8px;
      padding: 6px 9px;
      cursor: pointer;
      box-shadow: 0 8px 28px rgba(15, 23, 42, .28);
    }}
    #${{rootId}} .cem-panel {{
      display: none;
      margin-top: 6px;
      min-width: 188px;
      border: 1px solid rgba(148, 163, 184, .25);
      border-radius: 8px;
      overflow: hidden;
      background: rgba(2, 6, 23, .96);
      box-shadow: 0 18px 42px rgba(2, 6, 23, .45);
    }}
    #${{rootId}}.open .cem-panel {{ display: block; }}
    #${{rootId}} a, #${{rootId}} .cem-status {{
      display: block;
      padding: 8px 10px;
      color: #dbeafe;
      text-decoration: none;
      border-top: 1px solid rgba(148, 163, 184, .14);
      white-space: nowrap;
    }}
    #${{rootId}} a:hover {{ background: rgba(59, 130, 246, .18); }}
    #${{rootId}} .cem-status {{ color: #a7f3d0; }}
  `;
  document.documentElement.appendChild(style);

  const root = document.createElement('div');
  root.id = rootId;
  root.innerHTML = `
    <button type="button" aria-label="Codex Enhance Manager">Codex Enhance</button>
    <div class="cem-panel">
      <div class="cem-status">Checking backend...</div>
      <a href="${{config.backend}}/#codex-integration" target="_blank" rel="noreferrer">Connection</a>
      <a href="${{config.backend}}/#providers" target="_blank" rel="noreferrer">Providers</a>
      <a href="${{config.backend}}/#stats" target="_blank" rel="noreferrer">Usage</a>
      <a href="${{config.backend}}/#diagnostics" target="_blank" rel="noreferrer">Diagnostics</a>
    </div>
  `;
  root.querySelector('button').addEventListener('click', () => root.classList.toggle('open'));
  document.documentElement.appendChild(root);

  fetch(`${{config.backend}}/api/codex-injection/status`, {{ cache: 'no-store' }})
    .then((response) => response.json())
    .then((data) => {{
      const status = root.querySelector('.cem-status');
      if (status) status.textContent = data && data.success ? 'Backend connected' : 'Backend unavailable';
    }})
    .catch(() => {{
      const status = root.querySelector('.cem-status');
      if (status) status.textContent = 'Backend unavailable';
    }});
}})();
""".strip()


def discover_cdp_targets(port: int = DEFAULT_CDP_PORT, timeout: float = 1.0) -> List[Dict[str, Any]]:
    url = f"http://127.0.0.1:{int(port)}/json/list"
    with urllib.request.urlopen(url, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8", errors="replace"))
    return data if isinstance(data, list) else []


def inject_codex_enhancements(
    port: int = DEFAULT_CDP_PORT,
    backend_url: str = "",
    timeout_seconds: float = 8.0,
) -> Dict[str, Any]:
    script = build_injection_script(backend_url)
    deadline = time.time() + max(float(timeout_seconds), 0.5)
    last_error = ""
    injected = 0
    targets_seen = 0

    while time.time() < deadline:
        try:
            targets = discover_cdp_targets(port=port, timeout=0.8)
            page_targets = [
                target for target in targets
                if target.get("webSocketDebuggerUrl") and target.get("type") in ("page", "webview")
            ]
            targets_seen = max(targets_seen, len(page_targets))
            for target in page_targets:
                if _inject_target(str(target["webSocketDebuggerUrl"]), script):
                    injected += 1
            if injected:
                return {
                    "success": True,
                    "port": int(port),
                    "targets_seen": targets_seen,
                    "targets_injected": injected,
                    "error": "",
                }
        except Exception as exc:
            last_error = str(exc)
        time.sleep(0.35)

    return {
        "success": False,
        "port": int(port),
        "targets_seen": targets_seen,
        "targets_injected": injected,
        "error": last_error or "No injectable Codex renderer target found.",
    }


def _inject_target(ws_url: str, script: str) -> bool:
    client = _CdpWebSocket(ws_url)
    try:
        client.connect()
        client.call("Page.enable")
        client.call("Runtime.enable")
        client.call("Page.addScriptToEvaluateOnNewDocument", {"source": script})
        client.call("Runtime.evaluate", {"expression": script, "awaitPromise": False})
        return True
    finally:
        client.close()


class _CdpWebSocket:
    def __init__(self, url: str):
        self.url = url
        self.sock: Optional[socket.socket] = None
        self.next_id = 0

    def connect(self) -> None:
        host, port, path = _parse_ws_url(self.url)
        raw_sock = socket.create_connection((host, port), timeout=2.5)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        raw_sock.sendall(request.encode("ascii"))
        response = raw_sock.recv(4096).decode("iso-8859-1", errors="replace")
        if " 101 " not in response.split("\r\n", 1)[0]:
            raw_sock.close()
            raise RuntimeError("CDP websocket upgrade failed")
        accept = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
        ).decode("ascii")
        if accept not in response:
            raw_sock.close()
            raise RuntimeError("CDP websocket accept header mismatch")
        self.sock = raw_sock

    def call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self.next_id += 1
        message_id = self.next_id
        payload = {"id": message_id, "method": method}
        if params:
            payload["params"] = params
        self._send_text(json.dumps(payload, separators=(",", ":")))
        deadline = time.time() + 3.0
        while time.time() < deadline:
            message = self._recv_text()
            if not message:
                continue
            data = json.loads(message)
            if data.get("id") == message_id:
                if data.get("error"):
                    raise RuntimeError(str(data["error"]))
                return data
        raise TimeoutError(f"Timed out waiting for CDP response: {method}")

    def close(self) -> None:
        if self.sock:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def _send_text(self, text: str) -> None:
        if not self.sock:
            raise RuntimeError("CDP websocket is not connected")
        payload = text.encode("utf-8")
        mask = os.urandom(4)
        header = bytearray([0x81])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length <= 0xFFFF:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        self.sock.sendall(bytes(header) + mask + masked)

    def _recv_text(self) -> str:
        if not self.sock:
            return ""
        first = self._recv_exact(2)
        if not first:
            return ""
        opcode = first[0] & 0x0F
        length = first[1] & 0x7F
        masked = bool(first[1] & 0x80)
        if length == 126:
            length = struct.unpack("!H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv_exact(8))[0]
        mask = self._recv_exact(4) if masked else b""
        payload = self._recv_exact(length)
        if masked:
            payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        if opcode == 8:
            return ""
        if opcode != 1:
            return ""
        return payload.decode("utf-8", errors="replace")

    def _recv_exact(self, length: int) -> bytes:
        chunks = []
        remaining = length
        while remaining > 0:
            chunk = self.sock.recv(remaining) if self.sock else b""
            if not chunk:
                raise ConnectionError("CDP websocket closed")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)


def _parse_ws_url(url: str) -> tuple[str, int, str]:
    if not url.startswith("ws://"):
        raise ValueError("Only local ws:// CDP URLs are supported")
    rest = url[len("ws://"):]
    host_port, _, path = rest.partition("/")
    host, _, port_raw = host_port.partition(":")
    return host or "127.0.0.1", int(port_raw or DEFAULT_CDP_PORT), "/" + path
