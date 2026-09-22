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

    def validate(self, original: str, translation: str,
                 rec_type: str | None = None,
                 field_type: str | None = None) -> ValidationResult:
        """Тип записи необязателен, но два правила читают именно его: точка в конце
        имени и оборот журнала квестов. Без него проверка их не применяет и потому
        рапортует меньше, чем отвергли бы ворота."""
        qs, tok_ok, issues, status = compute_string_status(
            original, translation, None, rec_type, field_type)
        return ValidationResult(
            quality_score=qs,
            tok_ok=tok_ok,
            token_issues=issues,
            status=status,
        )
