"""
F4 — security hardening.

- safe_under() confines request-derived paths to an allowed root (blocks path traversal in
  backup restore/delete).
- _is_lan_url() blocks SSRF on the server-test fallback (no public IPs, no 169.254 metadata).
- (Token auth is an opt-in before_request hook gated by SKYLATOR_TOKEN; verified by compile
  + wiring — agents attach X-Skylator-Token via their http_client default headers.)
"""
import pytest
from werkzeug.exceptions import HTTPException

from translator.web.routes.utils import safe_under
from translator.web.routes.api import _is_lan_url


def test_safe_under_allows_child(tmp_path):
    (tmp_path / "ModA").mkdir()
    p = safe_under(tmp_path, "ModA")
    assert p.name == "ModA" and p.parent == tmp_path.resolve()


def test_safe_under_blocks_traversal(tmp_path):
    with pytest.raises(HTTPException):
        safe_under(tmp_path, "../../etc/passwd")
    with pytest.raises(HTTPException):
        safe_under(tmp_path, "..")


def test_is_lan_url_allows_private():
    assert _is_lan_url("http://192.168.1.5:8765") is True
    assert _is_lan_url("http://10.0.0.2") is True
    assert _is_lan_url("http://172.16.5.5:5000") is True


def test_is_lan_url_blocks_ssrf():
    assert _is_lan_url("http://169.254.169.254") is False   # cloud metadata
    assert _is_lan_url("http://8.8.8.8") is False           # public
    assert _is_lan_url("http://1.1.1.1:80/health") is False
    assert _is_lan_url("") is False
