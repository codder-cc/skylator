"""
R4 — faithful fake-agent harness.

Everywhere else the tests use _FakeDB and mock the registry. This harness instead drives the
*real* Flask app (real blueprints, real WorkerRegistry, real SQLite) over the *real* agent
wire (register → pull chunk → post result → heartbeat). It is the foundation for porting the
mocked chaos/recovery tests onto the real contract, and for strangler-fig dual-running a
rewritten coordination layer against the current one.

`real_app()` builds an isolated app with a temp config + temp DB (never the project DB).
`FakeAgent` speaks exactly the HTTP an actual remote_worker speaks.
"""
from __future__ import annotations

import contextlib
from pathlib import Path

import yaml

_MINIMAL_CONFIG = {
    "paths": {
        "model_cache_dir": "models",
        "nexus_cache": "cache/nexus_cache.json",
        "translation_cache": "cache/translation_cache.json",
        "skyrim_terms": "data/skyrim_terms.json",
        "log_file": "logs/translator.log",
        "mods_dirs": [],
    },
    "ensemble": {"model_b": {"repo_id": "test/model", "local_dir_name": "test"}},
}


@contextlib.contextmanager
def real_app(tmp_path: Path):
    """Build the real Flask app against an isolated temp config + DB. Yields (app, client)."""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(_MINIMAL_CONFIG), encoding="utf-8")
    (tmp_path / "cache").mkdir(exist_ok=True)
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "logs").mkdir(exist_ok=True)

    import translator.config as _cfgmod
    _saved = _cfgmod._config
    _cfgmod._config = None                     # defeat the module-level config cache
    try:
        from translator.web.app import create_app
        app = create_app(cfg_path)
        app.config.update(TESTING=True)
        with app.test_client() as client:
            yield app, client
    finally:
        _cfgmod._config = _saved


class FakeAgent:
    """Speaks the real pull-mode agent protocol over a Flask test client."""

    def __init__(self, client, label="agent-1", url="http://127.0.0.1:9999",
                 translate=None):
        self.client = client
        self.label = label
        self.url = url
        # how to "translate" a chunk's work → result string; default echoes deterministically
        self.translate = translate or (lambda chunk: f"RU::{chunk.get('chunk_id', '')[:8]}")
        self.processed: list[str] = []          # chunk_ids this agent posted results for

    # ── wire calls ──────────────────────────────────────────────────────────
    def register(self, digest=None, protocol=1):
        body = {"label": self.label, "url": self.url, "model": "test.gguf"}
        if digest is not None:
            body["digest"] = digest
        body["protocol"] = protocol
        return self.client.post("/api/workers/register", json=body).get_json()

    def heartbeat(self, **extra):
        body = {"label": self.label, "model": "test.gguf"}
        body.update(extra)
        return self.client.post("/api/workers/heartbeat", json=body).get_json()

    def pull(self, timeout=0):
        r = self.client.get(f"/api/workers/{self.label}/chunk?timeout={timeout}")
        return (r.get_json() or {}).get("chunk")

    def post_result(self, chunk_id, result):
        return self.client.post(f"/api/workers/{self.label}/result",
                                json={"chunk_id": chunk_id, "result": result}).get_json()

    # ── higher-level behaviour ────────────────────────────────────────────────
    def step(self, timeout=0) -> bool:
        """Pull one chunk, translate it, post the result. Returns False if no work."""
        chunk = self.pull(timeout=timeout)
        if not chunk:
            return False
        cid = chunk.get("chunk_id", "")
        self.post_result(cid, self.translate(chunk))
        self.processed.append(cid)
        return True

    def drain(self, max_steps=1000) -> int:
        """Process chunks until the queue is empty (or max_steps). Returns count processed."""
        n = 0
        while n < max_steps and self.step(timeout=0):
            n += 1
        return n

    def step_no_ack(self, timeout=0):
        """Pull a chunk but DO NOT post a result — models an agent that dequeued work then
        crashed (the in-flight item the durability layer must recover)."""
        return self.pull(timeout=timeout)
