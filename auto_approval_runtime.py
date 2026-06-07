"""
Runtime reviewer for the Auto Approval / Proxy Approval Broker.

The pure broker in ``approval_broker.py`` builds redacted approval prompts and
parses strict JSON decisions. This module is the runtime piece that sends that
prompt to a configured text-capable provider. It intentionally supports only
verified protocol families: OpenAI-compatible Chat, OpenAI-compatible
Responses, and Anthropic Messages.
"""
from __future__ import annotations

import json
import math
import socket
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

from approval_broker import build_auto_approval_prompt
from anthropic_adapter import anthropic_messages_url
from responses_adapter import chat_completions_url, responses_url


ProviderSupplier = Callable[[], Any]

SUPPORTED_TEXT_FORMATS = {
    "openai_chat",
    "openai_compatible",
    "openai_responses",
    "anthropic",
}


class AutoApprovalRuntimeError(RuntimeError):
    """Raised when a runtime approval review cannot be completed safely."""


class AutoApprovalModelReviewer:
    """
    Callable model reviewer used by proxy approval hooks.

    The reviewer chooses a text-capable provider from ``approval_profile``:
    ``reviewer_model`` wins when present, otherwise the action provider itself
    is used only if it can handle text review requests.
    """

    def __init__(self, provider_supplier: ProviderSupplier):
        self.provider_supplier = provider_supplier

    def review(self, action: Dict[str, Any], profile: Dict[str, Any], provider: Dict[str, Any]) -> Any:
        providers = self._load_providers()
        reviewer_provider, reviewer_model = self._resolve_reviewer_provider(profile, provider, providers)
        prompt = build_auto_approval_prompt(
            action,
            profile,
            context={
                "reviewer_provider_id": reviewer_provider.get("id", ""),
                "reviewer_api_format": reviewer_provider.get("api_format", ""),
            },
        )
        request_body = self._build_request_body(prompt, profile, reviewer_provider, reviewer_model)
        response_json = self._post_json(
            self._review_url(reviewer_provider),
            self._build_headers(reviewer_provider),
            request_body,
            timeout_seconds=self._timeout_seconds(profile),
            max_retries=self._max_retries(profile),
        )
        return self._extract_decision_payload(response_json)

    def _load_providers(self) -> List[Dict[str, Any]]:
        supplied = self.provider_supplier()
        if isinstance(supplied, dict):
            supplied = supplied.get("providers", [])
        if not isinstance(supplied, list):
            return []
        return [item for item in supplied if isinstance(item, dict)]

    def _resolve_reviewer_provider(
        self,
        profile: Dict[str, Any],
        action_provider: Dict[str, Any],
        providers: List[Dict[str, Any]],
    ) -> Tuple[Dict[str, Any], str]:
        reviewer_model = str(profile.get("reviewer_model") or "").strip()
        if reviewer_model:
            resolved = self._resolve_model_reference(reviewer_model, providers)
            if resolved:
                return resolved
            raise AutoApprovalRuntimeError("configured Auto Approval reviewer model was not found")

        if self._is_supported_text_provider(action_provider):
            model_id = self._first_text_model_id(action_provider)
            if model_id:
                return action_provider, model_id
        raise AutoApprovalRuntimeError("no text-capable Auto Approval reviewer provider is configured")

    def _resolve_model_reference(
        self,
        model_ref: str,
        providers: List[Dict[str, Any]],
    ) -> Optional[Tuple[Dict[str, Any], str]]:
        if "/" in model_ref:
            prefix, model_id = model_ref.split("/", 1)
            prefix = prefix.lower().strip()
            model_id = model_id.strip()
            for provider in providers:
                if not self._is_supported_text_provider(provider):
                    continue
                if str(provider.get("short_alias") or "").lower() == prefix or str(provider.get("id") or "").lower() == prefix:
                    return provider, model_id
            return None

        model_ref_lower = model_ref.lower().strip()
        for provider in providers:
            if not self._is_supported_text_provider(provider):
                continue
            for model in provider.get("models") or []:
                if not isinstance(model, dict) or not model.get("enabled", True):
                    continue
                if str(model.get("id") or "").lower().strip() == model_ref_lower:
                    return provider, str(model.get("id") or "").strip()
        return None

    def _is_supported_text_provider(self, provider: Dict[str, Any]) -> bool:
        if not provider.get("enabled", True):
            return False
        if not str(provider.get("base_url") or "").strip():
            return False
        if self._api_format(provider) not in SUPPORTED_TEXT_FORMATS:
            return False
        capabilities = provider.get("capabilities") if isinstance(provider.get("capabilities"), dict) else {}
        return bool(capabilities.get("text", True))

    def _first_text_model_id(self, provider: Dict[str, Any]) -> str:
        for model in provider.get("models") or []:
            if not isinstance(model, dict) or not model.get("enabled", True):
                continue
            capabilities = model.get("capabilities") if isinstance(model.get("capabilities"), dict) else {}
            if capabilities.get("text", True):
                model_id = str(model.get("id") or "").strip()
                if model_id:
                    return model_id
        return ""

    def _build_request_body(
        self,
        prompt: Dict[str, Any],
        profile: Dict[str, Any],
        provider: Dict[str, Any],
        model_id: str,
    ) -> Dict[str, Any]:
        messages = prompt.get("messages") if isinstance(prompt.get("messages"), list) else []
        system_text = self._message_text(messages, "system")
        user_text = self._message_text(messages, "user")
        api_format = self._api_format(provider)

        if api_format in {"openai_chat", "openai_compatible"}:
            body: Dict[str, Any] = {
                "model": model_id,
                "messages": messages,
                "temperature": 0,
                "max_tokens": 512,
            }
            if profile.get("require_structured_json", True):
                body["response_format"] = {"type": "json_object"}
            return body

        if api_format == "openai_responses":
            return {
                "model": model_id,
                "input": [
                    self._responses_message("system", system_text),
                    self._responses_message("user", user_text),
                ],
                "store": False,
            }

        if api_format == "anthropic":
            return {
                "model": model_id,
                "max_tokens": 512,
                "system": system_text,
                "messages": [{"role": "user", "content": user_text}],
            }

        raise AutoApprovalRuntimeError("Auto Approval reviewer provider uses an unsupported API format")

    def _review_url(self, provider: Dict[str, Any]) -> str:
        base_url = str(provider.get("base_url") or "").strip()
        api_format = self._api_format(provider)
        if api_format in {"openai_chat", "openai_compatible"}:
            return chat_completions_url(base_url)
        if api_format == "openai_responses":
            return responses_url(base_url)
        if api_format == "anthropic":
            return anthropic_messages_url(base_url)
        raise AutoApprovalRuntimeError("Auto Approval reviewer provider uses an unsupported API format")

    def _build_headers(self, provider: Dict[str, Any]) -> Dict[str, str]:
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        custom = provider.get("headers") if isinstance(provider.get("headers"), dict) else {}
        user_agent = str(provider.get("user_agent") or custom.get("User-Agent") or "Codex-Enhance-Manager-Proxy/1.0")
        headers["User-Agent"] = user_agent
        for key, value in custom.items():
            key_str = str(key)
            if key_str.lower() in {"authorization", "x-api-key", "content-type"}:
                continue
            if isinstance(value, str):
                headers[key_str] = value
        api_key = str(provider.get("api_key") or "")
        if api_key:
            if self._api_format(provider) == "anthropic":
                headers["x-api-key"] = api_key
            else:
                headers["Authorization"] = f"Bearer {api_key}"
        if self._api_format(provider) == "anthropic" and not self._has_header(headers, "anthropic-version"):
            headers["anthropic-version"] = str(provider.get("anthropic_version") or "2023-06-01")
        return headers

    def _post_json(
        self,
        url: str,
        headers: Dict[str, str],
        body: Dict[str, Any],
        timeout_seconds: int,
        max_retries: int,
    ) -> Dict[str, Any]:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        last_error: Optional[BaseException] = None
        for attempt in range(max_retries + 1):
            request = urllib.request.Request(url, data=payload, method="POST")
            for key, value in headers.items():
                request.add_header(key, value)
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                response = opener.open(request, timeout=timeout_seconds)
                response_body = response.read()
                return json.loads(response_body.decode("utf-8", errors="replace"))
            except urllib.error.HTTPError as exc:
                raise AutoApprovalRuntimeError(f"Auto Approval reviewer upstream returned HTTP {exc.code}") from exc
            except (urllib.error.URLError, socket.timeout, OSError) as exc:
                last_error = exc
                if attempt >= max_retries:
                    break
        raise AutoApprovalRuntimeError(f"Auto Approval reviewer connection failed: {last_error}") from last_error

    def _extract_decision_payload(self, response_json: Dict[str, Any]) -> Any:
        if not isinstance(response_json, dict):
            raise AutoApprovalRuntimeError("Auto Approval reviewer returned non-object JSON")
        if "decision" in response_json and "risk_level" in response_json:
            return response_json

        choices = response_json.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message") if isinstance(choices[0], dict) else {}
            content = message.get("content") if isinstance(message, dict) else None
            text = self._content_text(content)
            if text:
                return text

        output_text = response_json.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text

        output = response_json.get("output")
        if isinstance(output, list):
            text = self._responses_output_text(output)
            if text:
                return text

        content = response_json.get("content")
        text = self._content_text(content)
        if text:
            return text

        raise AutoApprovalRuntimeError("Auto Approval reviewer response did not contain text")

    def _responses_output_text(self, output: List[Any]) -> str:
        parts: List[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            parts.append(self._content_text(item.get("content")))
        return "\n".join(part for part in parts if part).strip()

    def _content_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: List[str] = []
            for part in content:
                if isinstance(part, str):
                    parts.append(part)
                elif isinstance(part, dict):
                    part_type = str(part.get("type") or "")
                    if part_type in {"text", "input_text", "output_text"} and part.get("text") is not None:
                        parts.append(str(part.get("text")))
            return "\n".join(parts).strip()
        return ""

    def _responses_message(self, role: str, text: str) -> Dict[str, Any]:
        return {
            "type": "message",
            "role": role,
            "content": [{"type": "input_text", "text": text}],
        }

    def _message_text(self, messages: List[Any], role: str) -> str:
        for message in messages:
            if isinstance(message, dict) and message.get("role") == role:
                return self._content_text(message.get("content"))
        return ""

    def _timeout_seconds(self, profile: Dict[str, Any]) -> int:
        try:
            timeout_ms = int(profile.get("timeout_ms") or 90000)
        except (TypeError, ValueError):
            timeout_ms = 90000
        return max(1, math.ceil(timeout_ms / 1000))

    def _max_retries(self, profile: Dict[str, Any]) -> int:
        try:
            retries = int(profile.get("max_retries") or 0)
        except (TypeError, ValueError):
            retries = 0
        return min(max(retries, 0), 5)

    def _api_format(self, provider: Dict[str, Any]) -> str:
        api_format = str(provider.get("api_format") or "openai_responses")
        return api_format if api_format in SUPPORTED_TEXT_FORMATS else api_format

    def _has_header(self, headers: Dict[str, str], name: str) -> bool:
        target = name.lower()
        return any(str(key).lower() == target for key in headers)
