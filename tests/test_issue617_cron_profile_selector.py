"""Regression coverage for issue #617 scheduled-job profile selection."""

import io
import json
import sys
import types
from pathlib import Path
from urllib.parse import urlparse

import pytest

REPO = Path(__file__).resolve().parent.parent


class _JSONHandler:
    def __init__(self):
        self.status = None
        self.headers = {}
        self.response_headers = []
        self.wfile = io.BytesIO()
        self.request = None

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.response_headers.append((key, value))

    def end_headers(self):
        pass


def _payload(handler):
    return json.loads(handler.wfile.getvalue().decode("utf-8"))


class _ForbiddenCronProfileContext:
    def __enter__(self):
        pytest.fail("remote cron GET must fail before entering cron_profile_context")

    def __exit__(self, exc_type, exc, tb):
        return False


def _remote_profile(name="jen", label="Jen"):
    return {
        "name": name,
        "label": label,
        "owner_label": label,
        "remote_proxy": True,
        "profile_kind": "remote_gateway_proxy",
        "capabilities": {
            "chat": "remote_gateway",
            "cron": "unsupported_remote_no_api",
            "memory": "unsupported_remote_no_api",
            "filesystem": "unsupported_remote_no_api",
            "kanban_dispatch": "not_via_webui_proxy",
        },
    }


def test_cron_api_serializes_legacy_profile_as_explicit_server_default():
    from api.routes import _cron_job_for_api

    legacy = {"id": "legacy", "name": "Legacy job"}
    payload = _cron_job_for_api(legacy)

    assert payload["profile"] is None
    assert "profile" not in legacy, "API serialization must not mutate stored legacy jobs"



def test_cron_profile_value_validates_against_existing_profiles(monkeypatch):
    import api.profiles as profiles
    from api.routes import _normalize_cron_profile_value

    monkeypatch.setattr(
        profiles,
        "list_profiles_api",
        lambda: [
            {"name": "default"},
            {"name": "research"},
            _remote_profile(),
        ],
    )

    assert _normalize_cron_profile_value(" research ") == "research"
    assert _normalize_cron_profile_value("jen") == "jen"
    assert _normalize_cron_profile_value("") is None
    assert _normalize_cron_profile_value(None) is None
    with pytest.raises(ValueError, match="Unknown profile: missing"):
        _normalize_cron_profile_value("missing")



def test_cron_create_api_persists_local_profile_and_returns_it(monkeypatch):
    import api.profiles as profiles
    import api.routes as routes

    created = {
        "id": "job617",
        "name": "Profiled job",
        "prompt": "ping",
        "schedule": {"kind": "interval", "minutes": 60},
    }
    updated = {**created, "profile": "research"}
    calls = []

    cron_pkg = types.ModuleType("cron")
    cron_pkg.__path__ = []
    cron_jobs = types.ModuleType("cron.jobs")
    cron_jobs.create_job = lambda **kwargs: calls.append(("create", kwargs)) or dict(created)
    cron_jobs.update_job = lambda job_id, updates: calls.append(("update", job_id, updates)) or dict(updated)

    monkeypatch.setattr(profiles, "list_profiles_api", lambda: [{"name": "research"}, _remote_profile()])
    monkeypatch.setitem(sys.modules, "cron", cron_pkg)
    monkeypatch.setitem(sys.modules, "cron.jobs", cron_jobs)

    handler = _JSONHandler()
    routes._handle_cron_create(
        handler,
        {
            "name": "Profiled job",
            "prompt": "ping",
            "schedule": "every 60m",
            "deliver": "local",
            "profile": "research",
        },
    )

    body = _payload(handler)
    assert handler.status == 200
    assert body["ok"] is True
    assert body["job"]["profile"] == "research"
    assert calls[0][0] == "create"
    assert calls[1] == ("update", "job617", {"profile": "research"})



def test_cron_create_api_rejects_remote_proxy_profile_before_persisting(monkeypatch):
    import api.profiles as profiles
    import api.routes as routes

    cron_pkg = types.ModuleType("cron")
    cron_pkg.__path__ = []
    cron_jobs = types.ModuleType("cron.jobs")
    cron_jobs.create_job = lambda **kwargs: pytest.fail("remote proxy cron profiles must not create jobs")
    cron_jobs.update_job = lambda *args, **kwargs: pytest.fail("remote proxy cron profiles must not update jobs")

    monkeypatch.setattr(profiles, "list_profiles_api", lambda: [{"name": "research"}, _remote_profile()])
    monkeypatch.setitem(sys.modules, "cron", cron_pkg)
    monkeypatch.setitem(sys.modules, "cron.jobs", cron_jobs)

    handler = _JSONHandler()
    routes._handle_cron_create(
        handler,
        {"prompt": "ping", "schedule": "every 60m", "profile": "jen"},
    )

    body = _payload(handler)
    assert handler.status == 409
    assert body["error_type"] == "unsupported_remote_no_api"
    assert body["profile"] == "jen"
    assert "/api/crons/create fails closed" in body["reason"]



