"""Offline provider contracts: config, tool round trips, credentials and failover."""

import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from bot.AIModels import (
    AIModels,
    Content,
    Endpoint,
    FunctionDeclaration,
    ModelError,
    ModelSettings,
    Part,
    Tool,
)
from bot.agent import Agent
from bot.config import Config
from bot.integrations import ToolRegistry
from bot.integrations.base import LocalTool, PendingAction
from bot.models import ConfigError


def settings(**env):
    with patch.dict(os.environ, env, clear=True):
        return ModelSettings.from_env()


@pytest.mark.parametrize(
    "name,provider",
    [
        ("gemini-test", "gemini"),
        ("gpt-test", "openai"),
        ("o3-test", "openai"),
        ("claude-test", "anthropic"),
        ("deepseek-chat", "deepseek"),
    ],
)
def test_infers_provider_and_does_not_require_gemini_key(name, provider):
    config = settings(AI_MODEL=name, AI_API_KEY="primary", AI_API_KEY_BACKUP="backup")
    assert config.primary.provider == provider
    assert config.backup.model == name
    assert config.backup.api_key == "backup"
    assert "primary" not in repr(config.primary)


def test_config_load_accepts_cloud_and_keyless_local_without_gemini():
    base = dict(
        DOBBY_ENV_FILE="/nonexistent.env",
        DISCORD_TOKEN="token",
        DISCORD_GUILD_ID="1",
        ALLOWED_USER_IDS="1",
        COMPOSIO_API_KEY="test",
        DATABASE_URL="test",
    )
    for ai_env in (
        {"AI_MODEL": "claude-test", "AI_API_KEY": "test"},
        {"AI_MODEL": "qwen-test", "LOCAL_MODEL": "true"},
    ):
        with patch.dict(os.environ, base | ai_env, clear=True):
            config = Config.load()
        assert config.model == ai_env["AI_MODEL"]
        assert config.gemini_key == ""


def test_explicit_provider_overrides_model_name_and_supports_custom_url():
    config = settings(
        AI_PROVIDER="openai-compatible",
        AI_MODEL="vendor/custom",
        AI_API_KEY="test",
        AI_BASE_URL="https://example.test/v1/",
    )
    assert config.primary.base_url == "https://example.test/v1"
    assert config.backup is None


def test_local_never_uses_legacy_gemini_key_or_cloud_defaults():
    config = settings(LOCAL_MODEL="true", AI_MODEL="gemini-local", GEMINI_API_KEY="cloud-secret")
    assert config.primary.provider == "local"
    assert config.primary.api_key == ""
    assert config.primary.base_url == "http://localhost:11434/v1"
    assert config.backup is None


@pytest.mark.parametrize(
    "env",
    [
        {"LOCAL_MODEL": "maybe"},
        {"LOCAL_MODEL": "true"},
        {"AI_MODEL": "unknown", "AI_API_KEY": "test"},
        {"AI_PROVIDER": "wrong", "AI_API_KEY": "test"},
        {"AI_PROVIDER": "anthropic"},
        {"AI_PROVIDER": "openai-compatible", "AI_MODEL": "custom", "AI_API_KEY": "test"},
        {"LOCAL_MODEL": "true", "AI_MODEL": "local", "AI_BASE_URL": "file:///tmp"},
        {"LOCAL_MODEL": "true", "AI_MODEL": "local", "AI_BASE_URL": "https://key@example.test/v1"},
    ],
)
def test_invalid_configuration_fails_at_startup(env):
    with pytest.raises(ConfigError):
        settings(**env)


def test_backup_configuration_and_legacy_precedence():
    cfg = settings(
        GEMINI_API_KEY="legacy",
        GEMINI_MODEL="gemini-old",
        AI_API_KEY="new",
        AI_MODEL="gpt-test",
        AI_MODEL_BACKUP="gpt-backup",
    )
    assert cfg.primary.api_key == "new"
    assert cfg.backup == Endpoint("openai", "gpt-backup", "new", "https://api.openai.com/v1")
    assert settings(GEMINI_API_KEY="legacy", AI_MODEL_BACKUP="").backup is None
    assert settings(GEMINI_API_KEY="legacy").backup.model == "gemini-3.1-flash-lite"
    assert settings(AI_MODEL="gpt-test", AI_API_KEY="same", AI_API_KEY_BACKUP="same").backup is None


