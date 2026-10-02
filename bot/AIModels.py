"""Model configuration, provider adapters and request-scoped failover.

The agent and integrations use the small data classes below. Only this module
knows provider SDKs/wire formats. Local servers use OpenAI Chat Completions.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from google import genai
from google.genai import errors, types as google_types

from .models import ConfigError

log = logging.getLogger("ai_models")


@dataclass
class FunctionDeclaration:
    name: str
    description: str = ""
    parameters: dict | None = None


@dataclass
class Tool:
    function_declarations: list[FunctionDeclaration]


@dataclass
class FunctionCall:
    name: str
    args: dict
    id: str | None = field(default_factory=lambda: "call_" + uuid4().hex)


@dataclass
class FunctionResponse:
    name: str
    response: dict
    id: str | None = None


@dataclass
class Part:
    text: str | None = None
    function_call: FunctionCall | None = None
    function_response: FunctionResponse | None = None

    @classmethod
    def from_function_response(cls, *, name, response, id=None):
        return cls(function_response=FunctionResponse(name, response, id))


@dataclass
class Content:
    role: str
    parts: list[Part]
    # Preserve signatures/reasoning in history when continuing with the same provider.
    native: object = None
    provider: str = ""


DEFAULT_MODELS = {
    "gemini": "gemini-2.5-flash-lite",
    "openai": "gpt-4.1-mini",
    "anthropic": "claude-sonnet-4-6",
    "deepseek": "deepseek-chat",
}
BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "local": "http://localhost:11434/v1",
}


def provider_for(model, explicit="", local=False):
    if local:
        return "local"
    provider = explicit.lower().strip()
    provider = {"claude": "anthropic", "gpt": "openai", "google": "gemini"}.get(provider, provider)
    if provider and provider != "auto":
        if provider not in {*DEFAULT_MODELS, "local", "openai-compatible"}:
            raise ConfigError(
                "AI_PROVIDER must be auto, gemini, openai, anthropic, deepseek or openai-compatible."
            )
        return provider
    name = model.lower()
    for prefix, value in (
        ("gemini", "gemini"),
        ("claude", "anthropic"),
        ("deepseek", "deepseek"),
        ("gpt", "openai"),
        ("o1", "openai"),
        ("o3", "openai"),
        ("o4", "openai"),
        ("chatgpt", "openai"),
    ):
        if name.startswith(prefix):
            return value
    if not model:
        return "gemini"
    raise ConfigError("Cannot infer AI provider from AI_MODEL; set AI_PROVIDER explicitly.")


@dataclass(frozen=True)
class Endpoint:
    provider: str
    model: str
    api_key: str = field(default="", repr=False)
    base_url: str = ""


@dataclass(frozen=True)
class ModelSettings:
    primary: Endpoint
    backup: Endpoint | None = None
    # Reasoning models spend part of this on hidden reasoning before the visible reply.
    max_tokens: int = 8192
    reasoning_effort: str = ""

    @classmethod
    def from_env(cls):
        local_value = os.getenv("LOCAL_MODEL", "false").strip().lower()
        if local_value not in ("true", "false", "1", "0", "yes", "no"):
            raise ConfigError("LOCAL_MODEL must be true or false.")
        local = local_value in ("true", "1", "yes")
        model = os.getenv("AI_MODEL", "").strip()
        explicit = os.getenv("AI_PROVIDER", "auto").strip().lower()
        if not model and explicit in ("", "auto", "gemini", "google") and not local:
            model = os.getenv("GEMINI_MODEL", "").strip()
        provider = provider_for(model, explicit, local)
        model = model or DEFAULT_MODELS.get(provider, "")
        if not model:
            raise ConfigError("Set AI_MODEL to the model served by your local/custom endpoint.")
        key = os.getenv("AI_API_KEY", "").strip()
        if not key and provider == "gemini":
            key = os.getenv("GEMINI_API_KEY", "").strip()
        primary = cls._endpoint(provider, model, key, os.getenv("AI_BASE_URL", "").strip())
        try:
            max_tokens = int(os.getenv("AI_MAX_TOKENS", "8192"))
        except ValueError:
            max_tokens = 0
        if not 256 <= max_tokens <= 131072:
            raise ConfigError("AI_MAX_TOKENS must be a whole number from 256 to 131072.")
        tuning = {"max_tokens": max_tokens, "reasoning_effort": os.getenv("AI_REASONING_EFFORT", "").strip()}
        backup_key = os.getenv("AI_API_KEY_BACKUP", "").strip()
        legacy = (
            not any(os.getenv(k, "").strip() for k in ("AI_MODEL", "AI_API_KEY")) and provider == "gemini"
        )
        default_backup = os.getenv("GEMINI_MODEL_BACKUP", "gemini-3.1-flash-lite") if legacy else ""
        backup_model = os.getenv("AI_MODEL_BACKUP", default_backup).strip()
        if not backup_model and not backup_key:
            return cls(primary, **tuning)
        backup_model = backup_model or model
        # Backups deliberately use the same provider/endpoint; keys never cross providers.
        backup = cls._endpoint(provider, backup_model, backup_key or key, primary.base_url)
        return cls(primary, backup if backup != primary else None, **tuning)

    @staticmethod
    def _endpoint(provider, model, key, base_url):
        if not key and provider != "local":
            raise ConfigError("Missing configuration: AI_API_KEY (or GEMINI_API_KEY for Gemini).")
        base_url = base_url or BASE_URLS.get(provider, "")
        if provider not in ("gemini", "anthropic"):
            base_url = base_url.rstrip("/").removesuffix("/chat/completions")
        if provider != "gemini":
            parsed = urlparse(base_url)
            if (
                parsed.scheme not in ("http", "https")
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise ConfigError("AI_BASE_URL must be an http(s) URL without embedded credentials.")
            if parsed.query or parsed.fragment:
                raise ConfigError("AI_BASE_URL must not contain a query or fragment.")
        elif base_url:
            raise ConfigError("AI_BASE_URL is not supported for Gemini; leave it empty.")
        return Endpoint(provider, model, key, base_url.rstrip("/"))

    @classmethod
    def from_config(cls, config):
        if getattr(config, "ai_settings", None) is not None:
            return config.ai_settings
        # Compatibility for integrations constructing Config directly.
        primary = Endpoint("gemini", config.model, config.gemini_key)
        backup = Endpoint("gemini", config.model_backup, config.gemini_key) if config.model_backup else None
        return cls(primary, backup if backup != primary else None)


class ModelError(Exception):
    def __init__(self, code=None, *, transient=False):
        super().__init__("Model request failed")
        self.code = code
        self.transient = transient


def gemini_schema(schema):
    """Convert standard JSON Schema to the Gemini SDK's schema subset."""
    out = {}
    for key, value in schema.items():
        if key not in {"type", "description", "properties", "required", "items", "enum", "nullable"}:
            continue
        if key == "properties":
            out[key] = {name: gemini_schema(prop) for name, prop in value.items()}
        elif key == "items":
            out[key] = gemini_schema(value)
        elif key == "type" and isinstance(value, list):
            kinds = [kind for kind in value if kind != "null"]
            if kinds:
                out[key] = kinds[0]
            if len(kinds) < len(value):
                out["nullable"] = True
        else:
            out[key] = value
    if "properties" in out and "type" not in out:
        out["type"] = "object"
    return out


