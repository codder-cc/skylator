"""
tools_rt path-jail (defense-in-depth): /tools file+subprocess endpoints reject request paths
that escape the configured roots (mods / temp / backup / model / project), even without a token.
"""
from tests.harness_agent import real_app


def _mods_dir(app):
    cfg = app.config["TRANSLATOR_CFG"]
    return str(cfg.paths.mods_dirs[0]) if cfg and cfg.paths.mods_dirs else None


def test_traversal_path_rejected(tmp_path):
    with real_app(tmp_path) as (app, client):
        # a path clearly outside any translator root
        r = client.post("/tools/esp/parse", json={"path": "/etc/passwd"})
        assert r.status_code == 403
        r = client.post("/tools/bsa/unpack", json={"bsa_path": "/tmp/evil.bsa"})
        assert r.status_code == 403


def test_path_under_project_root_allowed(tmp_path):
    with real_app(tmp_path) as (app, client):
        # a path under the project root passes the jail (then 404s because it doesn't exist —
        # proving it got past the 403 guard, not blocked by it)
        import pathlib
        proj = pathlib.Path(__file__).resolve().parents[1]
        r = client.post("/tools/esp/parse", json={"path": str(proj / "does_not_exist.esp")})
        assert r.status_code == 404          # allowed by the jail, then not-found