def models(provider="openai", backup=False):
    primary = Endpoint(provider, "test-model", "primary-secret", "https://example.test/v1")
    secondary = Endpoint(provider, "backup-model", "backup-secret", primary.base_url) if backup else None
    return AIModels(SimpleNamespace(ai_settings=ModelSettings(primary, secondary)))


def install_transport(monkeypatch, handler):
    client_class = httpx.AsyncClient
    monkeypatch.setattr(
        "bot.AIModels.httpx.AsyncClient",
        lambda **kwargs: client_class(transport=httpx.MockTransport(handler), **kwargs),
    )


def text_response(provider, text="done"):
    if provider == "anthropic":
        return {"content": [{"type": "text", "text": text}]}
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


def tool_response(provider):
    if provider == "anthropic":
        return {
            "content": [
                {"type": "tool_use", "id": name, "name": "draft", "input": {"text": name}}
                for name in ("call_a", "call_b")
            ]
        }
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "reasoning_content": "provider-private-reasoning",
                    "tool_calls": [
                        {
                            "id": name,
                            "type": "function",
                            "function": {"name": "draft", "arguments": json.dumps({"text": name})},
                        }
                        for name in ("call_a", "call_b")
                    ],
                }
            }
        ]
    }


@pytest.mark.parametrize("provider", ["openai", "anthropic", "deepseek", "local", "openai-compatible"])
def test_agent_tool_roundtrip_with_backup_keeps_ids_pending_and_history(monkeypatch, provider):
    requests = []

    def handle(request):
        payload = json.loads(request.content)
        requests.append((request, payload))
        if len(requests) == 1:
            return httpx.Response(200, json=tool_response(provider))
        if len(requests) == 2:
            return httpx.Response(429, json={"error": "secret error body"})
        return httpx.Response(200, json=text_response(provider))

    install_transport(monkeypatch, handle)
    execute = AsyncMock()
    handler = AsyncMock(
        side_effect=lambda ctx, params: ctx.queue(PendingAction("test", "draft", params["text"], execute))
    )
    declaration = FunctionDeclaration("draft", parameters={"type": "object", "properties": {}})
    local = LocalTool(declaration, handler)
    registry = ToolRegistry([Tool([declaration])], local={"draft": local})
    config = SimpleNamespace(ai_settings=models(provider, backup=True).settings, timezone="UTC")

    async def run():
        agent = Agent(config, registry)
        with patch("bot.agent.record_action", new=AsyncMock()):
            result = await agent.run(AsyncMock(), "draft", "1", "2", "3")
            await agent.run(AsyncMock(), "next", "1", "2", "3")
        assert result.text == "done"
        assert [p.preview for p in result.pending] == ["call_a", "call_b"]
        assert handler.await_count == 2
        execute.assert_not_awaited()

    asyncio.run(run())
    assert [body["model"] for _, body in requests] == [
        "test-model",
        "test-model",
        "backup-model",
        "test-model",
    ]
    first, failed, retried, next_request = requests
    header = "x-api-key" if provider == "anthropic" else "authorization"
    assert "primary-secret" in first[0].headers[header]
    assert "backup-secret" in retried[0].headers[header]
    assert "primary-secret" in next_request[0].headers[header]
    assert failed[1]["messages"] == retried[1]["messages"]
    if provider == "anthropic":
        assert first[0].url.path == "/v1/messages"
        assert first[0].headers["anthropic-version"] == "2023-06-01"
        assert "system" in first[1]
        assert [b["tool_use_id"] for b in retried[1]["messages"][-1]["content"]] == ["call_a", "call_b"]
        assert "input_schema" in first[1]["tools"][0]
    else:
        assert first[0].url.path == "/v1/chat/completions"
        assert [m["tool_call_id"] for m in retried[1]["messages"][-2:]] == ["call_a", "call_b"]
        assert retried[1]["messages"][-3]["reasoning_content"] == "provider-private-reasoning"
        assert first[1]["tools"][0]["type"] == "function"
        assert "temperature" not in first[1]
        assert ("max_completion_tokens" in first[1]) == (provider == "openai")


