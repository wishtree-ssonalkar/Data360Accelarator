"""LLM providers for the AI assistant, chosen in Admin > AI assistant (config/ai.json).

Supported providers
  claude             Anthropic Claude (official SDK)
  gemini             Google Gemini (REST)
  openai             OpenAI (REST, Chat Completions)
  azure_openai       Azure OpenAI (REST; model = deployment name, base_url = resource endpoint)
  openai_compatible  Any OpenAI-compatible API: Groq, Mistral, Together, OpenRouter, DeepSeek, a local Ollama...

The API key is a credential (encrypted store, or an environment variable of the same name); config/ai.json
holds only non-secret settings. Without config/ai.json the provider is detected from the keys that are set.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

import anthropic

from cdp.config import CONFIG_DIR

PROVIDERS: dict[str, dict] = {
    "claude": {"label": "Anthropic Claude", "key": "ANTHROPIC_API_KEY", "default_model": "claude-opus-5-5",
               "needs_base_url": False, "help": "Key from console.anthropic.com."},
    "gemini": {"label": "Google Gemini", "key": "GEMINI_API_KEY",
               "default_model": os.environ.get("GEMINI_MODEL", "gemini-flash-latest"),
               "needs_base_url": False, "help": "Key from Google AI Studio (aistudio.google.com)."},
    "openai": {"label": "OpenAI", "key": "OPENAI_API_KEY", "default_model": "", "needs_base_url": False,
               "help": "Key from platform.openai.com. Enter a model your account can use."},
    "azure_openai": {"label": "Azure OpenAI", "key": "AZURE_OPENAI_API_KEY", "default_model": "",
                     "needs_base_url": True, "help": "Endpoint like https://<resource>.openai.azure.com; "
                                                    "model = your deployment name."},
    "openai_compatible": {"label": "OpenAI-compatible (Groq, Mistral, OpenRouter, Ollama…)",
                          "key": "OPENAI_COMPATIBLE_API_KEY", "default_model": "", "needs_base_url": True,
                          "help": "Base URL ending in /v1, e.g. https://api.groq.com/openai/v1 or "
                                  "http://localhost:11434/v1 (Ollama, key optional)."},
}
AZURE_API_VERSION = "2024-10-21"
SETTINGS_PATH = CONFIG_DIR / "ai.json"


# --------------------------------------------------------------------------- settings
def load_settings() -> dict:
    """Saved settings, or a provider auto-detected from the keys that are set."""
    if SETTINGS_PATH.exists():
        saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        return {"share_result_rows": True, **saved}
    wanted = os.environ.get("CDP_AI_PROVIDER", "").lower()
    order = [wanted] if wanted in PROVIDERS else []
    order += [p for p in ("claude", "gemini", "openai", "azure_openai", "openai_compatible") if p not in order]
    for name in order:
        if _key(name) or (name == "claude" and (os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_PROFILE"))):
            return {"provider": name, "model": "", "base_url": "", "share_result_rows": True}
    return {"provider": None, "model": "", "base_url": "", "share_result_rows": True}


def _key(provider: str) -> str | None:
    from cdp.secrets import get_secret
    try:
        return get_secret(PROVIDERS[provider]["key"])
    except RuntimeError:
        return None


def model_for(settings: dict) -> str:
    return settings.get("model") or PROVIDERS.get(settings.get("provider") or "", {}).get("default_model", "")


def is_configured(settings: dict | None = None) -> bool:
    s = settings or load_settings()
    p = s.get("provider")
    if p not in PROVIDERS or not model_for(s):
        return False
    if PROVIDERS[p]["needs_base_url"] and not s.get("base_url"):
        return False
    if p == "openai_compatible":
        return True  # local servers (e.g. Ollama) need no key
    if p == "claude":
        return bool(_key(p) or os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_PROFILE"))
    return bool(_key(p))


def label(settings: dict | None = None) -> str:
    s = settings or load_settings()
    if s.get("provider") not in PROVIDERS:
        return "not configured"
    return f"{PROVIDERS[s['provider']]['label'].split(' (')[0]} · {model_for(s)}"


# --------------------------------------------------------------------------- providers
def _post_json(url: str, body: dict, headers: dict, who: str, timeout: int = 120) -> dict:
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise ProviderHTTPError(f"{who} API error ({exc.code}): {detail}", exc.code) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach {who}: {exc.reason}") from exc


class ProviderHTTPError(RuntimeError):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


def _claude(s: dict, system: str, messages: list[dict], max_tokens: int, effort: str, schema: dict | None) -> str:
    output_config = {"effort": effort}
    if schema:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    key = _key("claude")
    client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
    try:
        response = client.beta.messages.create(
            model=model_for(s), max_tokens=max_tokens, system=system, messages=messages,
            output_config=output_config, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            cache_control={"type": "ephemeral"},
        )
    except (anthropic.APIConnectionError, anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
        raise RuntimeError(f"Could not reach Claude: {exc}") from exc
    except anthropic.APIStatusError as exc:
        raise RuntimeError(f"Claude API error ({exc.status_code}): {exc.message}") from exc
    if response.stop_reason == "refusal":
        raise RuntimeError("The model declined to answer this question.")
    return "".join(b.text for b in response.content if b.type == "text")


def _gemini(s: dict, system: str, messages: list[dict], max_tokens: int, effort: str, schema: dict | None) -> str:
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
                     for m in messages],
        "generationConfig": {"maxOutputTokens": max_tokens},
    }
    if schema:
        body["generationConfig"].update({"responseMimeType": "application/json", "responseJsonSchema": schema})
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_for(s)}:generateContent"
    data = _post_json(url, body, {"x-goog-api-key": _key("gemini") or ""}, "Gemini")
    candidates = data.get("candidates") or []
    if not candidates or not candidates[0].get("content"):
        reason = (candidates[0].get("finishReason") if candidates else None) or data.get("promptFeedback")
        raise RuntimeError(f"Gemini returned no answer ({reason}).")
    return "".join(p.get("text", "") for p in candidates[0]["content"].get("parts", []))


def _openai_family(s: dict, system: str, messages: list[dict], max_tokens: int, effort: str,
                   schema: dict | None) -> str:
    provider = s["provider"]
    if schema:  # JSON mode is the most widely supported; the schema is spelled out in the prompt
        system += ("\n\nRespond with a single JSON object (no markdown, no code fences) that matches this JSON "
                   f"schema:\n{json.dumps(schema)}")
    body = {"messages": [{"role": "system", "content": system}, *messages], "max_tokens": max_tokens}
    if provider == "azure_openai":
        endpoint = s["base_url"].rstrip("/")
        url = (f"{endpoint}/openai/deployments/{model_for(s)}/chat/completions"
               f"?api-version={s.get('api_version') or AZURE_API_VERSION}")
        headers, who = {"api-key": _key(provider) or ""}, "Azure OpenAI"
    else:
        base = (s.get("base_url") or "https://api.openai.com/v1").rstrip("/")
        url, who = f"{base}/chat/completions", "OpenAI" if provider == "openai" else "The AI service"
        body["model"] = model_for(s)
        key = _key(provider)
        headers = {"Authorization": f"Bearer {key}"} if key else {}
    if schema:
        body["response_format"] = {"type": "json_object"}
    try:
        data = _post_json(url, body, headers, who)
    except ProviderHTTPError as exc:
        if exc.status != 400 or not schema:
            raise
        body.pop("response_format", None)  # some compatible servers don't support JSON mode
        data = _post_json(url, body, headers, who)
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"{who} returned no answer.")
    return choices[0].get("message", {}).get("content") or ""


BACKENDS = {"claude": _claude, "gemini": _gemini, "openai": _openai_family, "azure_openai": _openai_family,
            "openai_compatible": _openai_family}


def complete(system: str, messages: list[dict], max_tokens: int = 4000, effort: str = "medium",
             schema: dict | None = None, settings: dict | None = None) -> str:
    """One model call with the configured provider. Raises RuntimeError with a readable message."""
    s = settings or load_settings()
    if not is_configured(s):
        raise RuntimeError("The AI assistant is not configured (Admin > AI assistant).")
    return BACKENDS[s["provider"]](s, system, messages, max_tokens, effort, schema)


def parse_json(text: str) -> dict:
    """JSON from a model reply, tolerating code fences or text around the object."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise
        return json.loads(match.group(0))


def test_connection(settings: dict) -> str:
    """Send a tiny JSON request with the given settings; returns the model's reply or raises RuntimeError."""
    reply = complete("You are a connectivity check.", [{"role": "user", "content": 'Reply with {"ok": true}'}],
                     max_tokens=200, effort="low",
                     schema={"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"],
                             "additionalProperties": False}, settings=settings)
    if not parse_json(reply).get("ok"):
        raise RuntimeError(f"Unexpected reply: {reply[:200]}")
    return reply