def test_cron_update_api_accepts_profile_clear_and_rejects_remote_proxy(monkeypatch):
    import api.profiles as profiles
    import api.routes as routes

    calls = []
    jobs = {
        "local-job": {"id": "local-job", "name": "Updated", "profile": "research"},
        "remote-job": {"id": "remote-job", "name": "Remote", "profile": "jen"},
    }
    cron_pkg = types.ModuleType("cron")
    cron_pkg.__path__ = []
    cron_jobs = types.ModuleType("cron.jobs")

    def get_job(job_id):
        return dict(jobs[job_id]) if job_id in jobs else None

    def update_job(job_id, updates):
        calls.append((job_id, updates))
        merged = {**jobs[job_id], **updates}
        jobs[job_id] = merged
        return merged

    cron_jobs.get_job = get_job
    cron_jobs.update_job = update_job
    monkeypatch.setattr(profiles, "list_profiles_api", lambda: [{"name": "research"}, _remote_profile()])
    monkeypatch.setitem(sys.modules, "cron", cron_pkg)
    monkeypatch.setitem(sys.modules, "cron.jobs", cron_jobs)

    handler = _JSONHandler()
    routes._handle_cron_update(handler, {"job_id": "local-job", "profile": ""})
    assert handler.status == 200
    assert _payload(handler)["job"]["profile"] is None
    assert calls == [("local-job", {"profile": None})]

    remote_handler = _JSONHandler()
    routes._handle_cron_update(remote_handler, {"job_id": "local-job", "profile": "jen"})
    remote_body = _payload(remote_handler)
    assert remote_handler.status == 409
    assert remote_body["error_type"] == "unsupported_remote_no_api"
    assert calls == [("local-job", {"profile": None})]

    existing_remote_handler = _JSONHandler()
    routes._handle_cron_update(existing_remote_handler, {"job_id": "remote-job", "name": "still blocked"})
    existing_remote_body = _payload(existing_remote_handler)
    assert existing_remote_handler.status == 409
    assert existing_remote_body["profile"] == "jen"
    assert calls == [("local-job", {"profile": None})]


@pytest.mark.parametrize(
    ("action", "handler_name", "job_mutator"),
    [
        ("delete", "_handle_cron_delete", "remove_job"),
        ("run", "_handle_cron_run", None),
        ("pause", "_handle_cron_pause", "pause_job"),
        ("resume", "_handle_cron_resume", "resume_job"),
    ],
)
def test_remote_cron_job_actions_fail_closed(monkeypatch, action, handler_name, job_mutator):
    import api.profiles as profiles
    import api.routes as routes

    cron_pkg = types.ModuleType("cron")
    cron_pkg.__path__ = []
    cron_jobs = types.ModuleType("cron.jobs")
    cron_jobs.get_job = lambda job_id: {"id": job_id, "name": "Remote job", "profile": "jen"}

    if job_mutator == "remove_job":
        cron_jobs.remove_job = lambda job_id: pytest.fail("remote cron delete must fail closed")
    elif job_mutator == "pause_job":
        cron_jobs.pause_job = lambda job_id, reason=None: pytest.fail("remote cron pause must fail closed")
    elif job_mutator == "resume_job":
        cron_jobs.resume_job = lambda job_id: pytest.fail("remote cron resume must fail closed")

    monkeypatch.setattr(profiles, "list_profiles_api", lambda: [
        {"name": "research", "remote_proxy": False},
        _remote_profile(),
    ])
    monkeypatch.setitem(sys.modules, "cron", cron_pkg)
    monkeypatch.setitem(sys.modules, "cron.jobs", cron_jobs)

    handler = _JSONHandler()
    getattr(routes, handler_name)(handler, {"job_id": "job617"})

    body = _payload(handler)
    assert handler.status == 409
    assert body["error_type"] == "unsupported_remote_no_api"
    assert body["profile"] == "jen"
    assert f"/api/crons/{action} fails closed" in body["reason"]


