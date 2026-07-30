"""Narrow token-authorized creation of a persistent WebUI session and stream."""
from __future__ import annotations

import hmac
import logging
import os
import re
from contextlib import contextmanager

from api.helpers import j

logger = logging.getLogger(__name__)
PATH = "/api/internal/session-launch"
HEADER = "X-Hermes-Service-Token"
TOKEN_ENV = "HERMES_WEBUI_SESSION_LAUNCH_TOKEN"
_PROFILE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_ALLOWED_FIELDS = frozenset({"profile", "workspace", "model", "reasoning", "initial_prompt"})
_MAX_PROMPT_CHARS = 24_000


def is_service_launch_path(path: str) -> bool:
    return path == PATH


def _authorized(handler) -> bool:
    expected = os.environ.get(TOKEN_ENV, "").strip()
    presented = str(handler.headers.get(HEADER, "") or "")
    return len(expected) >= 32 and hmac.compare_digest(expected, presented)


def _validate_body(body: object) -> tuple[dict | None, str | None]:
    if not isinstance(body, dict):
        return None, "JSON object required"
    if set(body) - _ALLOWED_FIELDS:
        return None, "unsupported field"
    profile = str(body.get("profile") or "").strip()
    workspace = str(body.get("workspace") or "").strip()
    prompt = str(body.get("initial_prompt") or "").strip()
    model = body.get("model")
    if not _PROFILE_RE.fullmatch(profile):
        return None, "valid profile is required"
    if not workspace:
        return None, "workspace is required"
    if not prompt or len(prompt) > _MAX_PROMPT_CHARS:
        return None, "initial_prompt is required and must not exceed 24000 characters"
    if model is not None and (not isinstance(model, str) or not model.strip() or len(model.strip()) > 256):
        return None, "model must be a non-empty string of at most 256 characters"
    if body.get("reasoning") not in (None, "", "profile_default"):
        return None, "reasoning must be profile_default"
    return {"profile": profile, "workspace": workspace, "model": model.strip() if isinstance(model, str) else None, "initial_prompt": prompt}, None


@contextmanager
def _profile_scope(profile: str):
    from api.profiles import clear_request_profile, set_request_profile
    set_request_profile(profile)
    try:
        yield
    finally:
        clear_request_profile()


def _profile_exists(profile: str) -> bool:
    from api.profiles import list_profiles_api
    return any(str(row.get("name") or "") == profile and not row.get("remote_proxy") for row in list_profiles_api(include_remote=False))


def _verify_active_stream(session_id: str, stream_id: str) -> bool:
    from api.config import STREAMS, STREAMS_LOCK
    from api.models import get_session
    try:
        session = get_session(session_id)
    except KeyError:
        return False
    if not hmac.compare_digest(str(getattr(session, "active_stream_id", "") or ""), stream_id):
        return False
    with STREAMS_LOCK:
        return stream_id in STREAMS


def handle_service_session_launch(handler, body: object) -> bool:
    """Launch one persisted, profile-scoped turn without leaking prompt or token."""
    if not _authorized(handler):
        j(handler, {"error": "service authorization required"}, status=401)
        return True
    data, error = _validate_body(body)
    if error:
        j(handler, {"error": error}, status=400)
        return True
    assert data is not None
    if not _profile_exists(data["profile"]):
        j(handler, {"error": "profile not found"}, status=404)
        return True
    try:
        from api.models import new_session
        from api.routes import _session_model_state_from_request, start_session_turn
        from api.workspace import resolve_trusted_workspace
        with _profile_scope(data["profile"]):
            workspace = str(resolve_trusted_workspace(data["workspace"]))
            model, provider = _session_model_state_from_request(data["model"], None)
            session = new_session(workspace=workspace, model=model, model_provider=provider, profile=data["profile"])
            session.save()
            result = start_session_turn(session.session_id, data["initial_prompt"], source="service_session_launch")
        status = int(result.get("_status", 200) or 200)
        stream_id = str(result.get("stream_id") or "")
        if status >= 400:
            j(handler, {"error": str(result.get("error") or "session launch failed")}, status=status)
        elif not stream_id or not _verify_active_stream(session.session_id, stream_id):
            j(handler, {"error": "session launch stream could not be verified active"}, status=502)
        else:
            j(handler, {"session_id": session.session_id, "stream_id": stream_id, "verified_state": {"session_id": session.session_id, "stream_id": stream_id, "active": True}, "profile": data["profile"], "workspace": workspace, "model": model, "reasoning": "profile_default"}, status=201)
    except ValueError as exc:
        j(handler, {"error": str(exc)}, status=400)
    except Exception:
        logger.exception("service session launch failed")
        j(handler, {"error": "session launch failed"}, status=500)
    return True
