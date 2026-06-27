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
"""
from __future__ import annotations

import threading
import time
import uuid


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

    def __init__(self, registry):
        self._registry = registry
        self._lock = threading.Lock()
        # label -> {spec, job_id, hf_token, in_flight_chunk, issued_at}
        self._desired: dict[str, dict] = {}

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
        self._registry.enqueue_chunk(label, {
            "type": "load_model", "chunk_id": cid,
            "payload": {
                "backend_type":  spec.get("backend_type", "llamacpp"),
                "repo_id":       spec.get("repo_id", ""),
                "gguf_filename": spec.get("gguf_filename", ""),
                "n_ctx":         spec.get("n_ctx", 8192),
                "hf_token":      d.get("hf_token", ""),
                "load":          True,
            },
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

    def reconcile(self, label: str) -> bool:
        """B — called on each heartbeat. If the agent has diverged from its desired model and
        nothing fresh is in flight (or the in-flight load went stale), re-issue the load.
        Returns True if a (re)load was issued."""
        with self._lock:
            d = self._desired.get(label)
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