@pytest.mark.parametrize(
    "code,attempts", [(400, 1), (404, 1), (401, 2), (403, 2), (408, 2), (429, 2), (503, 2)]
)
def test_http_fallback_is_bounded_and_does_not_log_secrets(monkeypatch, caplog, code, attempts):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(code, json={"error": "private-response-body"})

    install_transport(monkeypatch, handle)

    async def run():
        with pytest.raises(ModelError):
            await (
                models(backup=True)
                .start_request()
                .generate(contents=[Content("user", [Part(text="hi")])], system="system", tools=[])
            )

    asyncio.run(run())
    assert len(requests) == attempts
    assert "primary-secret" not in caplog.text and "backup-secret" not in caplog.text
    assert "private-response-body" not in caplog.text


def test_connection_failure_falls_back_and_local_omits_authorization(monkeypatch):
    calls = []

    def handle(request):
        calls.append(request)
        assert "authorization" not in request.headers
        assert "tools" not in json.loads(request.content)
        if len(calls) == 1:
            raise httpx.ConnectError("private", request=request)
        return httpx.Response(200, json=text_response("local"))

    install_transport(monkeypatch, handle)
    cfg = settings(LOCAL_MODEL="true", AI_MODEL="local-a", AI_MODEL_BACKUP="local-b")

    async def run():
        result = (
            await AIModels(SimpleNamespace(ai_settings=cfg))
            .start_request()
            .generate(contents=[Content("user", [Part(text="hi")])], system="system", tools=[])
        )
        assert result.parts[0].text == "done"

    asyncio.run(run())
    assert len(calls) == 2


@pytest.mark.parametrize("arguments", ["{bad", "[]", "null", '"text"'])
def test_malformed_tool_arguments_are_rejected(monkeypatch, arguments):
    response = tool_response("openai")
    response["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = arguments
    install_transport(monkeypatch, lambda request: httpx.Response(200, json=response))

    async def run():
        with pytest.raises(ModelError):
            await (
                models()
                .start_request()
                .generate(contents=[Content("user", [Part(text="hi")])], system="system", tools=[])
            )

    asyncio.run(run())


def test_gemini_preserves_native_thought_signatures_and_rotates_keys():
    from google.genai import errors, types

    async def run():
        content = types.Content(
            role="model",
            parts=[
                types.Part(
                    thought_signature=b"signature",
                    function_call=types.FunctionCall(name="draft", args={}, id="call_one"),
                )
            ],
        )
        response = types.GenerateContentResponse(candidates=[types.Candidate(content=content)])
        primary, backup = AsyncMock(), AsyncMock()
        primary.side_effect = [response, errors.APIError(401, {})]
        backup.return_value = types.GenerateContentResponse(
            candidates=[types.Candidate(content=types.Content(role="model", parts=[types.Part(text="done")]))]
        )
        clients = [
            SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=fn)))
            for fn in (primary, backup)
        ]
        cfg = settings(AI_API_KEY="one", AI_API_KEY_BACKUP="two", AI_MODEL="gemini-test")
        with patch("bot.AIModels.genai.Client", side_effect=clients) as factory:
            request = AIModels(SimpleNamespace(ai_settings=cfg)).start_request()
            history = [Content("user", [Part(text="hi")])]
            result = await request.generate(contents=history, system="system", tools=[])
            history.extend(
                [
                    result,
                    Content(
                        "user",
                        [
                            Part.from_function_response(
                                name="draft", response={"success": True}, id=result.parts[0].function_call.id
                            )
                        ],
                    ),
                ]
            )
            await request.generate(contents=history, system="system", tools=[])
        assert [c.kwargs["api_key"] for c in factory.call_args_list] == ["one", "two"]
        replayed = backup.await_args.kwargs["contents"]
        assert replayed[1].parts[0].thought_signature == b"signature"
        assert replayed[2].parts[0].function_response.id == "call_one"

    asyncio.run(run())