@pytest.mark.parametrize(
    ("path", "needle"),
    [
        ("/api/crons", "/api/crons fails closed"),
        ("/api/crons/output?job_id=job617&filename=run.md", "/api/crons/output fails closed"),
        ("/api/crons/history?job_id=job617", "/api/crons/history fails closed"),
        ("/api/crons/run?job_id=job617&filename=run.md", "/api/crons/run fails closed"),
        ("/api/crons/recent?since=0", "/api/crons/recent fails closed"),
        ("/api/crons/status?job_id=job617", "/api/crons/status fails closed"),
        ("/api/crons/delivery-options", "/api/crons/delivery-options fails closed"),
    ],
)
def test_remote_cron_get_routes_fail_closed_before_local_profile_context(monkeypatch, path, needle):
    import api.profiles as profiles
    import api.routes as routes

    cron_pkg = types.ModuleType("cron")
    cron_pkg.__path__ = []
    cron_jobs = types.ModuleType("cron.jobs")
    setattr(cron_jobs, "list_jobs", lambda **kwargs: pytest.fail("remote cron GET list must not touch local cron.jobs"))

    monkeypatch.setattr(profiles, "list_profiles_api", lambda: [{"name": "default"}, _remote_profile()])
    monkeypatch.setattr(profiles, "cron_profile_context", lambda: _ForbiddenCronProfileContext())
    monkeypatch.setitem(sys.modules, "cron", cron_pkg)
    monkeypatch.setitem(sys.modules, "cron.jobs", cron_jobs)

    profiles.set_request_profile("jen")
    try:
        handler = _JSONHandler()
        handled = routes.handle_get(handler, urlparse(path))
    finally:
        profiles.clear_request_profile()

    body = _payload(handler)
    assert handled is True
    assert handler.status == 409
    assert body["error_type"] == "unsupported_remote_no_api"
    assert body["profile"] == "jen"
    assert body["remote_proxy"] is True
    assert needle in body["reason"]



def test_manual_cron_run_uses_execution_profile_but_persists_to_owning_store(monkeypatch):
    import api.profiles as profiles
    import api.routes as routes

    events = []

    class Ctx:
        def __init__(self, home):
            self.home = str(home)

        def __enter__(self):
            events.append(("enter", self.home))

        def __exit__(self, exc_type, exc, tb):
            events.append(("exit", self.home))

    cron_pkg = types.ModuleType("cron")
    cron_pkg.__path__ = []
    cron_jobs = types.ModuleType("cron.jobs")
    cron_jobs.save_job_output = lambda job_id, output: events.append(("save", job_id, output))
    cron_jobs.mark_job_run = lambda job_id, success, error=None: events.append(("mark", job_id, success, error))
    cron_scheduler = types.ModuleType("cron.scheduler")
    cron_scheduler.run_job = lambda job: events.append(("run", job["id"])) or (True, "output", "final", None)

    def fake_subprocess_run(job, execution_profile_home):
        events.append(("run", job["id"], str(execution_profile_home)))
        return True, "output", "final", None

    monkeypatch.setattr(profiles, "cron_profile_context_for_home", Ctx)
    monkeypatch.setattr(routes, "_run_cron_job_in_profile_subprocess", fake_subprocess_run)
    monkeypatch.setitem(sys.modules, "cron", cron_pkg)
    monkeypatch.setitem(sys.modules, "cron.jobs", cron_jobs)
    monkeypatch.setitem(sys.modules, "cron.scheduler", cron_scheduler)

    routes._mark_cron_running("job617")
    routes._run_cron_tracked(
        {"id": "job617"},
        profile_home="/hermes/default",
        execution_profile_home="/hermes/profiles/research",
    )

    assert events == [
        ("run", "job617", "/hermes/profiles/research"),
        ("enter", "/hermes/default"),
        ("save", "job617", "output"),
        ("mark", "job617", True, None),
        ("exit", "/hermes/default"),
    ]
    assert routes._is_cron_running("job617") == (False, 0.0)



def test_cron_profile_selector_source_hooks_present():
    panels = (REPO / "static" / "panels.js").read_text(encoding="utf-8")
    css = (REPO / "static" / "style.css").read_text(encoding="utf-8")
    i18n = (REPO / "static" / "i18n.js").read_text(encoding="utf-8")

    assert "async function loadCronProfiles()" in panels
    assert "api('/api/profiles')" in panels
    assert 'id="cronFormProfile"' in panels
    assert "remote gateway — unsupported" in panels
    assert "...routeHints" in panels
    assert "cron-profile-badge" in panels
    assert "cron-owner-badge" in panels
    assert ".cron-profile-badge" in css
    assert "cron_profile_server_default" in i18n
    assert "cron_profile_server_default_hint" in i18n
