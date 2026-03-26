"""
Validator — thin wrapper around quality.py for use by StringManager.
"""
from __future__ import annotations
from dataclasses import dataclass

from translator.validation.quality import compute_string_status, validate_tokens


@dataclass
class ValidationResult:
    quality_score: int
    tok_ok: bool
    token_issues: list[str]
    status: str   # pending | translated | needs_review


class Validator:
    """Validates a (original, translation) pair and returns a ValidationResult."""

    def validate(self, original: str, translation: str) -> ValidationResult:
        qs, tok_ok, issues, status = compute_string_status(original, translation)
        return ValidationResult(
            quality_score=qs,
            tok_ok=tok_ok,
            token_issues=issues,
            status=status,
        )
