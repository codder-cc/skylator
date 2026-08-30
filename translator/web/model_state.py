"""
Declarative desired-model state per agent + heartbeat reconciliation.

The connection between master and agents is pull-only: the master can never initiate a
connection, it can only enqueue work an agent later pulls. That makes imperative,
fire-and-forget "load model X" commands fragile — if an agent dequeues the command then
reboots before acking, the master forgets the agent still owes it model X, and a phased
auto-translate job silently runs against the wrong model.

This module fixes that with a Kubernetes-style reconcile loop. The master records the model
each agent *should* be running (the desired state). On every heartbeat it compares the
agent's reported model to the desired one and re-issues a load command if they diverge and
none is in flight. Result: model switching is self-healing — it survives agent reboots,
missed commands, and lost acks — without the master ever calling the agent.

  set_desired(label, spec, …)  — record what an agent should run
  dispatch_all(labels)         — A: parallel, non-blocking initial fan-out
  reconcile(label)             — B: re-issue on heartbeat if diverged (idempotent — C)
  all_satisfied(labels)        — convergence check for the orchestrator's wait loop
  clear(label|job_id)          — drop the desire when the job ends

On top of the job-scoped desires sits a PERSISTENT per-agent default model (D): every
explicit model load from the UI records "this agent should run model X" in a JSON file
(kept out of translations.db, which is deleted to force re-imports). When an agent
heartbeats with NO model loaded and no job desire is active, the default is materialized
into a desired entry and the normal reconcile loop loads it — so a machine that reboots
(or a host that restarts, then sees the agent come up empty) converges back to its model
without anyone touching the UI. An explicit unload suspends the default (the user asked
for an empty machine); the next explicit load lifts the suspension.

  set_default(label, spec)     — D: record the durable per-agent default (persists)
  suspend_default(label)       — stop auto-heal until the next explicit load
  clear_default(label)         — forget the default entirely
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path

log = logging.getLogger(__name__)

# job_id marker for desires materialized from a persistent default (never matches a real job)
DEFAULT_JOB_ID = "__default__"

# per-request / transport keys that must never be persisted in a default spec
_VOLATILE_SPEC_KEYS = ("hf_token", "transfer", "load", "delivery")


def _sanitize_spec(spec: dict) -> dict:
    return {k: v for k, v in spec.items() if k not in _VOLATILE_SPEC_KEYS}


def model_matches(spec: dict, reported_model: str | None) -> bool:
    """Is the agent's currently-loaded model the one `spec` asks for?

    Agents report a model label that is the gguf filename (llamacpp) or the repo leaf
    (MLX) — see remote_server: `state.model_label = req.gguf_filename or req.repo_id`.
    We match leniently (exact / suffix / containment) because the host stores the full
    filename while an agent may report a normalized variant.
    """
    if not reported_model:
        return False
    want = (spec.get("gguf_filename") or "").strip()
    if not want:
        want = (spec.get("repo_id") or "").split("/")[-1].strip()
    if not want:
        return False
    rm = reported_model.strip()
    return rm == want or rm.endswith(want) or want.endswith(rm) or want in rm


class ModelStateManager:
    # Max seconds to wait on an in-flight load before assuming it was lost and re-issuing.
    LOAD_TIMEOUT = 3600.0

    def __init__(self, registry, defaults_path: Path | str | None = None):
        self._registry = registry
        self._lock = threading.Lock()
        # label -> {spec, job_id, hf_token, in_flight_chunk, issued_at}
        self._desired: dict[str, dict] = {}
        # label -> {spec, suspended, updated_at} — durable, survives host restarts
        self._defaults_path = Path(defaults_path) if defaults_path else None
        self._defaults: dict[str, dict] = self._load_defaults()

    # ── persistent per-agent defaults (D) ────────────────────────────────────
    def _load_defaults(self) -> dict[str, dict]:
        if self._defaults_path is None or not self._defaults_path.exists():
            return {}
        try:
            data = json.loads(self._defaults_path.read_text(encoding="utf-8"))
            return {k: v for k, v in data.items() if isinstance(v, dict) and v.get("spec")}
        except Exception as exc:
            log.warning("model defaults: failed to read %s (%s) — starting empty",
                        self._defaults_path, exc)
            return {}

    def _save_defaults_nolock(self) -> None:
        if self._defaults_path is None:
            return
        try:
            self._defaults_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._defaults_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._defaults, indent=2, ensure_ascii=False),
                           encoding="utf-8")
            os.replace(tmp, self._defaults_path)
        except Exception as exc:
            log.warning("model defaults: failed to persist to %s: %s", self._defaults_path, exc)

    def set_default(self, label: str, spec: dict) -> None:
        """Record the durable default model for an agent (called on every explicit UI
        load). Lifts any suspension — an explicit load is the user re-arming auto-heal."""
        clean = _sanitize_spec(spec)
        if not (clean.get("repo_id") or clean.get("gguf_filename") or clean.get("model_path")):
            return                                      # nothing identifiable to reload later
        with self._lock:
            self._defaults[label] = {"spec": clean, "suspended": False,
                                     "updated_at": time.time()}
            self._save_defaults_nolock()
            # A desire materialised from the PREVIOUS default is pinned until LOAD_TIMEOUT
            # (an hour), so without this the agent keeps chasing the old spec — and if that
            # spec was unloadable, it sits with no model the whole time. A job's desire is
            # left alone: only the default-driven one is re-derived.
            d = self._desired.get(label)
            if d is not None and d.get("job_id") == DEFAULT_JOB_ID and                     _sanitize_spec(d.get("spec") or {}) != clean:
                del self._desired[label]

    def get_default(self, label: str) -> dict | None:
        with self._lock:
            d = self._defaults.get(label)
            return dict(d) if d else None

    def get_all_defaults(self) -> dict[str, dict]:
        with self._lock:
            return {k: dict(v) for k, v in self._defaults.items()}

    def suspend_default(self, label: str) -> bool:
        """Explicit unload: keep the default on file but stop auto-heal until the next
        explicit load — otherwise the reconcile loop would fight the user's unload."""
        with self._lock:
            d = self._defaults.get(label)
            if not d:
                return False
            d["suspended"] = True
            self._save_defaults_nolock()
            # drop a materialized default desire so it stops reconciling right away
            cur = self._desired.get(label)
            if cur and cur.get("job_id") == DEFAULT_JOB_ID:
                self._desired.pop(label, None)
            return True

    def clear_default(self, label: str) -> bool:
        with self._lock:
            existed = self._defaults.pop(label, None) is not None
            if existed:
                self._save_defaults_nolock()
            cur = self._desired.get(label)
            if cur and cur.get("job_id") == DEFAULT_JOB_ID:
                self._desired.pop(label, None)
            return existed

    def _materialize_default_nolock(self, label: str, hf_token: str) -> dict | None:
        """If the agent has NO model loaded, no active desire, and an unsuspended default
        on file, turn the default into a regular desired entry so the normal reconcile
        loop loads it. Agents that already run *some* model are left alone — job desires
        own switching, and reverting after every job would churn multi-GB loads."""
        rec = self._defaults.get(label)
        if not rec or rec.get("suspended"):
            return None
        w = self._registry.get(label)
        if w is None or getattr(w, "model", None):
            return None
        d = {"spec": dict(rec["spec"]), "job_id": DEFAULT_JOB_ID, "hf_token": hf_token,
             "in_flight_chunk": None, "issued_at": 0.0}
        self._desired[label] = d
        log.info("model defaults: %s is up with no model — restoring default %s",
                 label, rec["spec"].get("gguf_filename") or rec["spec"].get("repo_id"))
        return d

    # ── desired-state CRUD ──────────────────────────────────────────────────
    def set_desired(self, label: str, spec: dict, job_id: str = "", hf_token: str = "") -> None:
        with self._lock:
            self._desired[label] = {
                "spec": dict(spec), "job_id": job_id, "hf_token": hf_token,
                "in_flight_chunk": None, "issued_at": 0.0,
            }

    def get_desired(self, label: str) -> dict | None:
        with self._lock:
            d = self._desired.get(label)
            return dict(d) if d else None

    def clear(self, label: str | None = None, job_id: str | None = None) -> None:
        with self._lock:
            if label is not None:
                self._desired.pop(label, None)
            elif job_id is not None:
                for lbl in [k for k, v in self._desired.items() if v.get("job_id") == job_id]:
                    self._desired.pop(lbl, None)
            else:
                self._desired.clear()

    # ── convergence checks ──────────────────────────────────────────────────
    def _satisfied_nolock(self, label: str, d: dict) -> bool:
        w = self._registry.get(label)              # registry has its own lock — safe to call
        return model_matches(d["spec"], w.model if w else None)

    def is_satisfied(self, label: str) -> bool:
        with self._lock:
            d = self._desired.get(label)
            if not d:
                return True                        # no desire → nothing to converge to
            return self._satisfied_nolock(label, d)

    def all_satisfied(self, labels) -> bool:
        return all(self.is_satisfied(lbl) for lbl in labels)

    def pending(self, labels) -> list[str]:
        return [lbl for lbl in labels if not self.is_satisfied(lbl)]

    # ── load issuance (idempotent — C) ──────────────────────────────────────
    def _enqueue_load_nolock(self, label: str, d: dict) -> str:
        spec = d["spec"]
        cid = str(uuid.uuid4())
        # Forward the whole spec (n_gpu_layers, batch_size, model_path, …) so a restored
        # default loads with the same parameters the user picked, not bare defaults.
        payload = _sanitize_spec(spec)
        payload.setdefault("backend_type", "llamacpp")
        payload.setdefault("repo_id", "")
        payload.setdefault("gguf_filename", "")
        payload.setdefault("n_ctx", 8192)
        payload["hf_token"] = d.get("hf_token", "")
        payload["load"] = True
        self._registry.enqueue_chunk(label, {
            "type": "load_model", "chunk_id": cid,
            "payload": payload,
        })
        d["in_flight_chunk"] = cid
        d["issued_at"] = time.time()
        return cid

    def dispatch_all(self, labels) -> int:
        """A — initial parallel fan-out. Enqueues a load for every agent not already on the
        desired model, without blocking on any of them. Returns how many loads were issued."""
        issued = 0
        with self._lock:
            for label in labels:
                d = self._desired.get(label)
                if d and not self._satisfied_nolock(label, d):
                    self._enqueue_load_nolock(label, d)
                    issued += 1
        return issued

    def reconcile(self, label: str, hf_token: str = "") -> bool:
        """B — called on each heartbeat. If the agent has diverged from its desired model and
        nothing fresh is in flight (or the in-flight load went stale), re-issue the load.
        With no active desire, falls back to the persistent default (D) when the agent
        reports no model loaded. Returns True if a (re)load was issued."""
        with self._lock:
            d = self._desired.get(label)
            if not d:
                d = self._materialize_default_nolock(label, hf_token)
            if not d:
                return False
            if self._satisfied_nolock(label, d):
                d["in_flight_chunk"] = None        # converged — stop reconciling
                return False
            stale = (time.time() - d["issued_at"]) > self.LOAD_TIMEOUT
            if d["in_flight_chunk"] is None or stale:
                self._enqueue_load_nolock(label, d)
                return True
            return False
