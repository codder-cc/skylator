"""
InferenceParams — per-call inference overrides.

All fields except `thinking` are Optional: None means "use the backend's
configured default" (from ModelConfig in server_config.yaml).
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


@dataclass
class InferenceParams:
    """All per-call inference parameters passed from frontend to backend."""

    # ── Prompt construction ────────────────────────────────────────────────
    system_prompt: Optional[str] = None
    # False = disable chain-of-thought (append </think> in assistant opener)
    thinking: bool = False

    # ── Sampling ──────────────────────────────────────────────────────────
    temperature:        Optional[float] = None  # None → ModelConfig.temperature
    top_p:              Optional[float] = None  # None → ModelConfig.top_p
    top_k:              Optional[int]   = None  # None → ModelConfig.top_k
    max_tokens:         Optional[int]   = None  # None → ModelConfig.max_new_tokens
    repetition_penalty: Optional[float] = None  # None → ModelConfig.repetition_penalty
    batch_size:         Optional[int]   = None  # None → ModelConfig.batch_size

    # ── Serialisation ─────────────────────────────────────────────────────

    def as_dict(self) -> dict:
        return {
            "system_prompt":      self.system_prompt,
            "thinking":           self.thinking,
            "temperature":        self.temperature,
            "top_p":              self.top_p,
            "top_k":              self.top_k,
            "max_tokens":         self.max_tokens,
            "repetition_penalty": self.repetition_penalty,
            "batch_size":         self.batch_size,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "InferenceParams":
        return cls(
            system_prompt      = d.get("system_prompt"),
            thinking           = bool(d.get("thinking", False)),
            temperature        = d.get("temperature"),
            top_p              = d.get("top_p"),
            top_k              = d.get("top_k"),
            max_tokens         = d.get("max_tokens"),
            repetition_penalty = d.get("repetition_penalty"),
            batch_size         = d.get("batch_size"),
        )

    @classmethod
    def defaults(cls) -> "InferenceParams":
        """Return params with all None — backend will use its ModelConfig defaults."""
        return cls()