class AIModels:
    def __init__(self, config):
        self.settings = ModelSettings.from_config(config)
        self._gemini = {}

    def start_request(self):
        return ModelRequest(self)

    def _gemini_client(self, endpoint):
        if endpoint.api_key not in self._gemini:
            self._gemini[endpoint.api_key] = genai.Client(
                api_key=endpoint.api_key,
                http_options=google_types.HttpOptions(
                    timeout=60000, retry_options=google_types.HttpRetryOptions(attempts=1)
                ),
            )
        return self._gemini[endpoint.api_key]

    async def generate(self, endpoint, contents, system, tools):
        try:
            if endpoint.provider == "gemini":
                return await self._generate_gemini(endpoint, contents, system, tools)
            payload = self._payload(endpoint, contents, system, tools, self.settings)
            headers = {}
            if endpoint.provider == "anthropic":
                path = "/messages"
                headers = {"x-api-key": endpoint.api_key, "anthropic-version": "2023-06-01"}
            else:
                path = "/chat/completions"
                if endpoint.api_key:
                    headers["Authorization"] = "Bearer " + endpoint.api_key
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(endpoint.base_url + path, headers=headers, json=payload)
                response.raise_for_status()
                return self._parse(endpoint.provider, response.json())
        except (errors.APIError, httpx.HTTPStatusError) as exc:
            code = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else exc.code
            raise ModelError(
                code, transient=code in (408, 429) or (isinstance(code, int) and 500 <= code < 600)
            ) from None
        except (httpx.TransportError, TimeoutError):
            raise ModelError(transient=True) from None
        except (ValueError, KeyError, TypeError, IndexError):
            # Malformed responses/arguments must never reach tool handlers or leak in logs.
            raise ModelError() from None

    async def _generate_gemini(self, endpoint, contents, system, tools):
        history = []
        for content in contents:
            if content.provider == "gemini" and content.native is not None:
                history.append(content.native)
                continue
            parts = []
            for part in content.parts:
                if part.text:
                    parts.append(google_types.Part(text=part.text))
                if part.function_call:
                    fc = part.function_call
                    parts.append(
                        google_types.Part(
                            function_call=google_types.FunctionCall(name=fc.name, args=fc.args, id=fc.id)
                        )
                    )
                if part.function_response:
                    fr = part.function_response
                    parts.append(
                        google_types.Part(
                            function_response=google_types.FunctionResponse(
                                name=fr.name, response=fr.response, id=fr.id
                            )
                        )
                    )
            history.append(google_types.Content(role=content.role, parts=parts))
        declarations = [
            google_types.FunctionDeclaration(
                name=d.name,
                description=d.description,
                parameters=gemini_schema(d.parameters or {"type": "object", "properties": {}}),
            )
            for tool in tools
            for d in tool.function_declarations
        ]
        response = await self._gemini_client(endpoint).aio.models.generate_content(
            model=endpoint.model,
            contents=history,
            config=google_types.GenerateContentConfig(
                system_instruction=system,
                tools=[google_types.Tool(function_declarations=declarations)] if declarations else None,
                temperature=0,
                max_output_tokens=self.settings.max_tokens,
            ),
        )
        candidate = response.candidates[0] if response.candidates else None
        native = getattr(candidate, "content", None)
        parts = []
        for part in getattr(native, "parts", None) or []:
            if part.function_call:
                fc = part.function_call
                parts.append(
                    Part(function_call=FunctionCall(fc.name, fc.args or {}, getattr(fc, "id", None)))
                )
            elif part.text and not getattr(part, "thought", False):
                parts.append(Part(text=part.text))
        return Content("model", parts, native, "gemini")

    @staticmethod
    def _payload(endpoint, contents, system, tools, settings=None):
        settings = settings or ModelSettings(endpoint)
        anthropic = endpoint.provider == "anthropic"
        messages = [] if anthropic else [{"role": "system", "content": system}]
        for content in contents:
            if content.provider == endpoint.provider and content.native is not None:
                messages.append(content.native)
                continue
            if anthropic:
                blocks = []
                for part in content.parts:
                    if part.text:
                        blocks.append({"type": "text", "text": part.text})
                    if part.function_call:
                        fc = part.function_call
                        blocks.append({"type": "tool_use", "id": fc.id, "name": fc.name, "input": fc.args})
                    if part.function_response:
                        fr = part.function_response
                        blocks.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": fr.id,
                                "content": json.dumps(fr.response),
                                "is_error": not fr.response.get("success", True),
                            }
                        )
                messages.append(
                    {"role": "assistant" if content.role == "model" else "user", "content": blocks}
                )
            else:
                text = "".join(p.text for p in content.parts if p.text)
                calls = [
                    {
                        "id": p.function_call.id,
                        "type": "function",
                        "function": {
                            "name": p.function_call.name,
                            "arguments": json.dumps(p.function_call.args),
                        },
                    }
                    for p in content.parts
                    if p.function_call
                ]
                if text or calls:
                    message = {
                        "role": "assistant" if content.role == "model" else "user",
                        "content": text or None,
                    }
                    if calls:
                        message["tool_calls"] = calls
                    messages.append(message)
                for part in content.parts:
                    if part.function_response:
                        fr = part.function_response
                        messages.append(
                            {"role": "tool", "tool_call_id": fr.id, "content": json.dumps(fr.response)}
                        )
        payload = {"model": endpoint.model, "messages": messages}
        # Omitting temperature also supports reasoning models that reject temperature=0.
        payload["max_completion_tokens" if endpoint.provider == "openai" else "max_tokens"] = (
            settings.max_tokens
        )
        if settings.reasoning_effort and not anthropic:
            payload["reasoning_effort"] = settings.reasoning_effort
        if anthropic:
            payload["system"] = system
        declarations = [d for tool in tools for d in tool.function_declarations]
        if declarations:
            payload["tools"] = [
                {
                    "name": d.name,
                    "description": d.description,
                    "input_schema": d.parameters or {"type": "object", "properties": {}},
                }
                if anthropic
                else {
                    "type": "function",
                    "function": {
                        "name": d.name,
                        "description": d.description,
                        "parameters": d.parameters or {"type": "object", "properties": {}},
                    },
                }
                for d in declarations
            ]
        return payload

    @staticmethod
    def _parse(provider, data):
        parts = []
        if provider == "anthropic":
            blocks = data["content"]
            native = {"role": "assistant", "content": blocks}
            for block in blocks:
                if block["type"] == "text":
                    parts.append(Part(text=block["text"]))
                elif block["type"] == "tool_use":
                    parts.append(Part(function_call=FunctionCall(block["name"], block["input"], block["id"])))
        else:
            if not data["choices"]:
                return Content("model", [])
            if data["choices"][0].get("finish_reason") == "length":
                log.warning("model_reply_truncated provider=%s", provider)
            message = data["choices"][0]["message"]
            if not isinstance(message, dict):
                raise ValueError("Invalid assistant message")
            native = {
                k: v
                for k, v in message.items()
                if k in ("role", "content", "tool_calls", "reasoning_content")
            }
            if message.get("content"):
                parts.append(Part(text=message["content"]))
            for call in message.get("tool_calls") or []:
                fn = call["function"]
                parts.append(
                    Part(function_call=FunctionCall(fn["name"], json.loads(fn["arguments"]), call["id"]))
                )
        call_ids = set()
        for part in parts:
            if part.text is not None and not isinstance(part.text, str):
                raise ValueError("Invalid assistant text")
            fc = part.function_call
            if fc:
                if (
                    not isinstance(fc.args, dict)
                    or not isinstance(fc.id, str)
                    or not fc.id
                    or not isinstance(fc.name, str)
                    or not fc.name
                    or fc.id in call_ids
                ):
                    raise ValueError("Invalid tool call")
                call_ids.add(fc.id)
        return Content("model", parts, native, provider)


class ModelRequest:
    """One fallback per user request; continuing tool turns stay on the backup."""

    def __init__(self, models):
        self.models = models
        self.endpoint = models.settings.primary
        self.used_backup = False

    async def generate(self, *, contents, system, tools):
        while True:
            try:
                return await self.models.generate(self.endpoint, contents, system, tools)
            except ModelError as exc:
                log.error("model_error provider=%s code=%s", self.endpoint.provider, exc.code)
                backup = self.models.settings.backup
                # A different credential can also recover from a revoked/invalid primary key.
                auth_fallback = exc.code in (401, 403) and backup and backup.api_key != self.endpoint.api_key
                if backup and not self.used_backup and (exc.transient or auth_fallback):
                    log.warning("model_fallback provider=%s", backup.provider)
                    self.endpoint = backup
                    self.used_backup = True
                    continue
                raise
