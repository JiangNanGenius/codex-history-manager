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


def _renderer_enhancement_runtime() -> str:
    return r"""
  const cemRuntime = (() => {
    const runtimeKey = '__codexEnhanceManagerRuntime';
    const version = 2;
    const previous = window[runtimeKey];
    if (previous && typeof previous.destroy === 'function') previous.destroy();

    const hiddenUsageAttr = 'data-cem-hidden-usage-alert';
    const state = {
      settings: {
        pluginMarketplaceUnlock: false,
        pluginEntryUnlock: false,
        forcePluginInstall: false,
        hideOfficialUsageAlert: false,
      },
      observer: null,
      scanTimer: 0,
      statusTimer: 0,
      hiddenUsageNodes: new Set(),
      marketplaceFilterPatched: false,
      responseJsonPatched: false,
      fetchPatched: false,
      webSocketPatched: false,
    };

    function normalizeText(value) {
      return String(value || '').replace(/\s+/g, ' ').trim();
    }

    function elementText(node) {
      if (!node || node.nodeType !== Node.ELEMENT_NODE) return '';
      return normalizeText(node.innerText || node.textContent || node.getAttribute('aria-label') || '');
    }

    function visibleBox(node, minWidth = 120, minHeight = 12) {
      if (!(node instanceof HTMLElement)) return false;
      const rect = node.getBoundingClientRect();
      if (!rect || rect.width < minWidth || rect.height < minHeight) return false;
      const vw = window.innerWidth || 1200;
      const vh = window.innerHeight || 900;
      return rect.bottom > 0 && rect.top < vh && rect.right > 0 && rect.left < vw;
    }

    function inConversationContent(node) {
      return Boolean(node && node.closest([
        '[data-message-author-role]',
        '[data-testid*="message" i]',
        '[data-test-id*="message" i]',
        '[data-thread-find-target]',
        'article',
      ].join(',')));
    }

    const usageAlertText = /(Codex\s*(message|usage)?\s*(limit|quota)|message\s+limit|usage\s+limit|quota\s+will\s+reset|limit\s+will\s+reset|usage\s+remaining|remaining\s+\d+%\s+usage|消息限额|使用量|额度|下次重置|重置频率|剩余\s*\d+%\s*使用量)/i;
    const usageActionText = /(upgrade|plus|pricing|plan|reset|continue|quota|limit|升级|重置|继续使用|限额|额度|套餐)/i;

    function usageAlertRoot(node) {
      const status = node.closest?.('[role="alert"], [role="status"], [aria-live]');
      if (status && status !== document.body && status !== document.documentElement) return status;
      const parent = node.parentElement;
      if (parent && parent !== document.body && visibleBox(parent, 160, 16)) {
        const text = elementText(parent);
        if (text.length <= 520 && usageAlertText.test(text)) return parent;
      }
      return node;
    }

    function looksLikeOfficialUsageAlert(node) {
      if (!(node instanceof HTMLElement) || inConversationContent(node) || !visibleBox(node, 160, 16)) return false;
      const text = elementText(node);
      if (text.length < 12 || text.length > 560) return false;
      if (!usageAlertText.test(text)) return false;
      const actionText = normalizeText(Array.from(node.querySelectorAll('button, a, [role="button"]'))
        .slice(0, 8)
        .map((item) => item.innerText || item.textContent || item.getAttribute('aria-label') || '')
        .join(' '));
      return usageActionText.test(text + ' ' + actionText);
    }

    function installUsageAlertStyle() {
      if (document.getElementById('cem-hide-usage-alert-style')) return;
      const style = document.createElement('style');
      style.id = 'cem-hide-usage-alert-style';
      style.textContent = [
        '[' + hiddenUsageAttr + '="true"] { display: none !important; visibility: hidden !important; pointer-events: none !important; }',
        '.cem-force-install-unlocked { pointer-events: auto !important; cursor: pointer !important; opacity: 1 !important; }',
      ].join('\n');
      document.documentElement.appendChild(style);
    }

    function restoreUsageAlerts() {
      for (const node of state.hiddenUsageNodes) {
        try {
          node.removeAttribute(hiddenUsageAttr);
          node.removeAttribute(hiddenUsageAttr + '-kind');
        } catch {}
      }
      state.hiddenUsageNodes.clear();
    }

    function scanOfficialUsageAlerts() {
      if (!state.settings.hideOfficialUsageAlert) {
        restoreUsageAlerts();
        return;
      }
      installUsageAlertStyle();
      const root = document.body || document.documentElement;
      if (!root) return;
      const selectors = ['[role="alert"]', '[role="status"]', '[aria-live]', 'header', 'aside', 'section', 'div'].join(',');
      for (const node of root.querySelectorAll(selectors)) {
        if (node.getAttribute(hiddenUsageAttr) === 'true') continue;
        if (!looksLikeOfficialUsageAlert(node)) continue;
        const target = usageAlertRoot(node);
        if (!target || target === document.body || target === document.documentElement) continue;
        target.setAttribute(hiddenUsageAttr, 'true');
        target.setAttribute(hiddenUsageAttr + '-kind', 'official-usage-alert');
        state.hiddenUsageNodes.add(target);
      }
    }

    function appServerRequestMethod(method, params) {
      if (method === 'send-cli-request-for-host' && params && params.method) return String(params.method);
      return String(method || '');
    }

    function patchPluginMarketplaceParams(method, params) {
      const requestMethod = appServerRequestMethod(method, params);
      if (requestMethod !== 'list-plugins' || !params || typeof params !== 'object') return params;
      const next = { ...params };
      delete next.marketplaceKinds;
      if (next.params && typeof next.params === 'object') {
        next.params = { ...next.params };
        delete next.params.marketplaceKinds;
      }
      return next;
    }

    function patchPluginRequestObject(value, seen = new WeakSet()) {
      if (!value || typeof value !== 'object' || seen.has(value)) return value;
      seen.add(value);
      const method = String(value.method || '');
      if (method === 'list-plugins' || (method === 'send-cli-request-for-host' && value.params?.method === 'list-plugins')) {
        if (value.params && typeof value.params === 'object') {
          value.params = patchPluginMarketplaceParams(appServerRequestMethod(method, value.params), value.params);
        }
        delete value.marketplaceKinds;
      }
      for (const child of Object.values(value)) {
        if (child && typeof child === 'object') patchPluginRequestObject(child, seen);
      }
      return value;
    }

    function parsePatchStringPayload(data) {
      if (typeof data !== 'string' || !data.includes('list-plugins')) return data;
      try {
        const parsed = JSON.parse(data);
        patchPluginRequestObject(parsed);
        return JSON.stringify(parsed);
      } catch {
        return data.replace(/"marketplaceKinds"\s*:\s*\[[^\]]*\]\s*,?/g, '');
      }
    }

    function patchPluginMarketplaceResultGraph(value, seen = new WeakSet(), depth = 0) {
      if (!value || typeof value !== 'object' || seen.has(value) || depth > 8) return value;
      seen.add(value);
      if (Array.isArray(value.marketplaces)) {
        value.marketplaces.forEach((marketplace) => {
          if (!marketplace || typeof marketplace !== 'object') return;
          const name = String(marketplace.name || marketplace.marketplaceName || '');
          if (/^openai-(bundled|curated|primary-runtime)$/.test(name)) {
            const displayName = marketplace.displayName || marketplace.title || marketplace.label || name;
            marketplace.displayName = displayName;
            marketplace.title = displayName;
            marketplace.label = displayName;
            marketplace.__cemMarketplaceUnlocked = true;
          }
        });
      }
      for (const child of Object.values(value)) patchPluginMarketplaceResultGraph(child, seen, depth + 1);
      return value;
    }

    function installPluginMarketplaceUnlock() {
      if (!state.settings.pluginMarketplaceUnlock) return;
      installUsageAlertStyle();
      if (!state.marketplaceFilterPatched) {
        const originalFilter = Array.prototype.__cemOriginalFilter || Array.prototype.filter;
        if (!Array.prototype.__cemOriginalFilter) {
          Object.defineProperty(Array.prototype, '__cemOriginalFilter', { value: originalFilter, configurable: true });
        }
        Array.prototype.filter = function cemMarketplaceFilter(callback, thisArg) {
          if (state.settings.pluginMarketplaceUnlock && Array.isArray(this) && this.some((item) => {
            const name = String(item?.marketplaceName || item?.name || '');
            return /^openai-(bundled|curated|primary-runtime)$/.test(name);
          })) {
            let source = '';
            try { source = Function.prototype.toString.call(callback); } catch {}
            if (/marketplace(Name)?/.test(source) && this.some((item) => callback.call(thisArg, item) === false)) {
              return Array.from(this);
            }
          }
          return originalFilter.call(this, callback, thisArg);
        };
        state.marketplaceFilterPatched = true;
      }
      if (!state.responseJsonPatched && window.Response?.prototype?.json) {
        const originalJson = Response.prototype.__cemOriginalJson || Response.prototype.json;
        Response.prototype.__cemOriginalJson = originalJson;
        Response.prototype.json = async function cemMarketplaceJsonPatch() {
          const result = await originalJson.call(this);
          if (state.settings.pluginMarketplaceUnlock) patchPluginMarketplaceResultGraph(result);
          return result;
        };
        state.responseJsonPatched = true;
      }
      if (!state.fetchPatched && window.fetch) {
        const originalFetch = window.__cemOriginalFetch || window.fetch;
        window.__cemOriginalFetch = originalFetch;
        window.fetch = function cemMarketplaceFetch(input, init) {
          if (state.settings.pluginMarketplaceUnlock && init && typeof init === 'object' && init.body) {
            init = { ...init, body: parsePatchStringPayload(init.body) };
          }
          return originalFetch.call(this, input, init);
        };
        state.fetchPatched = true;
      }
      if (!state.webSocketPatched && window.WebSocket?.prototype?.send) {
        const originalSend = WebSocket.prototype.__cemOriginalSend || WebSocket.prototype.send;
        WebSocket.prototype.__cemOriginalSend = originalSend;
        WebSocket.prototype.send = function cemMarketplaceWebSocketSend(data) {
          return originalSend.call(this, state.settings.pluginMarketplaceUnlock ? parsePatchStringPayload(data) : data);
        };
        state.webSocketPatched = true;
      }
    }

    function patchReactDisabledProps(element) {
      Object.keys(element || {}).filter((key) => key.startsWith('__reactProps')).forEach((key) => {
        const props = element[key];
        if (!props || typeof props !== 'object') return;
        props.disabled = false;
        props['aria-disabled'] = false;
        props['data-disabled'] = undefined;
      });
    }

    function clearDisabledState(element) {
      if (!(element instanceof HTMLElement)) return;
      if ('disabled' in element) element.disabled = false;
      element.removeAttribute('disabled');
      element.removeAttribute('aria-disabled');
      element.removeAttribute('data-disabled');
      element.removeAttribute('inert');
      element.classList.remove('disabled', 'cursor-not-allowed', 'pointer-events-none', 'opacity-50');
      element.classList.add('cem-force-install-unlocked');
      element.style.pointerEvents = 'auto';
      element.style.cursor = 'pointer';
      element.style.opacity = '';
      patchReactDisabledProps(element);
    }

    function looksLikePluginInstallButton(element) {
      const text = elementText(element);
      const aria = normalizeText(element.getAttribute?.('aria-label') || '');
      const combined = text + ' ' + aria;
      return /(install|app unavailable|unavailable|安装|应用不可用|不可用)/i.test(combined)
        && !/(uninstall|remove|delete|卸载|移除|删除)/i.test(combined);
    }

    function pluginInstallCandidates() {
      const selectors = [
        'button[disabled]',
        'button[aria-disabled="true"]',
        '[role="button"][aria-disabled="true"]',
        '[data-disabled]',
        '.cursor-not-allowed',
        '.pointer-events-none',
      ].join(',');
      return Array.from(document.querySelectorAll(selectors))
        .map((node) => node.closest?.('button, [role="button"]') || node)
        .filter((node) => node instanceof HTMLElement && looksLikePluginInstallButton(node));
    }

    function unblockPluginInstallButtons() {
      if (!state.settings.forcePluginInstall) return;
      installUsageAlertStyle();
      for (const button of pluginInstallCandidates()) {
        clearDisabledState(button);
        button.querySelectorAll?.('button, [role="button"], [disabled], [aria-disabled], [data-disabled]').forEach(clearDisabledState);
      }
    }

    function unlockPluginEntry() {
      if (!state.settings.pluginEntryUnlock) return;
      const button = Array.from(document.querySelectorAll('button, [role="button"], a')).find((item) => {
        const text = elementText(item);
        return /^(plugins|插件)(\s|$)/i.test(text) || /plugins|插件/i.test(item.getAttribute?.('aria-label') || '');
      });
      if (!button) return;
      clearDisabledState(button);
      button.dataset.cemPluginEntryUnlocked = 'true';
    }

    function scanEnhancements() {
      state.scanTimer = 0;
      installPluginMarketplaceUnlock();
      scanOfficialUsageAlerts();
      unlockPluginEntry();
      unblockPluginInstallButtons();
    }

    function scheduleScan(delay = 80) {
      if (state.scanTimer) return;
      state.scanTimer = window.setTimeout(scanEnhancements, delay);
    }

    function ensureObserver() {
      if (state.observer) return;
      const root = document.body || document.documentElement;
      if (!root) {
        document.addEventListener('DOMContentLoaded', ensureObserver, { once: true });
        return;
      }
      state.observer = new MutationObserver((mutations) => {
        if (mutations.some((mutation) => mutation.addedNodes.length || mutation.type === 'characterData' || mutation.attributeName)) {
          scheduleScan();
        }
      });
      state.observer.observe(root, { childList: true, subtree: true, characterData: true, attributes: true });
    }

    function applyStatus(data) {
      const enabled = !data || data.enabled !== false;
      state.settings.pluginMarketplaceUnlock = enabled && Boolean(data && (data.plugin_marketplace_unlock || data.plugin_unlock_enabled));
      state.settings.pluginEntryUnlock = enabled && Boolean(data && (data.plugin_entry_unlock || data.plugin_unlock_enabled));
      state.settings.forcePluginInstall = enabled && Boolean(data && (data.force_plugin_install || data.plugin_unlock_enabled));
      state.settings.hideOfficialUsageAlert = enabled && Boolean(data && data.hide_official_usage_alert);
      scheduleScan(0);
    }

    function destroy() {
      if (state.scanTimer) window.clearTimeout(state.scanTimer);
      if (state.statusTimer) window.clearInterval(state.statusTimer);
      state.scanTimer = 0;
      state.statusTimer = 0;
      state.observer?.disconnect();
      state.observer = null;
      restoreUsageAlerts();
      if (Array.prototype.__cemOriginalFilter) Array.prototype.filter = Array.prototype.__cemOriginalFilter;
      if (Response?.prototype?.__cemOriginalJson) Response.prototype.json = Response.prototype.__cemOriginalJson;
      if (window.__cemOriginalFetch) window.fetch = window.__cemOriginalFetch;
      if (WebSocket?.prototype?.__cemOriginalSend) WebSocket.prototype.send = WebSocket.prototype.__cemOriginalSend;
      document.getElementById('cem-hide-usage-alert-style')?.remove();
      if (window[runtimeKey]?.version === version) delete window[runtimeKey];
    }

    ensureObserver();
    window[runtimeKey] = { version, state, applyStatus, scheduleScan, destroy };
    scheduleScan(0);
    return window[runtimeKey];
  })();
""".strip()


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
    return rf"""
(() => {{
  const config = {config_json};
  if (window.__codexEnhanceManagerInjected === config.marker) return;
  window.__codexEnhanceManagerInjected = config.marker;

{_renderer_enhancement_runtime()}

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
      width: 320px;
      border: 1px solid rgba(148, 163, 184, .25);
      border-radius: 8px;
      overflow: hidden;
      background: rgba(2, 6, 23, .96);
      box-shadow: 0 18px 42px rgba(2, 6, 23, .45);
    }}
    #${{rootId}}.open .cem-panel {{ display: block; }}
    #${{rootId}} .cem-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding: 10px 12px;
      border-bottom: 1px solid rgba(148, 163, 184, .16);
    }}
    #${{rootId}} .cem-title {{ font-weight: 700; color: #f8fafc; }}
    #${{rootId}} .cem-status {{ color: #a7f3d0; font-size: 11px; }}
    #${{rootId}} .cem-body {{ padding: 10px 12px 12px; }}
    #${{rootId}} .cem-section + .cem-section {{ margin-top: 12px; }}
    #${{rootId}} .cem-section-title {{
      margin-bottom: 6px;
      color: #93c5fd;
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0;
    }}
    #${{rootId}} .cem-provider-list {{
      display: grid;
      gap: 6px;
      max-height: 160px;
      overflow: auto;
      padding-right: 2px;
    }}
    #${{rootId}} .cem-provider {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      width: 100%;
      box-shadow: none;
      text-align: left;
      padding: 7px 8px;
      background: rgba(15, 23, 42, .72);
    }}
    #${{rootId}} .cem-provider.active {{
      border-color: rgba(56, 189, 248, .76);
      background: rgba(14, 116, 144, .32);
    }}
    #${{rootId}} .cem-provider small {{
      color: #94a3b8;
      font-size: 10px;
      margin-left: 6px;
    }}
    #${{rootId}} .cem-toggle {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding: 7px 0;
      color: #dbeafe;
      border-top: 1px solid rgba(148, 163, 184, .10);
    }}
    #${{rootId}} .cem-switch {{
      position: relative;
      width: 34px;
      height: 20px;
      flex: 0 0 auto;
      border-radius: 999px;
      background: #334155;
      border: 1px solid rgba(148, 163, 184, .35);
    }}
    #${{rootId}} .cem-switch::after {{
      content: '';
      position: absolute;
      top: 2px;
      left: 2px;
      width: 14px;
      height: 14px;
      border-radius: 999px;
      background: #e5e7eb;
      transition: transform .14s ease, background .14s ease;
    }}
    #${{rootId}} .cem-switch.on {{ background: #0891b2; }}
    #${{rootId}} .cem-switch.on::after {{ transform: translateX(14px); background: #ffffff; }}
    #${{rootId}} .cem-toggle.cem-disabled {{ opacity: .55; cursor: not-allowed; }}
    #${{rootId}} .cem-toggle.cem-disabled .cem-switch {{ background: #1f2937; border-color: rgba(148, 163, 184, .16); }}
    #${{rootId}} .cem-switch.locked::after {{ background: #94a3b8; }}
    #${{rootId}} .cem-actions {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 6px;
      margin-top: 8px;
    }}
    #${{rootId}} a, #${{rootId}} .cem-link {{
      display: block;
      padding: 8px 10px;
      color: #dbeafe;
      text-decoration: none;
      border: 1px solid rgba(148, 163, 184, .18);
      border-radius: 8px;
      white-space: nowrap;
      text-align: center;
      background: rgba(15, 23, 42, .72);
    }}
    #${{rootId}} a:hover, #${{rootId}} .cem-link:hover {{ background: rgba(59, 130, 246, .18); }}
    #${{rootId}} .cem-muted {{ color: #94a3b8; }}
    #${{rootId}} .cem-error {{ color: #fca5a5; }}
  `;
  document.documentElement.appendChild(style);

  const root = document.createElement('div');
  root.id = rootId;
  root.innerHTML = `
    <button type="button" aria-label="Codex Enhance Manager">Codex Enhance</button>
    <div class="cem-panel">
      <div class="cem-head">
        <div class="cem-title">Quick Settings</div>
        <div class="cem-status">Checking backend...</div>
      </div>
      <div class="cem-body">
        <div class="cem-section">
          <div class="cem-section-title">Route</div>
          <div class="cem-provider-list" data-cem-providers>
            <div class="cem-muted">Loading providers...</div>
          </div>
        </div>
        <div class="cem-section">
          <div class="cem-section-title">Hot Settings</div>
          <label class="cem-toggle">
            <span>Floating monitor</span>
            <input type="checkbox" data-cem-toggle="desktop_monitor_enabled" hidden>
            <span class="cem-switch" data-cem-switch="desktop_monitor_enabled"></span>
          </label>
          <label class="cem-toggle">
            <span>Enhancement injection</span>
            <input type="checkbox" data-cem-toggle="codex_injection_enabled" hidden>
            <span class="cem-switch" data-cem-switch="codex_injection_enabled"></span>
          </label>
          <label class="cem-toggle">
            <span>Plugin unlock</span>
            <input type="checkbox" data-cem-toggle="plugin_unlock_enabled" hidden>
            <span class="cem-switch" data-cem-switch="plugin_unlock_enabled"></span>
          </label>
          <div class="cem-muted" data-cem-plugin-unlock-note style="display:none;">Disabled for official login.</div>
        </div>
        <div class="cem-actions">
          <a href="${{config.backend}}/#providers" target="_blank" rel="noreferrer">Providers</a>
          <a href="${{config.backend}}/#amr" target="_blank" rel="noreferrer">Smart Routing</a>
          <a href="${{config.backend}}/#stats" target="_blank" rel="noreferrer">Usage</a>
          <a href="${{config.backend}}/#codex-integration" target="_blank" rel="noreferrer">Connection</a>
        </div>
      </div>
    </div>
  `;
  const cemEscapeHtml = (value) => String(value || '').replace(/[&<>"']/g, (ch) => ({{
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  }}[ch]));
  const cemSetStatus = (message, error = false) => {{
    const status = root.querySelector('.cem-status');
    if (!status) return;
    status.textContent = message;
    status.classList.toggle('cem-error', Boolean(error));
  }};
  const cemHumanizeError = (err) => {{
    const msg = String(err && err.message || err || 'unknown error');
    if (msg.includes('Failed to fetch')) return 'Backend connection failed';
    if (msg.includes('NetworkError')) return 'Backend connection failed';
    if (msg.includes('abort')) return 'Request cancelled';
    return msg;
  }};
  let cemQuickSettings = null;
  const cemRenderQuickSettings = (data) => {{
    cemQuickSettings = data || cemQuickSettings;
    const settings = (cemQuickSettings && cemQuickSettings.settings) || {{}};
    root.querySelectorAll('[data-cem-toggle]').forEach((input) => {{
      const key = input.getAttribute('data-cem-toggle');
      const value = settings[key] !== false;
      const locked = key === 'plugin_unlock_enabled' && Boolean(settings.plugin_unlock_forced_off);
      input.checked = value;
      input.disabled = locked;
      input.closest?.('.cem-toggle')?.classList.toggle('cem-disabled', locked);
      const sw = root.querySelector(`[data-cem-switch="${{key}}"]`);
      if (sw) {{
        sw.classList.toggle('on', value);
        sw.classList.toggle('locked', locked);
      }}
    }});
    const pluginNote = root.querySelector('[data-cem-plugin-unlock-note]');
    if (pluginNote) pluginNote.style.display = settings.plugin_unlock_forced_off ? 'block' : 'none';
    const list = root.querySelector('[data-cem-providers]');
    if (!list) return;
    const providers = Array.isArray(cemQuickSettings && cemQuickSettings.providers) ? cemQuickSettings.providers : [];
    const focus = String((cemQuickSettings && cemQuickSettings.focus_provider_id) || '');
    const autoActive = !focus;
    const autoButton = `<button type="button" class="cem-provider ${{autoActive ? 'active' : ''}}" data-cem-provider-id="">
      <span>${{autoActive ? '✓ ' : ''}}Auto provider</span><small>hot</small>
    </button>`;
    list.innerHTML = autoButton + providers.map((provider) => {{
      const id = String(provider.id || '');
      const label = provider.display_name || id;
      const alias = provider.short_alias ? ` / ${{provider.short_alias}}` : '';
      const active = id === focus || provider.focused;
      const badge = provider.switch_only || provider.codex_login ? 'official' : (provider.local_proxy_routing === false ? 'direct' : 'proxy');
      return `<button type="button" class="cem-provider ${{active ? 'active' : ''}}" data-cem-provider-id="${{cemEscapeHtml(id)}}">
        <span>${{active ? '✓ ' : ''}}${{cemEscapeHtml(label)}}<small>${{cemEscapeHtml(alias)}}</small></span><small>${{cemEscapeHtml(badge)}}</small>
      </button>`;
    }}).join('');
  }};
  const cemLoadQuickSettings = () => fetch(`${{config.backend}}/api/codex-injection/quick-settings`, {{ cache: 'no-store' }})
    .then((response) => response.json())
    .then((data) => {{
      if (!data || data.success === false) throw new Error((data && data.error) || 'Quick settings unavailable');
      cemRenderQuickSettings(data);
      return data;
    }});
  const cemPostQuickSettings = (patch) => fetch(`${{config.backend}}/api/codex-injection/quick-settings`, {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify(patch || {{}}),
  }}).then((response) => response.json()).then((data) => {{
    if (!data || data.success === false) throw new Error((data && data.error) || 'Quick settings failed');
    cemRenderQuickSettings(data);
    return data;
  }});
  root.querySelector('button').addEventListener('click', () => {{
    root.classList.toggle('open');
    if (root.classList.contains('open')) {{
      cemSetStatus('Loading...');
      cemLoadQuickSettings()
        .then(() => cemSetStatus('Ready'))
        .catch((error) => cemSetStatus(cemHumanizeError(error) || 'Backend unavailable', true));
    }}
  }});
  root.addEventListener('click', (event) => {{
    const providerButton = event.target.closest?.('[data-cem-provider-id]');
    if (providerButton) {{
      event.preventDefault();
      const providerId = providerButton.getAttribute('data-cem-provider-id') || '';
      cemSetStatus('Switching...');
      cemPostQuickSettings({{ provider_id: providerId }})
        .then(() => refreshCemBackendStatus())
        .then(() => cemSetStatus('Route updated'))
        .catch((error) => cemSetStatus(cemHumanizeError(error) || 'Switch failed', true));
    }}
  }});
  root.addEventListener('change', (event) => {{
    const input = event.target.closest?.('[data-cem-toggle]');
    if (!input) return;
    if (input.disabled) {{
      cemSetStatus('Plugin unlock is disabled for official login');
      cemRenderQuickSettings(cemQuickSettings);
      return;
    }}
    const key = input.getAttribute('data-cem-toggle');
    if (!key) return;
    cemSetStatus('Saving...');
    cemPostQuickSettings({{ [key]: input.checked }})
      .then(() => refreshCemBackendStatus())
      .then(() => cemSetStatus('Saved'))
      .catch((error) => {{
        input.checked = !input.checked;
        cemRenderQuickSettings(cemQuickSettings);
        cemSetStatus(cemHumanizeError(error) || 'Save failed', true);
      }});
  }});
  document.documentElement.appendChild(root);

  const refreshCemBackendStatus = () => {{
    fetch(`${{config.backend}}/api/codex-injection/status`, {{ cache: 'no-store' }})
      .then((response) => response.json())
      .then((data) => {{
        cemRuntime.applyStatus(data);
        const status = root.querySelector('.cem-status');
        if (status) status.textContent = data && data.success ? 'Backend connected' : 'Backend unavailable';
      }})
      .catch(() => {{
        cemRuntime.applyStatus({{ enabled: false }});
        const status = root.querySelector('.cem-status');
        if (status) status.textContent = 'Backend unavailable';
      }});
  }};
  refreshCemBackendStatus();
  if (window.__cemBackendStatusInterval) window.clearInterval(window.__cemBackendStatusInterval);
  window.__cemBackendStatusInterval = window.setInterval(refreshCemBackendStatus, 15000);
}})();
""".strip()


