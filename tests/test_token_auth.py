"""
Token gating: when SKYLATOR_TOKEN is configured, the code-exec-adjacent surfaces
(agent/admin/OTA + the /tools BSArch/FFDec endpoints) require the X-Skylator-Token header.
Off by default (trusted LAN); this pins the enforced behavior when it's on.
"""
from tests.harness_agent import real_app


def test_tools_require_token_when_configured(tmp_path):
    with real_app(tmp_path) as (app, client):
        app.config["API_TOKEN"] = "s3cret"          # simulate SKYLATOR_TOKEN set

        # /tools is now protected — no header → 401
        r = client.post("/tools/esp/parse", json={"esp_path": "x.esp"})
        assert r.status_code == 401

        # correct header → passes auth (not 401; may 4xx/5xx for other reasons, that's fine)
        r = client.post("/tools/esp/parse", json={"esp_path": "x.esp"},
                        headers={"X-Skylator-Token": "s3cret"})
        assert r.status_code != 401

        # agent endpoints stay gated too
        assert client.post("/api/workers/heartbeat", json={"label": "w"}).status_code == 401


def test_no_token_means_open(tmp_path):
    with real_app(tmp_path) as (app, client):
        app.config["API_TOKEN"] = ""                 # default: enforcement off
        r = client.post("/tools/esp/parse", json={"esp_path": "x.esp"})
        assert r.status_code != 401                  # not blocked by auth
