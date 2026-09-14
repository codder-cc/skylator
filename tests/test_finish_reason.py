"""
Почему генерация кончилась — это знает бэкенд, а не текст.

Обрыв книги на полуслове определялся по тексту: источник длинный, ответ короче 70% и не
кончается знаком конца. 217 таких в корпусе, из них 167 из 179 просмотренных обрывались
посреди слова — то есть признак работал, но оставался догадкой.

Бэкенд всё это время знал точно. llama.cpp возвращает finish_reason «length», mlx_lm
сообщает то же или его видно по числу выданных сегментов против потолка.

Сигнал проведён насквозь: бэкенд запоминает причину, агент помечает обрезанную строку
и не отдаёт её как готовую.
"""
import ast
import io
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _src(rel: str) -> str:
    return io.open(ROOT / rel, encoding="utf-8").read()


def test_llamacpp_records_the_reason():
    s = _src("remote_worker/models/llamacpp_backend.py")
    assert "last_finish_reason" in s
    assert 'get("finish_reason")' in s, "причина берётся из ответа, а не выдумывается"


def test_mlx_records_the_reason():
    s = _src("remote_worker/models/mlx_backend.py")
    assert "last_finish_reason" in s
    assert 'len(segments) >= cap' in s, (
        "где mlx_lm причину не сообщает, упор в потолок виден по числу сегментов")


def test_the_runner_reads_it_and_refuses_the_cut_string():
    s = _src("remote_worker/offline_translate.py")
    assert 'getattr(state.backend, "last_finish_reason", None) == "length"' in s
    assert "_cut_here" in s
    assert 'status = "translated" if (qs > 70 and not _cut_here) else "needs_review"' in s, (
        "обрезанная строка не должна уходить как готовая")


def test_only_the_last_filled_string_is_marked():
    """Потолок рубит последнюю выданную строку. Те, что до неё, целы, и портить их
    статус значило бы отправить на переделку верную работу."""
    s = _src("remote_worker/offline_translate.py")
    assert "j == last_filled" in s


def test_everything_still_parses():
    for rel in ("remote_worker/models/llamacpp_backend.py",
                "remote_worker/models/mlx_backend.py",
                "remote_worker/offline_translate.py"):
        ast.parse(_src(rel))
