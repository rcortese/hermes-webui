"""Default-off Hermes Gateway bridge for browser-originated chat turns."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit

from api.config import (
    CANCEL_FLAGS,
    STREAMS,
    STREAMS_LOCK,
    STREAM_LAST_EVENT_ID,
    STREAM_LIVE_TOOL_CALLS,
    STREAM_PARTIAL_TEXT,
    STREAM_REASONING_TEXT,
    _get_session_agent_lock,
    register_active_run,
    unregister_active_run,
    update_active_run,
)
from api.helpers import _redact_text, redact_session_data
from api.models import get_session
from api.run_journal import RunJournalWriter

logger = logging.getLogger(__name__)

_WEBUI_CHAT_BACKEND_ENV = "HERMES_WEBUI_CHAT_BACKEND"
_WEBUI_GATEWAY_BASE_URL_ENV = "HERMES_WEBUI_GATEWAY_BASE_URL"
_WEBUI_GATEWAY_API_KEY_ENV = "HERMES_WEBUI_GATEWAY_API_KEY"
_PROFILE_PROXY_ENV_PREFIX = "HERMES_WEBUI_PROFILE_PROXY_"
_GATEWAY_CHAT_BACKENDS = {"gateway", "api_server", "api-server"}


def _base_url_host_only(raw_url: str | None) -> str | None:
    value = str(raw_url or "").strip()
    if not value:
        return None
    try:
        parsed = urlsplit(value)
    except Exception:
        return None
    host = parsed.hostname or None
    if not host:
        return None
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if parsed.port is not None:
        return f"{host}:{parsed.port}"
    return host


def _execution_target_error(
    *,
    selected_profile: str,
    profile_kind: str,
    remote_persona: str | None,
    base_url: str | None,
    message: str,
    error_type: str,
    status: int = 502,
) -> dict:
    return {
        "ok": False,
        "selected_profile": selected_profile,
        "profile_kind": profile_kind,
        "execution_target": "fail_closed",
        "remote_persona": remote_persona,
        "base_url_host_only": _base_url_host_only(base_url),
        "error": message,
        "error_type": error_type,
        "status": status,
    }


def _profile_proxy_entries_from_env(environ: dict[str, str] | None = None) -> dict[str, dict]:
    """Return configured remote profile proxies from environment variables.

    Shape per profile:
      HERMES_WEBUI_PROFILE_PROXY_JEN_BASE_URL=http://jen:8642
      HERMES_WEBUI_PROFILE_PROXY_JEN_API_KEY_ENV=API_SERVER_KEY
      HERMES_WEBUI_PROFILE_PROXY_JEN_LABEL=Jen

    API keys are resolved at request time and are never included in the public
    profile list.
    """
    source = os.environ if environ is None else environ
    entries: dict[str, dict] = {}
    for key, value in source.items():
        if not key.startswith(_PROFILE_PROXY_ENV_PREFIX) or not key.endswith("_BASE_URL"):
            continue
        token = key[len(_PROFILE_PROXY_ENV_PREFIX):-len("_BASE_URL")]
        if not token:
            continue
        name = token.lower().replace("_", "-")
        prefix = f"{_PROFILE_PROXY_ENV_PREFIX}{token}_"
        base_url = str(value or "").strip().rstrip("/")
        if not base_url:
            continue
        api_key_env = str(source.get(prefix + "API_KEY_ENV") or "").strip()
        api_key = str(source.get(api_key_env) or "").strip() if api_key_env else str(source.get(prefix + "API_KEY") or "").strip()
        entries[name] = {
            "name": name,
            "label": str(source.get(prefix + "LABEL") or name).strip() or name,
            "base_url": base_url,
            "api_key": api_key,
            "api_key_configured": bool(api_key),
            "remote_profile": str(source.get(prefix + "REMOTE_PROFILE") or name).strip() or name,
            "session_key_prefix": str(source.get(prefix + "SESSION_KEY_PREFIX") or f"webui:{name}").strip() or f"webui:{name}",
            "source": "env",
        }
    return entries


def _profile_proxy_entries_from_config(config_data=None, environ: dict[str, str] | None = None) -> dict[str, dict]:
    cfg = config_data if isinstance(config_data, dict) else {}
    webui_cfg = cfg.get("webui") if isinstance(cfg.get("webui"), dict) else {}
    raw = webui_cfg.get("profile_proxies") if isinstance(webui_cfg, dict) else None
    if raw is None:
        raw = cfg.get("webui_profile_proxies")
    if not isinstance(raw, dict):
        return {}
    source = os.environ if environ is None else environ
    entries: dict[str, dict] = {}
    for name, item in raw.items():
        if not isinstance(item, dict):
            continue
        profile = str(name or "").strip().lower()
        base_url = str(item.get("base_url") or "").strip().rstrip("/")
        if not profile or not base_url:
            continue
        api_key_env = str(item.get("api_key_env") or "").strip()
        api_key = str(source.get(api_key_env) or "").strip() if api_key_env else str(item.get("api_key") or "").strip()
        entries[profile] = {
            "name": profile,
            "label": str(item.get("label") or profile).strip() or profile,
            "base_url": base_url,
            "api_key": api_key,
            "api_key_configured": bool(api_key),
            "remote_profile": str(item.get("remote_profile") or profile).strip() or profile,
            "session_key_prefix": str(item.get("session_key_prefix") or f"webui:{profile}").strip() or f"webui:{profile}",
            "source": "config",
        }
    return entries


def profile_proxy_entries(config_data=None, environ: dict[str, str] | None = None) -> dict[str, dict]:
    entries = _profile_proxy_entries_from_config(config_data, environ)
    entries.update(_profile_proxy_entries_from_env(environ))
    return entries


def profile_proxy_for(name: str, config_data=None, environ: dict[str, str] | None = None) -> dict | None:
    return profile_proxy_entries(config_data, environ).get(str(name or "").strip().lower())


_PROFILE_PROXY_PUBLIC_ORDER = {
    "jen": 0,
    "denholm": 1,
    "roy": 2,
    "richmond": 3,
    "the-elders": 4,
}


def _profile_proxy_public_sort_key(profile: dict) -> tuple[int, str]:
    name = str(profile.get("name") or "")
    return (_PROFILE_PROXY_PUBLIC_ORDER.get(name, len(_PROFILE_PROXY_PUBLIC_ORDER)), name)


def _remote_proxy_capabilities() -> dict[str, str]:
    return {
        "chat": "remote_gateway",
        "cron": "unsupported_remote_no_api",
        "memory": "unsupported_remote_no_api",
        "filesystem": "unsupported_remote_no_api",
        "kanban_dispatch": "not_via_webui_proxy",
    }


def profile_proxy_public_entries(config_data=None, environ: dict[str, str] | None = None) -> list[dict]:
    public = []
    for item in profile_proxy_entries(config_data, environ).values():
        base_url_host_only = _base_url_host_only(item.get("base_url"))
        public.append({
            "name": item["name"],
            "path": None,
            "is_default": False,
            "is_active": False,
            "gateway_running": True,
            "model": item.get("remote_profile") or item["name"],
            "provider": "remote-gateway",
            "has_env": bool(item.get("api_key_configured")),
            "skill_count": 0,
            "enabled_skills": 0,
            "total_skills": 0,
            "remote_proxy": True,
            "profile_kind": "remote_gateway_proxy",
            "base_url": base_url_host_only,
            "base_url_host_only": base_url_host_only,
            "label": item.get("label") or item["name"],
            "remote_profile": item.get("remote_profile") or item["name"],
            "proxy_profile": item["name"],
            "backend": "remote_gateway",
            "backend_label": "remote gateway",
            "owner_label": item.get("label") or item["name"],
            "federated_id": f"remote-profile:{item['name']}",
            "capabilities": _remote_proxy_capabilities(),
        })
    return sorted(public, key=_profile_proxy_public_sort_key)


def resolve_chat_execution_target(
    selected_profile: str | None,
    config_data=None,
    environ: dict[str, str] | None = None,
    *,
    profiles: list[dict] | None = None,
) -> dict:
    """Resolve the effective execution target for a WebUI chat request.

    Remote gateway proxies must route through their configured persona API even
    when the global gateway backend is disabled. Unknown or incomplete remote
    selections fail closed; they must never fall back to local agent execution.
    """
    selected = str(selected_profile or "").strip() or "default"
    cfg = config_data if isinstance(config_data, dict) else {}
    source = dict(os.environ if environ is None else environ)
    known_profiles = profiles
    if known_profiles is None:
        try:
            from api.profiles import list_profiles_api

            known_profiles = list_profiles_api()
        except Exception:
            known_profiles = []

    local_profile = None
    remote_profile = None
    for item in known_profiles or []:
        if str((item or {}).get("name") or "").strip() != selected:
            continue
        if bool((item or {}).get("remote_proxy")):
            remote_profile = dict(item)
        else:
            local_profile = dict(item)

    proxy = profile_proxy_for(selected, cfg, source)
    if proxy and local_profile:
        return _execution_target_error(
            selected_profile=selected,
            profile_kind="ambiguous_profile",
            remote_persona=str(proxy.get("remote_profile") or selected).strip() or selected,
            base_url=proxy.get("base_url"),
            message=(
                f"Selected profile '{selected}' is ambiguous: both a local profile and a remote gateway proxy exist. "
                "Disambiguate the selection before starting chat."
            ),
            error_type="ambiguous_profile",
            status=409,
        )

    if proxy:
        base_url = str(proxy.get("base_url") or "").strip().rstrip("/")
        remote_persona = str(proxy.get("remote_profile") or selected).strip() or selected
        if not base_url:
            return _execution_target_error(
                selected_profile=selected,
                profile_kind="remote_gateway_proxy",
                remote_persona=remote_persona,
                base_url=base_url,
                message=f"Remote gateway proxy '{selected}' is not fully configured (missing base URL).",
                error_type="remote_proxy_incomplete",
            )
        gateway_config = dict(proxy)
        gateway_config["base_url"] = base_url
        return {
            "ok": True,
            "selected_profile": selected,
            "profile_kind": "remote_gateway_proxy",
            "execution_target": "remote_gateway",
            "remote_persona": remote_persona,
            "base_url_host_only": _base_url_host_only(base_url),
            "gateway_config": gateway_config,
        }

    if remote_profile:
        return _execution_target_error(
            selected_profile=selected,
            profile_kind="remote_gateway_proxy",
            remote_persona=str((remote_profile or {}).get("remote_profile") or selected).strip() or selected,
            base_url=(remote_profile or {}).get("base_url"),
            message=f"Remote gateway proxy '{selected}' is not fully configured (missing base URL).",
            error_type="remote_proxy_incomplete",
        )

    gateway_enabled = webui_gateway_chat_enabled(cfg, source)
    if local_profile or selected == "default":
        execution_target = "local_gateway" if gateway_enabled else "local_legacy"
        base_url = _gateway_base_url(cfg, source) if execution_target == "local_gateway" else None
        gateway_config = None
        if execution_target == "local_gateway":
            gateway_config = {
                "base_url": str(base_url or "").strip().rstrip("/"),
                "api_key": _gateway_api_key(source),
                "session_key_prefix": "webui",
            }
        return {
            "ok": True,
            "selected_profile": selected,
            "profile_kind": "local_profile",
            "execution_target": execution_target,
            "remote_persona": None,
            "base_url_host_only": _base_url_host_only(base_url),
            "gateway_config": gateway_config,
        }

    return _execution_target_error(
        selected_profile=selected,
        profile_kind="unknown_profile",
        remote_persona=None,
        base_url=None,
        message=(
            f"Selected profile '{selected}' is not configured as a local profile or remote gateway proxy. "
            "Chat cannot fall back to a local runtime for this selection."
        ),
        error_type="profile_not_configured",
        status=404,
    )


def log_chat_execution_target(decision: dict) -> None:
    logger.info(
        "webui_chat_execution_target selected_profile=%s profile_kind=%s execution_target=%s remote_persona=%s base_url_host_only=%s",
        decision.get("selected_profile"),
        decision.get("profile_kind"),
        decision.get("execution_target"),
        decision.get("remote_persona"),
        decision.get("base_url_host_only"),
    )


def webui_chat_backend_mode(config_data=None, environ: dict[str, str] | None = None) -> str:
    """Return the explicitly selected browser chat backend.

    The default remains the in-process WebUI runtime. Only explicit gateway
    values opt browser chat into the Hermes API server bridge; generic truthy
    strings are deliberately ignored so deployments do not change execution
    ownership by accident.
    """
    source = os.environ if environ is None else environ
    cfg = config_data if isinstance(config_data, dict) else {}
    raw = str(
        source.get(_WEBUI_CHAT_BACKEND_ENV)
        or cfg.get("webui_chat_backend")
        or ""
    ).strip().lower()
    if raw in _GATEWAY_CHAT_BACKENDS:
        return "gateway"
    return "legacy"


def webui_gateway_chat_enabled(config_data=None, environ: dict[str, str] | None = None) -> bool:
    return webui_chat_backend_mode(config_data, environ) == "gateway"


def _gateway_base_url(config_data=None, environ: dict[str, str] | None = None) -> str:
    source = os.environ if environ is None else environ
    cfg = config_data if isinstance(config_data, dict) else {}
    raw = str(
        source.get(_WEBUI_GATEWAY_BASE_URL_ENV)
        or cfg.get("webui_gateway_base_url")
        or "http://127.0.0.1:8642"
    ).strip()
    return raw.rstrip("/") or "http://127.0.0.1:8642"


def _gateway_api_key(environ: dict[str, str] | None = None) -> str:
    source = os.environ if environ is None else environ
    return str(
        source.get(_WEBUI_GATEWAY_API_KEY_ENV)
        or source.get("API_SERVER_KEY")
        or ""
    ).strip()


def gateway_chat_config_status(config_data=None, environ: dict[str, str] | None = None) -> dict:
    """Return redacted Gateway-backed chat configuration status."""
    mode = webui_chat_backend_mode(config_data, environ)
    base_url = _gateway_base_url(config_data, environ)
    return {
        "enabled": mode == "gateway",
        "backend": mode,
        "base_url_configured": bool(base_url),
        "api_key_configured": bool(_gateway_api_key(environ)),
    }


def _gateway_http_error_event(exc: urllib.error.HTTPError, err_body: str, *, api_key_configured: bool) -> dict:
    safe = _redact_text(err_body or str(exc))[:500]
    if exc.code == 401:
        return {
            "label": "Gateway authentication failed",
            "type": "gateway_auth_error",
            "message": "Gateway rejected the WebUI API key (HTTP 401).",
            "hint": (
                "Set HERMES_WEBUI_GATEWAY_API_KEY to the same value as the Hermes Gateway "
                "API_SERVER_KEY, or disable HERMES_WEBUI_CHAT_BACKEND=gateway."
                if not api_key_configured
                else "Check that HERMES_WEBUI_GATEWAY_API_KEY matches the Hermes Gateway API_SERVER_KEY."
            ),
        }
    return {
        "label": "Gateway request failed",
        "type": "gateway_http_error",
        "message": f"Gateway returned HTTP {exc.code}.",
        "hint": safe or "Check the configured Gateway API server.",
    }


def _gateway_sse_delta(payload: dict) -> str:
    """Extract assistant text from an OpenAI-compatible streaming chunk."""
    try:
        choices = payload.get("choices") or []
        if not choices:
            return ""
        choice = choices[0] or {}
        delta = choice.get("delta") or {}
        content = delta.get("content")
        if isinstance(content, str):
            return content
        message = choice.get("message") or {}
        content = message.get("content")
        return content if isinstance(content, str) else ""
    except Exception:
        return ""


def _gateway_stream_usage(payload: dict) -> dict:
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return {}
    return {
        "input_tokens": int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
        "estimated_cost": usage.get("estimated_cost") or usage.get("estimated_cost_usd") or 0,
    }


def _gateway_tool_progress_event(payload: dict) -> tuple[str, dict] | None:
    """Translate Hermes Gateway tool-progress SSE payloads to WebUI events."""
    if not isinstance(payload, dict):
        return None
    name = str(payload.get("tool") or payload.get("name") or payload.get("function_name") or "").strip()
    if not name or name.startswith("_"):
        return None
    status = str(payload.get("status") or "running").strip().lower()
    tid = payload.get("toolCallId") or payload.get("tool_call_id") or payload.get("id")
    is_complete = status in {"completed", "complete", "success", "error", "failed"}
    event_payload = {
        "event_type": "tool.completed" if is_complete else "tool.started",
        "name": name,
        "preview": payload.get("label") or payload.get("preview"),
        "args": payload.get("args") if isinstance(payload.get("args"), dict) else {},
        "is_error": status in {"error", "failed"},
    }
    if tid:
        event_payload["tid"] = str(tid)
    return ("tool_complete" if is_complete else "tool"), event_payload


def _stream_writeback_is_current(session: Any, stream_id: str) -> bool:
    return bool(stream_id and getattr(session, "active_stream_id", None) == stream_id)


def _clear_gateway_pending_state(session: Any, stream_id: str) -> None:
    if not _stream_writeback_is_current(session, stream_id):
        return
    session.active_stream_id = None
    session.pending_user_message = None
    session.pending_attachments = None
    session.pending_started_at = None
    session.save()


def _run_gateway_chat_streaming(
    session_id,
    msg_text,
    model,
    workspace,
    stream_id,
    attachments=None,
    *,
    model_provider=None,
    gateway_config: dict | None = None,
):
    """Bridge a WebUI chat turn through Hermes Gateway's API server.

    This default-off path keeps the browser contract unchanged: /api/chat/start
    still returns a local stream_id and /api/chat/stream still receives WebUI SSE
    event names. The worker translates OpenAI-compatible streaming chunks from
    the configured Gateway API server into those local events and persists the
    final user/assistant turn back into the WebUI session.
    """
    q = STREAMS.get(stream_id)
    if q is None:
        return
    register_active_run(
        stream_id,
        session_id=session_id,
        started_at=time.time(),
        phase="gateway-starting",
        workspace=str(workspace),
        model=model,
        provider=model_provider,
        backend="gateway",
    )
    try:
        run_journal = RunJournalWriter(session_id, stream_id)
    except Exception:
        run_journal = None
        logger.debug("Failed to initialize gateway run journal for stream %s", stream_id, exc_info=True)
    cancel_event = threading.Event()
    with STREAMS_LOCK:
        CANCEL_FLAGS[stream_id] = cancel_event
        STREAM_PARTIAL_TEXT[stream_id] = ""
        STREAM_REASONING_TEXT[stream_id] = ""
        STREAM_LIVE_TOOL_CALLS[stream_id] = []

    def put_gateway_event(event, data):
        if cancel_event.is_set() and event not in ("cancel", "error", "apperror"):
            return
        if run_journal is not None:
            try:
                journaled = run_journal.append_sse_event(event, data)
                event_id = (journaled or {}).get("event_id") if isinstance(journaled, dict) else None
                if event_id:
                    STREAM_LAST_EVENT_ID[stream_id] = event_id
            except Exception:
                logger.debug("Failed to append gateway event %s for stream %s", event, stream_id, exc_info=True)
        try:
            q.put_nowait((event, data))
        except Exception:
            logger.debug("Failed to put gateway event to queue")

    s = None
    final_text = ""
    usage = {"input_tokens": 0, "output_tokens": 0, "estimated_cost": 0}
    try:
        s = get_session(session_id)
        from api.config import get_config  # imported lazily to avoid config-cycle churn

        cfg = get_config()
        try:
            from api.streaming import (
                _WEBUI_PROGRESS_PROMPT,
                _load_webui_prefill_context,
                _prefill_messages_with_webui_context,
                _public_prefill_context_status,
            )

            prefill_context = _load_webui_prefill_context(cfg)
            prefill_messages = [
                {"role": "system", "content": _WEBUI_PROGRESS_PROMPT},
                *_prefill_messages_with_webui_context(prefill_context, cfg),
            ]
            put_gateway_event("context_status", {
                "session_id": session_id,
                "prefill": _public_prefill_context_status(prefill_context),
            })
        except Exception:
            logger.debug("Failed to load WebUI gateway prefill context", exc_info=True)
            prefill_messages = []
        selected_gateway = gateway_config if isinstance(gateway_config, dict) else None
        base_url = (selected_gateway or {}).get("base_url") or _gateway_base_url(cfg)
        api_key = (selected_gateway or {}).get("api_key") or _gateway_api_key()
        url = f"{base_url}/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "X-Hermes-Session-Id": session_id,
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
            # Scope Gateway long-term continuity to this WebUI conversation
            # without exposing the browser's auth cookie or CSRF material.
            session_key_prefix = (selected_gateway or {}).get("session_key_prefix") or "webui"
            headers["X-Hermes-Session-Key"] = f"{session_key_prefix}:{session_id}"
        message_content: Any = str(msg_text or "")
        if attachments:
            try:
                from api.streaming import _build_native_multimodal_message

                message_content = _build_native_multimodal_message("", str(msg_text or ""), attachments, str(workspace), cfg=cfg)
            except Exception:
                logger.debug("Failed to build gateway multimodal attachment payload", exc_info=True)
                message_content = str(msg_text or "")
        body = {
            "model": model or "default",
            "stream": True,
            "messages": [*prefill_messages, {"role": "user", "content": message_content}],
        }
        if model_provider:
            body["provider"] = model_provider
        if selected_gateway and selected_gateway.get("remote_profile"):
            body["profile"] = selected_gateway.get("remote_profile")
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        update_active_run(stream_id, phase="gateway-request")
        last_payload = {}
        sse_event = "message"
        with urllib.request.urlopen(req, timeout=600) as resp:
            for raw_line in resp:
                if cancel_event.is_set():
                    put_gateway_event("cancel", {"message": "Cancelled by user"})
                    return
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    sse_event = "message"
                    continue
                if line.startswith("event:"):
                    sse_event = line[6:].strip() or "message"
                    continue
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if sse_event == "hermes.tool.progress":
                    translated = _gateway_tool_progress_event(payload)
                    if translated:
                        event_name, event_payload = translated
                        if stream_id in STREAM_LIVE_TOOL_CALLS:
                            if event_name == "tool":
                                STREAM_LIVE_TOOL_CALLS[stream_id].append({
                                    "name": event_payload.get("name"),
                                    "args": event_payload.get("args") or {},
                                    "done": False,
                                    **({"tid": event_payload.get("tid")} if event_payload.get("tid") else {}),
                                })
                            else:
                                for shared_tc in reversed(STREAM_LIVE_TOOL_CALLS[stream_id]):
                                    if shared_tc.get("done"):
                                        continue
                                    if (
                                        event_payload.get("tid") and shared_tc.get("tid") == event_payload.get("tid")
                                    ) or shared_tc.get("name") == event_payload.get("name"):
                                        shared_tc["done"] = True
                                        shared_tc["is_error"] = bool(event_payload.get("is_error"))
                                        break
                        put_gateway_event(event_name, event_payload)
                        update_active_run(stream_id, phase="gateway-tool", latest_tool=event_payload.get("name"))
                    sse_event = "message"
                    continue
                last_payload = payload
                delta = _gateway_sse_delta(payload)
                if delta:
                    final_text += delta
                    if stream_id in STREAM_PARTIAL_TEXT:
                        STREAM_PARTIAL_TEXT[stream_id] += delta
                    put_gateway_event("token", {"text": delta})
                usage.update({k: v for k, v in _gateway_stream_usage(payload).items() if v})
        usage.update({k: v for k, v in _gateway_stream_usage(last_payload).items() if v})
        assistant_text = final_text.strip()
        if not assistant_text:
            put_gateway_event("apperror", {
                "label": "Gateway returned no response",
                "type": "gateway_empty_response",
                "message": "Gateway returned no assistant message for this turn.",
                "hint": "Check that Hermes Gateway API server is running and reachable.",
            })
            return
        with _get_session_agent_lock(session_id):
            s = get_session(session_id)
            if not _stream_writeback_is_current(s, stream_id):
                return
            now = time.time()
            # Preserve subsecond ordering for gateway-backed turns. Using an
            # integer seconds timestamp gives the user and assistant rows the
            # same sort key; later transcript merges can then fall back to
            # role/content ordering instead of turn order.
            assistant_ts = now + 0.000001
            user_msg = {"role": "user", "content": str(msg_text or ""), "timestamp": now}
            if attachments:
                user_msg["attachments"] = list(attachments)
            assistant_msg = {"role": "assistant", "content": assistant_text, "timestamp": assistant_ts}
            previous_context = list(getattr(s, "context_messages", None) or getattr(s, "messages", None) or [])
            s.context_messages = previous_context + [user_msg, assistant_msg]
            display = list(getattr(s, "messages", None) or [])
            # Avoid duplicating the eager-save checkpointed user message.
            if display:
                latest = display[-1]
                if isinstance(latest, dict) and latest.get("role") == "user":
                    latest_text = " ".join(str(latest.get("content") or "").split())
                    msg_norm = " ".join(str(msg_text or "").split())
                    if latest_text == msg_norm:
                        display = display[:-1]
            s.messages = display + [user_msg, assistant_msg]
            s.active_stream_id = None
            s.pending_user_message = None
            s.pending_attachments = None
            s.pending_started_at = None
            s.workspace = str(workspace)
            s.model = model
            s.model_provider = model_provider
            s.save()
        gateway_session_payload = s.compact() | {"messages": s.messages, "tool_calls": []}
        put_gateway_event("done", {"session": redact_session_data(gateway_session_payload), "usage": usage})
        put_gateway_event("stream_end", {"session_id": session_id})
    except urllib.error.HTTPError as exc:
        try:
            err_body = exc.read(2048).decode("utf-8", errors="replace")
        except Exception:
            err_body = ""
        put_gateway_event(
            "apperror",
            _gateway_http_error_event(exc, err_body, api_key_configured=bool(_gateway_api_key())),
        )
    except Exception as exc:
        safe = _redact_text(str(exc))[:500]
        put_gateway_event("apperror", {
            "label": "Gateway request failed",
            "type": "gateway_error",
            "message": safe or "Gateway request failed.",
            "hint": "Check HERMES_WEBUI_GATEWAY_BASE_URL and Gateway API server health.",
        })
    finally:
        if s is not None:
            try:
                with _get_session_agent_lock(session_id):
                    _clear_gateway_pending_state(get_session(session_id), stream_id)
            except Exception:
                logger.debug("Failed to clear gateway stream state", exc_info=True)
        with STREAMS_LOCK:
            CANCEL_FLAGS.pop(stream_id, None)
            STREAM_PARTIAL_TEXT.pop(stream_id, None)
            STREAM_REASONING_TEXT.pop(stream_id, None)
            STREAM_LIVE_TOOL_CALLS.pop(stream_id, None)
            STREAM_LAST_EVENT_ID.pop(stream_id, None)
            STREAMS.pop(stream_id, None)
        unregister_active_run(stream_id)
