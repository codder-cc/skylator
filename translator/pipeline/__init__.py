"""
Публичный API перевода: translate_batch() и get_mod_context().
Им пользуются scripts/esp_engine.py, scripts/translate_mcm.py и SWF-помощник в
apply_pipeline.py.

Почему это живёт в __init__.py пакета, а не в translator/pipeline.py.
Долгое время в дереве лежали одновременно модуль translator/pipeline.py с этими
функциями и пакет translator/pipeline/ с пустым __init__.py. Python при импорте
выбирает пакет, так что `from translator.pipeline import translate_batch` падал с
ImportError — а вызывающие ловили его широким except и молча возвращали исходный
английский текст. Так MCM-обёртка писала английский в *_russian.txt, а контекст
мода для SWF всегда был пустым; снаружи всё выглядело как успешный проход.
Модуль-двойник удалён, его содержимое перенесено сюда.

Импорты тяжёлых зависимостей (EnsemblePipeline, ContextBuilder) остаются внутри
функций: пакет импортируют ради apply_pipeline / translate_pipeline, и грузить
модели при `import translator.pipeline.apply_pipeline` нельзя.
"""

from __future__ import annotations
import logging
from pathlib import Path

log = logging.getLogger(__name__)

_pipeline = None


def _get_pipeline():
    global _pipeline
    if _pipeline is None:
        from translator.ensemble.pipeline import EnsemblePipeline
        _pipeline = EnsemblePipeline()
    return _pipeline


def translate_batch(texts: list[str], context: str = "",
                    params=None, progress_cb=None, force: bool = False) -> list[str]:
    """
    Translate a batch of strings using the ensemble pipeline.
    Returns list of same length. Never raises — returns originals on failure.
    progress_cb(done, total) called after each inner batch completes.
    force=True bypasses the in-memory translation cache.
    params: InferenceParams with per-call overrides (None = use model config defaults).
    """
    if not texts:
        return []
    try:
        return _get_pipeline().translate(texts, context=context, params=params,
                                         progress_cb=progress_cb, force=force)
    except Exception:
        log.exception("translate_batch failed")
        return list(texts)


def get_mod_context(mod_folder) -> str:
    """
    Return a short description context string for a given mod folder.
    Returns "" if Nexus API is unavailable or not configured.
    """
    try:
        from translator.context import ContextBuilder
        builder = ContextBuilder()
        return builder.get_mod_context(Path(mod_folder))
    except Exception as exc:
        log.warning(f"get_mod_context failed: {exc}")
        return ""