def build_injection_script(backend_url: str = "") -> str:
    backend = (backend_url or backend_url_from_env()).rstrip("/")
    payload = {
        "backend": backend,
        "marker": "codex-enhance-manager-v3",
    }
    config_json = json.dumps(payload, ensure_ascii=False)
    return rf"""
(() => {{
  const config = {config_json};
  const rootId = 'codex-enhance-manager-menu';
  const previousBackend = window.__codexEnhanceManagerBackend || '';
  if (window.__codexEnhanceManagerInjected === config.marker && document.getElementById(rootId) && previousBackend === config.backend) return;
  window.__codexEnhanceManagerInjected = config.marker;
  window.__codexEnhanceManagerBackend = config.backend;

{_renderer_enhancement_runtime()}

  document.getElementById(rootId)?.remove();

  const style = document.createElement('style');
  style.textContent = `
    #${{rootId}} {{
      position: fixed;
      right: 16px;
      bottom: 16px;
      z-index: 2147483647;
      font: 12px/1.45 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: #e5e7eb;
    }}
    #${{rootId}} button, #${{rootId}} a {{ font: inherit; }}
    #${{rootId}} > .cem-launch {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border: 1px solid rgba(148, 163, 184, .35);
      background: linear-gradient(135deg, rgba(15, 23, 42, .96), rgba(8, 47, 73, .94));
      color: #f8fafc;
      border-radius: 999px;
      width: 38px;
      height: 38px;
      padding: 0;
      cursor: pointer;
      box-shadow: 0 10px 32px rgba(2, 6, 23, .32);
    }}
    #${{rootId}} .cem-dot {{
      width: 7px;
      height: 7px;
      border-radius: 999px;
      background: #22c55e;
      box-shadow: 0 0 0 4px rgba(34, 197, 94, .14);
    }}
    #${{rootId}} .cem-panel {{
      display: none;
      position: absolute;
      right: 0;
      bottom: 46px;
      width: 374px;
      border: 1px solid rgba(148, 163, 184, .25);
      border-radius: 10px;
      overflow: hidden;
      background: linear-gradient(180deg, rgba(8, 13, 28, .98), rgba(2, 6, 23, .98));
      box-shadow: 0 22px 52px rgba(2, 6, 23, .52);
      backdrop-filter: blur(16px);
    }}
    #${{rootId}}.open .cem-panel {{ display: block; }}
    #${{rootId}} .cem-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding: 13px 14px 11px;
      border-bottom: 1px solid rgba(148, 163, 184, .16);
    }}
    #${{rootId}} .cem-title {{ font-size: 14px; font-weight: 750; color: #f8fafc; }}
    #${{rootId}} .cem-subtitle {{ margin-top: 2px; color: #94a3b8; font-size: 11px; }}
    #${{rootId}} .cem-status {{ color: #a7f3d0; font-size: 11px; }}
    #${{rootId}} .cem-body {{ padding: 12px 14px 14px; }}
    #${{rootId}} .cem-route-card {{
      border: 1px solid rgba(56, 189, 248, .22);
      border-radius: 8px;
      padding: 10px;
      background: rgba(15, 23, 42, .66);
    }}
    #${{rootId}} .cem-route-top {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
    }}
    #${{rootId}} .cem-route-label {{ color: #93c5fd; font-size: 11px; }}
    #${{rootId}} .cem-route-value {{
      margin-top: 2px;
      color: #f8fafc;
      font-weight: 700;
      overflow-wrap: anywhere;
    }}
    #${{rootId}} .cem-pill {{
      flex: 0 0 auto;
      padding: 3px 7px;
      border-radius: 999px;
      color: #bae6fd;
      background: rgba(14, 116, 144, .28);
      border: 1px solid rgba(56, 189, 248, .24);
      font-size: 10px;
    }}
    #${{rootId}} .cem-metrics {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
      margin-top: 10px;
    }}
    #${{rootId}} .cem-metric {{
      min-width: 0;
      border: 1px solid rgba(148, 163, 184, .16);
      border-radius: 8px;
      padding: 9px;
      background: rgba(15, 23, 42, .52);
    }}
    #${{rootId}} .cem-metric span {{
      display: block;
      color: #94a3b8;
      font-size: 10px;
      text-transform: uppercase;
      letter-spacing: 0;
    }}
    #${{rootId}} .cem-metric strong {{
      display: block;
      margin-top: 3px;
      color: #f8fafc;
      font-size: 17px;
      line-height: 1.15;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    #${{rootId}} .cem-metric small {{
      display: block;
      margin-top: 4px;
      color: #94a3b8;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    #${{rootId}} .cem-balance {{
      margin-top: 8px;
      border: 1px solid rgba(20, 184, 166, .20);
      border-radius: 8px;
      padding: 8px 9px;
      color: #ccfbf1;
      background: rgba(6, 78, 59, .17);
      overflow-wrap: anywhere;
    }}
    #${{rootId}} .cem-section {{ margin-top: 12px; }}
    #${{rootId}} .cem-section-title {{
      margin-bottom: 6px;
      color: #93c5fd;
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0;
    }}
    #${{rootId}} .cem-provider-list {{
      display: grid;
      gap: 6px;
      max-height: 150px;
      overflow: auto;
      padding-right: 2px;
    }}
    #${{rootId}} .cem-provider {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      width: 100%;
      box-shadow: none;
      text-align: left;
      padding: 7px 8px;
      background: rgba(15, 23, 42, .72);
      border: 1px solid rgba(148, 163, 184, .20);
      border-radius: 8px;
      color: #f8fafc;
      cursor: pointer;
    }}
    #${{rootId}} .cem-provider.active {{
      border-color: rgba(56, 189, 248, .76);
      background: rgba(14, 116, 144, .32);
    }}
    #${{rootId}} .cem-provider:hover {{ background: rgba(30, 41, 59, .88); }}
    #${{rootId}} .cem-provider small {{
      color: #94a3b8;
      font-size: 10px;
      margin-left: 6px;
    }}
    #${{rootId}} .cem-toggle-grid {{
      display: grid;
      gap: 6px;
    }}
    #${{rootId}} .cem-toggle {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding: 8px 9px;
      color: #dbeafe;
      border: 1px solid rgba(148, 163, 184, .16);
      border-radius: 8px;
      background: rgba(15, 23, 42, .52);
      cursor: pointer;
    }}
    #${{rootId}} .cem-toggle.cem-disabled {{
      opacity: .55;
      cursor: not-allowed;
    }}
    #${{rootId}} .cem-switch {{
      position: relative;
      width: 34px;
      height: 20px;
      flex: 0 0 auto;
      border-radius: 999px;
      background: #334155;
      border: 1px solid rgba(148, 163, 184, .35);
    }}
    #${{rootId}} .cem-switch::after {{
      content: '';
      position: absolute;
      top: 2px;
      left: 2px;
      width: 14px;
      height: 14px;
      border-radius: 999px;
      background: #e5e7eb;
      transition: transform .14s ease, background .14s ease;
    }}
    #${{rootId}} .cem-switch.on {{ background: #0891b2; }}
    #${{rootId}} .cem-switch.on::after {{ transform: translateX(14px); background: #ffffff; }}
    #${{rootId}} .cem-switch.locked {{ background: #1f2937; border-color: rgba(148, 163, 184, .16); }}
    #${{rootId}} .cem-switch.locked::after {{ background: #94a3b8; }}
    #${{rootId}} .cem-actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 12px;
    }}
    #${{rootId}} a, #${{rootId}} .cem-link {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 30px;
      padding: 6px 9px;
      color: #dbeafe;
      text-decoration: none;
      border: 1px solid rgba(148, 163, 184, .18);
      border-radius: 8px;
      white-space: nowrap;
      text-align: center;
      background: rgba(15, 23, 42, .72);
      cursor: pointer;
    }}
    #${{rootId}} a:hover, #${{rootId}} .cem-link:hover {{ background: rgba(59, 130, 246, .18); }}
    #${{rootId}} .cem-muted {{ color: #94a3b8; }}
    #${{rootId}} .cem-error {{ color: #fca5a5; }}
  `;
  document.documentElement.appendChild(style);

  const root = document.createElement('div');
  root.id = rootId;
  root.innerHTML = `
    <button type="button" class="cem-launch" aria-label="Codex Enhance Manager" title="Codex Enhance Manager"><span class="cem-dot"></span></button>
    <div class="cem-panel">
      <div class="cem-head">
        <div>
          <div class="cem-title">Usage Panel</div>
          <div class="cem-subtitle">Route, tokens, cost and balance · <span data-cem-version>v-</span></div>
        </div>
        <div class="cem-status">Checking backend...</div>
      </div>
      <div class="cem-body">
        <div class="cem-route-card">
          <div class="cem-route-top">
            <div>
              <div class="cem-route-label">Current route</div>
              <div class="cem-route-value" data-cem-route>Loading...</div>
            </div>
            <div class="cem-pill" data-cem-route-mode>--</div>
          </div>
          <div class="cem-metrics">
            <div class="cem-metric"><span>Tokens</span><strong data-cem-stat="tokens">--</strong><small data-cem-stat="token-speed">--</small></div>
            <div class="cem-metric"><span>Requests</span><strong data-cem-stat="requests">--</strong><small data-cem-stat="request-health">--</small></div>
            <div class="cem-metric"><span>Cost</span><strong data-cem-stat="cost">--</strong><small data-cem-stat="cost-speed">--</small></div>
            <div class="cem-metric"><span>Context</span><strong data-cem-stat="context">--</strong><small data-cem-stat="context-note">window</small></div>
          </div>
          <div class="cem-balance" data-cem-balance>Waiting for usage data...</div>
        </div>
        <div class="cem-section">
          <div class="cem-section-title">Fast Route Switch</div>
          <div class="cem-provider-list" data-cem-providers><div class="cem-muted">Loading providers...</div></div>
        </div>
        <div class="cem-section">
          <div class="cem-section-title">Quick Toggles</div>
          <div class="cem-toggle-grid">
            <label class="cem-toggle">
              <span>Enhancement injection</span>
              <input type="checkbox" data-cem-toggle="codex_injection_enabled" hidden>
              <span class="cem-switch" data-cem-switch="codex_injection_enabled"></span>
            </label>
            <label class="cem-toggle">
              <span>Plugin unlock</span>
              <input type="checkbox" data-cem-toggle="plugin_unlock_enabled" hidden>
              <span class="cem-switch" data-cem-switch="plugin_unlock_enabled"></span>
            </label>
          </div>
          <div class="cem-muted" data-cem-plugin-unlock-note style="display:none; margin-top:6px;">Disabled for official login.</div>
        </div>
        <div class="cem-actions">
          <button type="button" class="cem-link" data-cem-refresh>Refresh</button>
          <a href="${{config.backend}}/#providers" data-cem-backend-link="/#providers" target="_blank" rel="noreferrer">Providers</a>
          <a href="${{config.backend}}/#amr" data-cem-backend-link="/#amr" target="_blank" rel="noreferrer">Smart Routing</a>
          <a href="${{config.backend}}/#stats" data-cem-backend-link="/#stats" target="_blank" rel="noreferrer">Usage</a>
          <a href="${{config.backend}}/#codex-integration" data-cem-backend-link="/#codex-integration" target="_blank" rel="noreferrer">Connection</a>
        </div>
      </div>
    </div>
  `;

  let cemBackend = String(window.__codexEnhanceManagerBackend || config.backend || '').replace(/\/+$/, '');
  const cemSetBackend = (backend) => {{
    const next = String(backend || config.backend || '').replace(/\/+$/, '');
    if (!next) return;
    cemBackend = next;
    window.__codexEnhanceManagerBackend = next;
    root.querySelectorAll('[data-cem-backend-link]').forEach((link) => {{
      const path = link.getAttribute('data-cem-backend-link') || '/';
      link.href = `${{next}}${{path}}`;
    }});
  }};
  const cemBackendCandidates = () => {{
    const candidates = [];
    const add = (value) => {{
      const cleaned = String(value || '').replace(/\/+$/, '');
      if (cleaned && !candidates.includes(cleaned)) candidates.push(cleaned);
    }};
    add(cemBackend);
    add(config.backend);
    const ports = new Set();
    [cemBackend, config.backend].forEach((value) => {{
      try {{
        const url = new URL(value);
        if (/^(127\.0\.0\.1|localhost)$/i.test(url.hostname)) {{
          const port = Number(url.port || 80);
          if (Number.isFinite(port) && port > 0) ports.add(port);
        }}
      }} catch {{}}
    }});
    for (let port = 51234; port <= 51264; port += 1) ports.add(port);
    ports.forEach((port) => add(`http://127.0.0.1:${{port}}`));
    return candidates;
  }};
  const cemFetchJson = async (path, options = {{}}) => {{
    let lastError = null;
    for (const backend of cemBackendCandidates()) {{
      const controller = new AbortController();
      const timer = window.setTimeout(() => controller.abort(), 2200);
      try {{
        const response = await fetch(`${{backend}}${{path}}`, {{ ...options, signal: controller.signal }});
        const data = await response.json();
        if (!response.ok || !data || data.success === false) {{
          throw new Error((data && data.error) || `Backend responded ${{response.status}}`);
        }}
        cemSetBackend(data.backend_url || backend);
        return data;
      }} catch (error) {{
        lastError = error;
      }} finally {{
        window.clearTimeout(timer);
      }}
    }}
    throw lastError || new Error('Backend connection failed');
  }};
  cemSetBackend(cemBackend);

  const cemEscapeHtml = (value) => String(value || '').replace(/[&<>"']/g, (ch) => ({{
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  }}[ch]));
  const cemSetStatus = (message, error = false) => {{
    const status = root.querySelector('.cem-status');
    if (!status) return;
    status.textContent = message;
    status.classList.toggle('cem-error', Boolean(error));
  }};
  const cemHumanizeError = (err) => {{
    const msg = String(err && err.message || err || 'unknown error');
    if (msg.includes('Failed to fetch')) return 'Backend connection failed';
    if (msg.includes('NetworkError')) return 'Backend connection failed';
    if (msg.includes('abort')) return 'Request cancelled';
    return msg;
  }};
  const cemFormatNumber = (value) => {{
    const number = Number(value || 0);
    if (!Number.isFinite(number) || number <= 0) return '--';
    if (Math.abs(number) >= 1000000000) return `${{(number / 1000000000).toFixed(2).replace(/\\.00$/, '')}}B`;
    if (Math.abs(number) >= 1000000) return `${{(number / 1000000).toFixed(2).replace(/\\.00$/, '')}}M`;
    if (Math.abs(number) >= 1000) return `${{(number / 1000).toFixed(1).replace(/\\.0$/, '')}}K`;
    return Math.round(number).toLocaleString();
  }};
  const cemFormatMoney = (amount, currency = '') => {{
    const value = Number(amount || 0);
    if (!Number.isFinite(value) || value <= 0) return '--';
    const text = value >= 10 ? value.toFixed(2) : value.toFixed(4).replace(/0+$/, '').replace(/\\.$/, '');
    return `${{currency || ''}} ${{text}}`.trim();
  }};
  const cemFirstNumber = (object, keys) => {{
    if (!object || typeof object !== 'object') return null;
    for (const key of keys) {{
      const value = Number(object[key]);
      if (Number.isFinite(value)) return value;
    }}
    return null;
  }};
  const cemPrimaryCost = (summary) => {{
    const costs = summary && summary.effective_cost_by_currency && typeof summary.effective_cost_by_currency === 'object'
      ? summary.effective_cost_by_currency
      : {{}};
    const entries = Object.entries(costs)
      .map(([currency, value]) => [currency, Number(value || 0)])
      .filter(([, value]) => Number.isFinite(value) && value > 0)
      .sort((a, b) => b[1] - a[1]);
    return entries.length ? {{ currency: entries[0][0], amount: entries[0][1] }} : null;
  }};
  const cemSamples = {{ tokens: [], costs: [] }};
  const cemSampleRate = (samples, value, extra = {{}}) => {{
    const number = Number(value || 0);
    if (!Number.isFinite(number)) return null;
    const now = Date.now();
    samples.push({{ ts: now, value: number, ...extra }});
    while (samples.length > 96) samples.shift();
    const cutoff = now - 30 * 60 * 1000;
    while (samples.length > 2 && samples[0].ts < cutoff) samples.shift();
    if (samples.length < 2) return null;
    const first = samples[0];
    const last = samples[samples.length - 1];
    const minutes = Math.max((last.ts - first.ts) / 60000, 0.001);
    return Math.max((last.value - first.value) / minutes, 0);
  }};

  let cemQuickSettings = null;
  const cemRenderQuickSettings = (data) => {{
    cemQuickSettings = data || cemQuickSettings;
    const usage = (cemQuickSettings && cemQuickSettings.usage) || {{}};
    const summary = usage.request_log_summary || {{}};
    const settings = (cemQuickSettings && cemQuickSettings.settings) || {{}};
    const providers = Array.isArray(cemQuickSettings && cemQuickSettings.providers) ? cemQuickSettings.providers : [];
    const focus = String((cemQuickSettings && cemQuickSettings.focus_provider_id) || '');
    const versionEl = root.querySelector('[data-cem-version]');
    if (versionEl && cemQuickSettings && cemQuickSettings.app_version) versionEl.textContent = cemQuickSettings.app_version;
    const activeProvider = providers.find((provider) => String(provider.id || '') === focus || provider.focused) || null;
    const activeLabel = activeProvider ? (activeProvider.display_name || activeProvider.id || 'Selected provider') : 'Smart routing';
    const activeBadge = activeProvider
      ? (activeProvider.switch_only || activeProvider.codex_login ? 'official' : (activeProvider.local_proxy_routing === false ? 'direct' : 'proxy'))
      : 'auto';
    const routeEl = root.querySelector('[data-cem-route]');
    const modeEl = root.querySelector('[data-cem-route-mode]');
    if (routeEl) routeEl.textContent = activeLabel;
    if (modeEl) modeEl.textContent = activeBadge;

    const totalTokens = Number(usage.current_total_tokens || usage.total_tokens || (summary.tokens && summary.tokens.total_tokens) || 0);
    const tokenRate = cemSampleRate(cemSamples.tokens, totalTokens);
    root.querySelector('[data-cem-stat="tokens"]').textContent = cemFormatNumber(totalTokens);
    root.querySelector('[data-cem-stat="token-speed"]').textContent = tokenRate === null ? 'collecting samples' : `${{cemFormatNumber(tokenRate)}} tokens/min`;

    const requestCount = Number(summary.count || 0);
    const successCount = Number(summary.success_count || 0);
    const errorCount = Number(summary.error_count || 0);
    root.querySelector('[data-cem-stat="requests"]').textContent = requestCount ? requestCount.toLocaleString() : '--';
    root.querySelector('[data-cem-stat="request-health"]').textContent = requestCount ? `${{successCount}} ok / ${{errorCount}} err` : 'no proxy log yet';

    const primaryCost = cemPrimaryCost(summary);
    root.querySelector('[data-cem-stat="cost"]').textContent = primaryCost ? cemFormatMoney(primaryCost.amount, primaryCost.currency) : '--';
    const costRate = primaryCost ? cemSampleRate(cemSamples.costs, primaryCost.amount, {{ currency: primaryCost.currency }}) : null;
    root.querySelector('[data-cem-stat="cost-speed"]').textContent = primaryCost && costRate !== null
      ? `${{cemFormatMoney(costRate, primaryCost.currency)}}/min`
      : (primaryCost ? 'collecting samples' : 'estimated from proxy');

    const contextWindow = Number(usage.current_context_window || 0);
    const contextUsed = Number(usage.current_context_used_tokens || 0);
    root.querySelector('[data-cem-stat="context"]').textContent = contextWindow > 0 ? cemFormatNumber(contextWindow) : '--';
    root.querySelector('[data-cem-stat="context-note"]').textContent = contextUsed > 0 ? `${{cemFormatNumber(contextUsed)}} used` : 'window';

    const quota = usage.quota || {{}};
    const values = quota.values || quota.snapshot || {{}};
    const balance = cemFirstNumber(values, ['balance', 'remaining_balance', 'available_balance', 'credit', 'remaining']);
    const spent = cemFirstNumber(values, ['spent', 'used', 'used_amount', 'total_spent']);
    const currency = String(values.currency || values.unit || values.balance_currency || (primaryCost && primaryCost.currency) || '');
    const balanceEl = root.querySelector('[data-cem-balance]');
    if (usage.official_usage_hidden_by_provider) {{
      balanceEl.textContent = 'Official account usage hidden while a third-party route is active.';
    }} else if (usage.official_usage_default) {{
      balanceEl.textContent = 'Official login route is active. Official usage remains visible.';
    }} else if (quota.success && balance !== null) {{
      balanceEl.textContent = `Balance ${{cemFormatMoney(balance, currency)}}${{spent !== null ? ` - spent ${{cemFormatMoney(spent, currency)}}` : ''}}`;
    }} else if (primaryCost) {{
      balanceEl.textContent = `No live balance snapshot - estimated burn ${{cemFormatMoney(primaryCost.amount, primaryCost.currency)}} total`;
    }} else {{
      balanceEl.textContent = 'No balance or cost data yet.';
    }}

    root.querySelectorAll('[data-cem-toggle]').forEach((input) => {{
      const key = input.getAttribute('data-cem-toggle');
      const locked = key === 'plugin_unlock_enabled' && Boolean(settings.plugin_unlock_forced_off);
      const value = locked ? false : settings[key] !== false;
      input.checked = value;
      input.disabled = locked;
      input.closest?.('.cem-toggle')?.classList.toggle('cem-disabled', locked);
      const sw = root.querySelector(`[data-cem-switch="${{key}}"]`);
      if (sw) {{
        sw.classList.toggle('on', value);
        sw.classList.toggle('locked', locked);
      }}
    }});
    const pluginNote = root.querySelector('[data-cem-plugin-unlock-note]');
    if (pluginNote) pluginNote.style.display = settings.plugin_unlock_forced_off ? 'block' : 'none';

    const list = root.querySelector('[data-cem-providers]');
    if (!list) return;
    const autoActive = !focus;
    const autoButton = `<button type="button" class="cem-provider ${{autoActive ? 'active' : ''}}" data-cem-provider-id="">
      <span>${{autoActive ? '* ' : ''}}Smart routing</span><small>auto</small>
    </button>`;
    list.innerHTML = autoButton + providers.map((provider) => {{
      const id = String(provider.id || '');
      const label = provider.display_name || id;
      const alias = provider.short_alias ? ` / ${{provider.short_alias}}` : '';
      const active = id === focus || provider.focused;
      const badge = provider.switch_only || provider.codex_login ? 'official' : (provider.local_proxy_routing === false ? 'direct' : 'proxy');
      return `<button type="button" class="cem-provider ${{active ? 'active' : ''}}" data-cem-provider-id="${{cemEscapeHtml(id)}}">
        <span>${{active ? '* ' : ''}}${{cemEscapeHtml(label)}}<small>${{cemEscapeHtml(alias)}}</small></span><small>${{cemEscapeHtml(badge)}}</small>
      </button>`;
    }}).join('');
  }};

  const cemLoadQuickSettings = () => cemFetchJson('/api/codex-injection/quick-settings', {{ cache: 'no-store' }})
    .then((data) => {{
      cemRenderQuickSettings(data);
      return data;
    }});
  const cemPostQuickSettings = (patch) => cemFetchJson('/api/codex-injection/quick-settings', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify(patch || {{}}),
  }}).then((data) => {{
    cemRenderQuickSettings(data);
    return data;
  }});

  root.querySelector('.cem-launch').addEventListener('click', (event) => {{
    event.preventDefault();
    event.stopPropagation();
    root.classList.toggle('open');
    if (root.classList.contains('open')) {{
      cemSetStatus('Loading...');
      cemLoadQuickSettings()
        .then(() => cemSetStatus('Ready'))
        .catch((error) => cemSetStatus(cemHumanizeError(error) || 'Backend unavailable', true));
    }}
  }});
  root.addEventListener('click', (event) => {{
    event.stopPropagation();
    const refreshButton = event.target.closest?.('[data-cem-refresh]');
    if (refreshButton) {{
      event.preventDefault();
      cemSetStatus('Refreshing...');
      cemLoadQuickSettings()
        .then(() => cemSetStatus('Ready'))
        .catch((error) => cemSetStatus(cemHumanizeError(error) || 'Refresh failed', true));
      return;
    }}
    const providerButton = event.target.closest?.('[data-cem-provider-id]');
    if (!providerButton) return;
    event.preventDefault();
    const providerId = providerButton.getAttribute('data-cem-provider-id') || '';
    cemSetStatus('Switching...');
    cemPostQuickSettings({{ provider_id: providerId }})
      .then(() => refreshCemBackendStatus())
      .then(() => cemSetStatus('Route updated'))
      .catch((error) => cemSetStatus(error.message || 'Switch failed', true));
  }});
  root.addEventListener('change', (event) => {{
    event.stopPropagation();
    const input = event.target.closest?.('[data-cem-toggle]');
    if (!input) return;
    if (input.disabled) {{
      cemSetStatus('Plugin unlock is disabled for official login');
      cemRenderQuickSettings(cemQuickSettings);
      return;
    }}
    const key = input.getAttribute('data-cem-toggle');
    if (!key) return;
    cemSetStatus('Saving...');
    cemPostQuickSettings({{ [key]: input.checked }})
      .then(() => refreshCemBackendStatus())
      .then(() => cemSetStatus('Saved'))
      .catch((error) => {{
        input.checked = !input.checked;
        cemRenderQuickSettings(cemQuickSettings);
        cemSetStatus(cemHumanizeError(error) || 'Save failed', true);
      }});
  }});
  document.documentElement.appendChild(root);

  const refreshCemBackendStatus = () => {{
    cemFetchJson('/api/codex-injection/status', {{ cache: 'no-store' }})
      .then((data) => {{
        cemRuntime.applyStatus(data);
        if (!root.classList.contains('open')) {{
          const status = root.querySelector('.cem-status');
          if (status) status.textContent = data && data.success ? 'Backend connected' : 'Backend unavailable';
        }}
      }})
      .catch(() => {{
        cemRuntime.applyStatus({{ enabled: false }});
        if (!root.classList.contains('open')) cemSetStatus('Backend unavailable', true);
      }});
  }};
  refreshCemBackendStatus();
  if (window.__cemBackendStatusInterval) window.clearInterval(window.__cemBackendStatusInterval);
  window.__cemBackendStatusInterval = window.setInterval(refreshCemBackendStatus, 15000);
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