def test_parallel_tool_batch_cannot_exceed_agent_limit(monkeypatch):
    response = tool_response("openai")
    message = response["choices"][0]["message"]
    message["tool_calls"] = [
        {"id": f"call_{i}", "type": "function", "function": {"name": "draft", "arguments": "{}"}}
        for i in range(12)
    ]
    install_transport(monkeypatch, lambda request: httpx.Response(200, json=response))
    handler = AsyncMock(return_value={"success": True})
    declaration = FunctionDeclaration("draft")
    registry = ToolRegistry([Tool([declaration])], local={"draft": LocalTool(declaration, handler)})

    async def run():
        config = SimpleNamespace(ai_settings=models().settings, timezone="UTC", max_tool_calls=8)
        with patch("bot.agent.record_action", new=AsyncMock()):
            result = await Agent(config, registry).run(AsyncMock(), "draft", "1", "2", "3")
        assert "smaller" in result.text
        assert handler.await_count == 8

    asyncio.run(run())


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_empty_response_returns_no_parts(monkeypatch, provider):
    data = {"content": []} if provider == "anthropic" else {"choices": []}
    install_transport(monkeypatch, lambda request: httpx.Response(200, json=data))

    async def run():
        result = (
            await models(provider)
            .start_request()
            .generate(contents=[Content("user", [Part(text="hi")])], system="system", tools=[])
        )
        assert result.parts == []

    asyncio.run(run())


def test_model_only_backup_does_not_retry_invalid_credentials(monkeypatch):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(401, json={"error": "unauthorized"})

    install_transport(monkeypatch, handle)
    cfg = settings(AI_MODEL="gpt-test", AI_API_KEY="one", AI_MODEL_BACKUP="gpt-backup")

    async def run():
        with pytest.raises(ModelError):
            await (
                AIModels(SimpleNamespace(ai_settings=cfg))
                .start_request()
                .generate(contents=[Content("user", [Part(text="hi")])], system="system", tools=[])
            )

    asyncio.run(run())
    assert len(calls) == 1


def test_modal_style_full_chat_completions_url_is_accepted():
    cfg = settings(
        AI_PROVIDER="openai-compatible",
        AI_MODEL="Qwen/Qwen3-8B",
        AI_API_KEY="token-id.token-secret",
        AI_BASE_URL="https://example.modal.direct/v1/chat/completions",
    )
    assert cfg.primary.base_url == "https://example.modal.direct/v1"


def test_token_budget_and_reasoning_effort_reach_the_request():
    cfg = settings(
        AI_PROVIDER="openai-compatible",
        AI_MODEL="custom",
        AI_API_KEY="test",
        AI_BASE_URL="https://example.test/v1",
        AI_MAX_TOKENS="16000",
        AI_REASONING_EFFORT="high",
    )
    payload = AIModels._payload(cfg.primary, [], "system", [], cfg)
    assert payload["max_tokens"] == 16000
    assert payload["reasoning_effort"] == "high"


def test_defaults_omit_reasoning_effort_and_never_send_it_to_anthropic():
    default = settings(AI_MODEL="gpt-test", AI_API_KEY="test")
    assert default.max_tokens == 8192
    assert "reasoning_effort" not in AIModels._payload(default.primary, [], "s", [], default)
    claude = settings(AI_MODEL="claude-test", AI_API_KEY="test", AI_REASONING_EFFORT="high")
    assert "reasoning_effort" not in AIModels._payload(claude.primary, [], "s", [], claude)


@pytest.mark.parametrize("value", ["100", "999999", "lots"])
def test_invalid_token_budget_fails_at_startup(value):
    with pytest.raises(ConfigError):
        settings(AI_MODEL="gpt-test", AI_API_KEY="test", AI_MAX_TOKENS=value)


def test_reply_cut_off_at_token_limit_is_logged(caplog):
    data = {"choices": [{"finish_reason": "length", "message": {"role": "assistant", "content": "cut"}}]}
    with caplog.at_level("WARNING", logger="ai_models"):
        content = AIModels._parse("openai-compatible", data)
    assert content.parts[0].text == "cut"
    assert "model_reply_truncated" in caplog.text
