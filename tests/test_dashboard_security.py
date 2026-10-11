"""Dashboard safety gates must reject callers before any side effect occurs."""
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

import config
from agent import access_tokens, approvals, cortex, self_mod, skill_forge
from dashboard import server, webhook_auth


MASTER = "test-dashboard-master"
APPROVALS = [
    (cortex, "approve_action", "/api/pending-actions/1/approve", {}),
    (cortex, "reject_action", "/api/pending-actions/1/reject", {}),
    (skill_forge, "approve_forged", "/api/forged-tools/1/approve", {}),
    (skill_forge, "reject_forged", "/api/forged-tools/1/reject", {}),
    (approvals, "approve", "/api/staged-writes/all/approve", {}),
    (approvals, "reject", "/api/staged-writes/1/reject", {}),
    (self_mod, "update_system_prompt", "/api/selfmod/prompt", {"addition": "test"}),
    (self_mod, "revert", "/api/selfmod/revert", {}),
]


@pytest.fixture
def client(test_db, monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_TOKEN", MASTER)
    server._throttle.reset("testclient")
    access_tokens.init_db()
    return TestClient(server.app)


@pytest.mark.parametrize("module,function,path,body", APPROVALS)
@pytest.mark.parametrize("caller", ["tokenless", "paired", "anonymous"])
def test_approval_refuses_non_owner_before_execution(client, monkeypatch, module, function, path, body, caller):
    side_effect = Mock(return_value="Registered dynamic tool test")
    monkeypatch.setattr(module, function, side_effect)
    headers = {}
    if caller == "tokenless":
        monkeypatch.setattr(config, "DASHBOARD_TOKEN", "")
    elif caller == "paired":
        headers["Authorization"] = f"Bearer {access_tokens.issue(label='paired test')}"
    response = client.post(path, json=body, headers=headers)
    assert response.status_code == (401 if caller == "anonymous" else 403)
    side_effect.assert_not_called()


@pytest.mark.parametrize("module,function,path,body", APPROVALS)
def test_owner_can_manage_approvals(client, monkeypatch, module, function, path, body):
    side_effect = Mock(return_value="Registered dynamic tool test")
    monkeypatch.setattr(module, function, side_effect)
    response = client.post(path, json=body, headers={
        "Authorization": f"Bearer {MASTER}", "Origin": "http://testserver",
    })
    assert response.status_code == 200
    side_effect.assert_called_once()


@pytest.mark.parametrize("kind", ["skill_code", "skill", "email"])
@pytest.mark.parametrize("caller", ["master", "paired", "tokenless"])
def test_executable_and_external_staged_writes_require_owner(client, monkeypatch, kind, caller):
    approvals.stage(kind, {"name": "test", "content": "test"})
    [pending] = approvals.list_pending()
    side_effect = Mock(return_value="installed")
    monkeypatch.setattr(approvals, "_apply", side_effect)
    headers = {"Authorization": f"Bearer {MASTER}"}
    if caller == "paired":
        headers["Authorization"] = f"Bearer {access_tokens.issue(label='paired test')}"
    elif caller == "tokenless":
        monkeypatch.setattr(config, "DASHBOARD_TOKEN", "")
        headers = {}
    response = client.post(f"/api/staged-writes/{pending['id']}/approve", headers=headers)
    assert response.status_code == (200 if caller == "master" else 403)
    if caller == "master":
        side_effect.assert_called_once()
        assert approvals.list_pending() == []
    else:
        side_effect.assert_not_called()
        assert approvals.list_pending()[0]["status"] == "pending"


def test_non_owner_helper_cannot_install_skill_code(client, monkeypatch):
    from agent import skills
    approvals.stage("skill_code", {"name": "test", "code": "print('test')"})
    [pending] = approvals.list_pending()
    side_effect = Mock()
    monkeypatch.setattr(skills, "create_skill", side_effect)
    result = approvals.approve(pending['id'], by_owner=False)
    assert "Only the owner" in result
    side_effect.assert_not_called()
    assert approvals.list_pending()[0]["status"] == "pending"


def test_simple_cross_site_approval_is_refused_before_registration(client, monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_TOKEN", "")
    registration = Mock()
    monkeypatch.setattr(skill_forge, "approve_forged", registration)
    response = client.post("/api/forged-tools/1/approve", content="", headers={
        "Origin": "https://evil.example", "Content-Type": "text/plain",
    })
    assert response.status_code == 403
    registration.assert_not_called()


@pytest.mark.parametrize("origin,authenticated", [
    ("https://evil.example", False), ("https://evil.example", True),
    ("null", False), ("null", True), ("chrome-extension://untrusted", False),
])
@pytest.mark.parametrize("method,path,module,function,body", [
    ("POST", "/api/memories", server.longterm, "remember", {"content": "test"}),
    ("DELETE", "/api/memories/1", server.longterm, "forget", None),
    ("PATCH", "/api/goals/1", server.goals, "update_goal", {"status": "done"}),
])
def test_cross_origin_mutation_has_no_side_effect(client, monkeypatch, origin, authenticated, method, path, module, function, body):
    monkeypatch.setattr(config, "DASHBOARD_TOKEN", MASTER if authenticated else "")
    side_effect = Mock(return_value="ok")
    monkeypatch.setattr(module, function, side_effect)
    headers = {"Origin": origin}
    if authenticated:
        headers["Authorization"] = f"Bearer {MASTER}"
    response = client.request(method, path, json=body, headers=headers)
    assert response.status_code == 403
    side_effect.assert_not_called()


@pytest.mark.parametrize("origin", [None, "http://testserver"])
def test_local_mutations_and_non_browser_clients_still_work(client, monkeypatch, origin):
    monkeypatch.setattr(config, "DASHBOARD_TOKEN", "")
    side_effect = Mock(return_value="ok")
    monkeypatch.setattr(server.longterm, "remember", side_effect)
    headers = {"Origin": origin} if origin is not None else {}
    assert client.post("/api/memories", json={"content": "test"}, headers=headers).status_code == 200
    side_effect.assert_called_once()


@pytest.mark.parametrize("origin", ["chrome-extension://test", "moz-extension://test"])
@pytest.mark.parametrize("caller", ["master", "paired", "invalid"])
def test_extension_access_requires_valid_bearer(client, monkeypatch, origin, caller):
    log = Mock()
    monkeypatch.setattr(server, "_awareness_log", log)
    token = {"master": MASTER, "invalid": "invalid"}.get(caller)
    if caller == "paired":
        token = access_tokens.issue(label="extension")
    response = client.post("/api/awareness/ingest", json={"content": "test"}, headers={
        "Origin": origin, "Authorization": f"Bearer {token}",
    })
    assert response.status_code == (401 if caller == "invalid" else 200)
    if caller == "invalid":
        log.add.assert_not_called()
    else:
        log.add.assert_called_once()


def test_extension_preflight_remains_available(client):
    response = client.options("/api/awareness/ingest", headers={
        "Origin": "chrome-extension://test", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type",
    })
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "chrome-extension://test"


def test_local_https_proxy_origin_is_supported(client, monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_TOKEN", "")
    side_effect = Mock(return_value="ok")
    monkeypatch.setattr(server.longterm, "remember", side_effect)
    proxy_client = TestClient(server.app, client=("127.0.0.1", 5555))
    response = proxy_client.post("/api/memories", json={"content": "test"}, headers={
        "Host": "apex.example", "Origin": "https://apex.example",
    })
    assert response.status_code == 200
    side_effect.assert_called_once()


@pytest.mark.parametrize("signed", [False, True])
def test_webhook_exemption_still_requires_signature(client, monkeypatch, signed):
    monkeypatch.setattr(config, "TWILIO_AUTH_TOKEN", "test-twilio")
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://apex.example")
    side_effect = Mock(return_value="<Response/>")
    monkeypatch.setattr(server.phone_mod, "dispatch_inbound_sms", side_effect)
    payload = {"From": "+15550001111", "Body": "test"}
    headers = {"Origin": "https://remote.example"}
    if signed:
        headers["X-Twilio-Signature"] = webhook_auth.twilio_signature(
            "test-twilio", "https://apex.example/twilio/sms", payload,
        )
    response = client.post("/twilio/sms", data=payload, headers=headers)
    assert response.status_code == (200 if signed else 403)
    if signed:
        side_effect.assert_called_once()
    else:
        side_effect.assert_not_called()
