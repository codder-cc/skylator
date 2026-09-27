"""
OfflineTranslateRunner — autonomous, durable translation on the remote worker.

Processes a work package from the host without requiring constant connectivity.
Every produced translation is written to the agent's durable ResultStore the instant
inference returns; delivery to the host is handled separately by the agent's deliver
loop. Production never depends on the host being reachable, and a crash/relaunch resumes
from the durable manifest with no lost or repeated work.
"""
from __future__ import annotations
import asyncio
import logging
import time
import re
from pathlib import Path

log = logging.getLogger(__name__)


def _code_revision() -> str:
    """Полный git HEAD кода агента; "" — если узнать нельзя (не git, нет git).

    Берётся ОДИН раз, при импорте, а не при первой трассе. OTA делает git pull и только
    потом перезапускает процесс: ревизия, прочитанная лениво между этими шагами, была бы
    ревизией нового кода на вызовах, которые делает ещё старый.
    """
    try:
        import subprocess
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(Path(__file__).parent),
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:                                              # noqa: BLE001
        return ""


CODE_REV = _code_revision()

# Token patterns that must be preserved verbatim
_TOKEN_RE = re.compile(r"<[^>]+>|%\d|⟨NL⟩|\[PlayerName\]|\{T\d+\}")
_CYRILLIC_RE = re.compile(r"[а-яА-ЯёЁ]")


def _inline_quality_score(original: str, translation: str) -> int:
    """
    Lightweight quality score (0-100) for use without the host esp_engine.

    Checks:
    - Cyrillic presence when Russian is expected
    - Token preservation (<Alias=...>, %1, ⟨NL⟩, etc.)
    - Output == input (untranslated)
    - Empty translation
    """
    if not translation:
        return 0
    score = 100

    # Untranslated — output is identical to input
    if translation.strip() == original.strip():
        score -= 40

    # No Cyrillic in output when we expect Russian
    if original and not _CYRILLIC_RE.search(translation):
        # Latin-only is OK for very short strings / numbers / tokens
        if len(original.split()) > 2:
            score -= 30

    # Missing tokens
    orig_tokens = set(_TOKEN_RE.findall(original))
    for tok in orig_tokens:
        if tok not in translation:
            score -= 15

    return max(0, min(100, score))


MAX_PASSES = 2   # initial pass + one retry for strings that failed inference

# Batching is a throughput decision, not a constant. Every call pays a fixed cost —
# system prompt, instructions, glossary, mod context — that dwarfs a short string:
# measured on the live backlog, a batch of four 20-character item names carried 42
# tokens of text inside a 1519-token prompt, 2.8% payload. Filling each call to a
# text budget instead of a fixed count amortises that cost over far more strings
# when they are short, while long prose still goes one or two at a time.
_BATCH_PAYLOAD_CHARS = 2000   # target characters of source text per call
_BATCH_MAX_ITEMS     = 32     # numbered-output parsing stays reliable to about here
# Below this, a string is a name or a UI label: the mod description explains nothing
# about "Iron Sword" and costs ~680 characters of prompt on every call.
_SHORT_STRING_CHARS  = 28
# How much of the string in flight to show the operator.
_PREVIEW_CHARS       = 160


# What each ESP record type actually is, in the words a translator would use. One line of
# prompt that tells the model whether it is naming an object or writing a spoken line — the
# register and grammar differ, and without it a batch of item names reads to the model
# exactly like a batch of dialogue. The type was already stored per string; it simply was
# never sent to the agent.
_REC_TYPE_HINT = {
    "WEAP": "weapon names", "ARMO": "armour names", "ALCH": "potion names",
    "INGR": "ingredient names", "MISC": "item names", "BOOK": "book titles or text",
    "AMMO": "ammunition names", "KEYM": "key names", "SLGM": "soul gem names",
    "NPC_": "character names", "FACT": "faction names", "RACE": "race names",
    "CELL": "place names", "WRLD": "region names", "LCTN": "location names",
    "QUST": "quest names", "INFO": "spoken dialogue", "DIAL": "dialogue topics",
    "MGEF": "magic effect descriptions", "SPEL": "spell names",
    "PERK": "perk names or descriptions", "MESG": "on-screen messages",
    "ACTI": "activator names", "CONT": "container names", "DOOR": "door names",
    "FLOR": "plant names", "FURN": "furniture names", "SHOU": "shout names",
}


# Уточнение по полю записи. Одна запись несёт строки разного рода: у книги FULL — это
# заголовок, DESC — сам текст; у квеста FULL — название, NNAM — цель в журнале. Тип поля
# хост всегда держал в ключе строки, но в пакет не клал, и подсказка строилась по одному
# rec_type — «book titles or text» на заголовок и на трёхстраничный текст одинаково.
_FIELD_TYPE_HINT = {
    ("BOOK", "FULL"): "book titles",            ("BOOK", "DESC"): "book text",
    ("INFO", "NAM1"): "spoken dialogue",        ("INFO", "RNAM"): "the player's dialogue choices",
    ("MGEF", "FULL"): "magic effect names",     ("MGEF", "DNAM"): "magic effect descriptions",
    ("PERK", "FULL"): "perk names",             ("PERK", "DESC"): "perk descriptions",
    ("QUST", "FULL"): "quest names",            ("QUST", "NNAM"): "quest objectives",
    ("QUST", "CNAM"): "quest journal entries",
    ("WEAP", "DESC"): "weapon descriptions",    ("ARMO", "DESC"): "armour descriptions",
    ("SPEL", "DESC"): "spell descriptions",     ("MESG", "ITXT"): "message box button labels",
    ("LSCR", "DESC"): "loading screen tips",
}


def rec_type_hint(batch: list) -> str:
    """One line describing what this batch is, when the batch is homogeneous.

    A mixed batch gets nothing: a wrong hint is worse than no hint. Когда у всех строк
    один и тот же известный field_type, подсказка точнее: «book text», а не «book
    titles or text». Смешанные или пустые поля оставляют общую подсказку по записи.
    """
    kinds = {(b.get("rec_type") or "").strip() for b in batch}
    kinds.discard("")
    if len(kinds) != 1:
        return ""
    rec = next(iter(kinds))
    fields = {(b.get("field_type") or "").strip() for b in batch
              if (b.get("rec_type") or "").strip() == rec}
    fields.discard("")
    if len(fields) == 1:
        precise = _FIELD_TYPE_HINT.get((rec, next(iter(fields))))
        if precise:
            return precise
    return _REC_TYPE_HINT.get(rec, "")


# Сколько контекста на строку помещается в один промпт. Раньше здесь были молчаливые
# обрезания: из блоков имён брались первые четыре различных, из разговора — первые
# шесть реплик, а всё, что дальше, выбрасывалось. Пятая строка батча уходила в модель
# без своих имён, и об этом не знал никто. Теперь предел — повод РАЗБИТЬ батч: строка,
# чей контекст не влезает, идёт в следующий вызов со всем своим контекстом. Одиночная
# строка получает свой контекст целиком, каким бы длинным он ни был.
_ENTITY_BLOCKS_PER_BATCH = 6
_TALK_LINES_PER_BATCH    = 12


def _talk_lines(b: dict) -> list[str]:
    return [ln.strip() for ln in (b.get("talk") or "").splitlines() if ln.strip()]


def batch_key(b: dict, split_mods: bool) -> tuple:
    """Что должно совпадать у строк одного промпта.

    Промпт на батч один, и всё, что в нём сказано про «эти строки», сказано про все
    сразу: карточка говорящего, примеры стиля, подсказка о типе записи, описание мода.
    Смешанный батч получал это от ПЕРВОЙ строки — так книга шла с примерами стиля
    реплик, потому что первой в батче оказалась реплика. Хост раскладывает строки по
    типу, но манифест читается ORDER BY string_id, и порядок хоста не доживает.
    """
    return ((b.get("mod_name") or "") if split_mods else "",
            b.get("speaker") or "", b.get("style") or "",
            (b.get("rec_type") or "").strip(), (b.get("field_type") or "").strip())


def group_pending(pending: list, split_mods: bool) -> list:
    """Переставить работу так, чтобы строки с одним batch_key шли подряд.

    Порядок групп — по первому появлению, внутри группы — прежний. Иначе ведущий
    отрезок одного ключа, которым режется батч, дробил бы перемешанный манифест на
    батчи из одной строки.
    """
    groups: dict = {}
    for b in pending:
        groups.setdefault(batch_key(b, split_mods), []).append(b)
    return [b for g in groups.values() for b in g]


def fit_context(batch: list) -> list:
    """Укоротить батч так, чтобы контекст КАЖДОЙ его строки поместился в промпт."""
    ents: set = set()
    talk = 0
    for n, b in enumerate(batch):
        e = b.get("entities") or ""
        new_ents = ents | ({e} if e else set())
        new_talk = talk + len(_talk_lines(b))
        if n and (len(new_ents) > _ENTITY_BLOCKS_PER_BATCH
                  or new_talk > _TALK_LINES_PER_BATCH):
            return batch[:n]
        ents, talk = new_ents, new_talk
    return batch


def _tm_block(batch: list, tm_pairs: dict) -> str:
    """Translation memory для батча — без ответа, который оценивается вслепую.

    Строка с `rival` переводится вслепую, чтобы судья сравнил новый ответ с хранимым.
    Но хранимый перевод сам лежит в памяти переводов: «Whiterun → <хранимое>» попадал
    в промпт, и модель переписывала соперника вместо того, чтобы перевести. Сравнение
    выходило с самим собой. Поэтому из памяти для ВСЕГО батча убирается запись, чей
    ключ — исходник такой строки, и любая запись со значением, равным её сопернику.
    """
    if not tm_pairs:
        return ""
    blind_keys = {(b.get("original") or "").strip() for b in batch if (b.get("rival") or "").strip()}
    blind_vals = {(b.get("rival") or "").strip() for b in batch if (b.get("rival") or "").strip()}
    tm_lines: list[str] = []
    for b in batch:
        for word in (b.get("original") or "").split():
            if word in tm_pairs and len(tm_lines) < 8:
                value = tm_pairs[word]
                if word.strip() in blind_keys or (str(value or "").strip() in blind_vals):
                    continue
                entry = f"  {word} → {value}"
                if entry not in tm_lines:
                    tm_lines.append(entry)
    return ("Translation memory:\n" + "\n".join(tm_lines) + "\n") if tm_lines else ""


def build_batch_context(batch: list, *, context: str = "", mods_context: dict | None = None,
                        tm_pairs: dict | None = None) -> str:
    """Весь контекст промпта для ЭТИХ строк, пронумерованный по их месту в батче.

    Одна функция на батч и на одиночный повтор. Раньше повтор брал готовый контекст
    всего батча: строка становилась номером 1, а её разговор оставался под номером 2,
    и рядом стоял разговор первой строки — модель переводила реплику по чужой беседе.
    """
    mods_context = mods_context or {}
    originals = [b.get("original") or "" for b in batch]
    # Per-mod context for multi-mod packages. Skipped when every string in the
    # batch is a short name or label — a mod description says nothing useful
    # about "Iron Sword" and is pure prompt cost on every call. Terminology and
    # the translation memory stay: those are exactly what short names need.
    all_short = all(len(o) <= _SHORT_STRING_CHARS for o in originals)
    if mods_context and not all_short:
        batch_mod = (batch[0].get("mod_name") or "") if batch else ""
        batch_ctx = mods_context.get(batch_mod) or context
    elif all_short:
        batch_ctx = ""
    else:
        batch_ctx = context
    hint = rec_type_hint(batch)
    if hint:
        batch_ctx = ("These strings are " + hint + ".\n" + batch_ctx).strip()
    # Карточка говорящего идёт ПЕРВОЙ и не отбрасывается на коротких строках:
    # описание мода про «Iron Sword» бесполезно, а пол говорящего — нет, и
    # именно на коротких репликах род первого лица и выбирался наугад.
    # Батч однороден по говорящему и стилю (batch_key), поэтому первая строка
    # говорит за всех — это уже не допущение, а свойство сборки батча.
    speaker_block = (batch[0].get("speaker") or "") if batch else ""
    if speaker_block:
        batch_ctx = (speaker_block + "\n" + batch_ctx).strip()
    style_block = (batch[0].get("style") or "") if batch else ""
    if style_block:
        batch_ctx = (batch_ctx + "\n" + style_block).strip()
    # Имена, которые игра уже назвала. Складываются по всему батчу: строки
    # разные, а промпт один, и подсказка нужна каждой из них. Все — fit_context
    # уже укоротил батч так, чтобы они поместились.
    ent_lines, ent_seen = [], set()
    for b in batch:
        line = b.get("entities") or ""
        if line and line not in ent_seen:
            ent_seen.add(line)
            ent_lines.append(line)
    if ent_lines:
        batch_ctx = (batch_ctx + "\n" + "\n".join(ent_lines)).strip()
    # Разговор вокруг строки. У каждой строки он свой, поэтому каждая
    # реплика помечена номером той строки, к которой относится: промпт на
    # батч один, и без номера соседи приняли бы чужую беседу за свою.
    # Без этого модель переводила фразу как отдельную — отсюда и «Ты
    # грубиян» в обращении к женщине, и кальки вроде «вино растрачивается
    # на твой язык»: сказано-то было в перепалке.
    talk_lines = []
    for n, b in enumerate(batch, 1):
        for line in _talk_lines(b):
            talk_lines.append(f"  ({n}) {line}")
    if talk_lines:
        batch_ctx = (batch_ctx + "\nConversation around these lines:\n"
                     + "\n".join(talk_lines)).strip()
    tm_block = _tm_block(batch, tm_pairs or {})
    return (batch_ctx + "\n" + tm_block).strip() if tm_block else batch_ctx


# Ответ судьи — одна буква. Всё прочее — не ответ. Раньше бралась первая A или B где
# угодно в тексте: «Both translations are equally good.» читалось как B (первая буква
# «B» в «Both»), и ничья превращалась в победу нового перевода.
_JUDGE_ANSWER_RE = re.compile(r"^\s*([AB])\s*[.!]?\s*$", re.IGNORECASE)


def parse_judge_answer(raw: str) -> str:
    """'A' | 'B' | '?' — строго: ответ целиком, допускается точка или «!» в конце."""
    m = _JUDGE_ANSWER_RE.match(raw or "")
    return m.group(1).upper() if m else "?"


def _producer_model(state) -> str | None:
    """Имя модели, которая сейчас генерирует, — чтобы записать его рядом со строкой."""
    label = getattr(state, "model_label", None)
    if label:
        return str(label)
    label = getattr(getattr(state, "backend", None), "_label", None)
    return str(label) if label else None


def _line_reasons(translations: list, reason) -> list:
    """Причина конца генерации для каждой строки одного нумерованного ответа.

    Потолок рубит последнюю выданную строку; те, что до неё, закончены. Если бэкенд
    причины не знает (generate() без потока), не знаем и мы — None, не «stop».
    """
    last_filled = max((k for k, t in enumerate(translations) if (t or "").strip()),
                      default=-1)
    out = []
    for k in range(len(translations)):
        if reason is None:
            out.append(None)
        elif reason == "length" and k == last_filled:
            out.append("length")
        else:
            out.append("stop")
    return out


# How often a held runner looks up to see whether its window has opened. A minute is
# well under the resolution of a schedule written in whole minutes, and costs nothing.
_SCHEDULE_POLL_SEC = 30.0


def _schedule_allows(state) -> bool:
    """May this machine translate right now? Failure to tell means yes.

    A machine that stops because the schedule module threw would look identical to one
    that finished its work, and nothing would say why.
    """
    try:
        from work_schedule import is_working
        return is_working(getattr(state, "schedule", None))
    except Exception:
        return True


def plan_batch(pending: list, start: int, cap: int) -> int:
    """How many of `pending` starting at `start` to send in one call.

    Returns a count >= 1. Greedy: keep adding while the accumulated source text fits
    the payload budget and the item cap. A single string longer than the budget still
    goes alone rather than being dropped.
    """
    n = 0
    chars = 0
    limit = max(1, min(cap, _BATCH_MAX_ITEMS))
    while start + n < len(pending) and n < limit:
        length = len(pending[start + n].get("original") or "")
        if n and chars + length > _BATCH_PAYLOAD_CHARS:
            break
        chars += length
        n += 1
    return max(1, n)


def _as_params(infer_params) -> dict:
    """Параметры инференса словарём.

    `infer_params` здесь — объект InferenceParams, а не словарь, и `dict(...)` на нём
    падает. Ошибка стоила дорого и молча: она была в самом судье, поэтому КАЖДЫЙ его
    вызов кидал исключение, перехватывался как «не знаю» и оставлял хранимый текст.
    Замер показал «судья выбрал хранимое 105 раз из 105», и это выглядело осмысленным
    выводом о качестве — а судья не судил ни разу.
    """
    if infer_params is None:
        return {}
    if isinstance(infer_params, dict):
        return dict(infer_params)
    for name in ("as_dict", "model_dump", "dict"):
        fn = getattr(infer_params, name, None)
        if callable(fn):
            try:
                got = fn()
                if isinstance(got, dict):
                    return dict(got)
            except Exception:                                      # noqa: BLE001
                pass
    return {}


def _params_with(infer_params, **changes):
    """Копия параметров инференса с заменой полей — ОБЪЕКТОМ, а не словарём.

    Бэкенд читает поля как атрибуты (`p.max_tokens`). Судья получал словарь, на каждом
    вызове падал с AttributeError, и это перехватывалось как «не уверен»: первые 31
    вердикт прогона — все «не уверен». Судья на агенте не судил ни разу, а замер на
    M5 проходил, потому что шёл через /infer, где параметры собираются правильно.
    """
    from models.inference_params import InferenceParams
    return InferenceParams.from_dict({**_as_params(infer_params), **changes})


class OfflineTranslateRunner:
    """
    Store-driven autonomous translation.

    Reads its work list from the agent's durable ResultStore manifest (NOT from an
    in-memory list), translates batch-by-batch, and writes every produced translation
    to the ResultStore **immediately** — before any network delivery. Delivery to the
    host is a separate concern (the agent's deliver loop), fully decoupled from
    production. This is what makes a week-long run unloseable:

      * crash mid-run  → at most one in-flight string is lost; the rest are on disk
      * relaunch       → run() resumes from manifest rows still marked done=0
      * host offline   → production continues regardless; results queue locally

    Parameters
    ----------
    store : ResultStore
        The agent's durable database.
    assignment_id : str
        Identifies the work parcel (== the host's offline_job_id).
    meta : dict
        Everything from the dispatch chunk EXCEPT the strings list: context,
        mods_context, src_lang, tgt_lang, params, terminology, preserve_tokens, tm_pairs.
    """

    def __init__(self, store, assignment_id: str, meta: dict) -> None:
        self._store      = store
        self._aid        = assignment_id
        self._meta       = meta or {}
        self.done_count  = 0
        self.current_text: str = ""
        self._stop       = False
        # Held outside working hours — tracked only so the log says so once, not every
        # thirty seconds for the eight hours a night window is closed.
        self._off_hours  = False
        # Промпт целиком в трассе — только для экспериментов: пакет несёт trace_full.
        # Хэш пишется всегда.
        self._trace_full = bool(self._meta.get("trace_full"))

    def cancel(self) -> None:
        self._stop = True

    async def _infer_call(self, state, loop, prompt: str, params, *,
                          kind: str = "translate", string_ids=()):
        """Один вызов модели: (текст, причина конца генерации, trace_id).

        Причина читается В ТОМ ЖЕ потоке и сразу после вызова, а не потом из
        `backend.last_finish_reason`. Раньше её читали после всего батча: при одиночных
        повторах доживала причина последнего вызова, и обрезанная первая строка
        становилась translated, потому что вторая закончилась штатно.

        Так же, в том же потоке, читается `backend.last_call` — что фактически ушло в
        mlx_lm — и пишется трассой в хранилище. Аудит не мог доказать, что видела
        модель: всё, что оставалось, — лог мастера «карточка приложена». Бэкенд без
        `last_call` (llama.cpp, заглушки) даёт трассу с запрошенными параметрами под
        ключом "requested": честно, что это не граница библиотеки.
        """
        backend = state.backend
        _self = self

        def call():
            t0 = time.monotonic()
            try:
                raw = backend._infer(prompt, params=params, stop_check=lambda: _self._stop)
            except Exception as exc:                               # noqa: BLE001
                return None, None, None, time.monotonic() - t0, exc
            return (raw, getattr(backend, "last_finish_reason", None),
                    getattr(backend, "last_call", None), time.monotonic() - t0, None)

        raw, reason, info, seconds, error = await loop.run_in_executor(None, call)
        trace_id = self._trace(state, kind, string_ids, prompt, params, info,
                               reason if error is None else "error", seconds)
        if error is not None:
            raise error
        return raw, reason, trace_id

    def _trace(self, state, kind, string_ids, prompt, params, info, reason, seconds):
        """Записать трассу одного вызова. Сбой записи — не сбой перевода."""
        info = info if isinstance(info, dict) else {}
        store = getattr(self, "_store", None)
        if store is None or not hasattr(store, "write_trace"):
            return None
        aid = getattr(self, "_aid", "") or ""
        try:
            return store.write_trace(
                assignment_id = aid,
                kind          = kind,
                string_ids    = list(string_ids or []),
                prompt        = prompt,
                params        = info.get("params") or {"requested": _as_params(params)},
                finish_reason = info.get("finish_reason", reason) if reason != "error" else "error",
                tokens_in     = info.get("tokens_in"),
                tokens_out    = info.get("tokens_out"),
                seconds       = info.get("seconds", round(seconds, 4)),
                model         = _producer_model(state),
                code_rev      = CODE_REV,
                keep_prompt   = bool(getattr(self, "_trace_full", False)),
            )
        except Exception as exc:                                   # noqa: BLE001
            log.warning("OfflineTranslateRunner[%s]: trace not recorded: %s", aid[:8], exc)
            return None

    async def _judge_detail(self, state, loop, source: str, stored: str, fresh: str,
                            infer_params, names: str = "", *, string_ids=(),
                            traces: list | None = None) -> str:
        """Вердикт судьи с отличием невалидного ответа: fresh | stored | unsure | invalid.

        Спрашивается ДВАЖДЫ, с перестановкой вариантов. Модель, выбирающая по месту,
        а не по существу, ответит одной и той же буквой и будет поймана; на замере из
        семи пар так поймалась одна. Несогласие двух ответов — это «не знаю», и тогда
        остаётся хранимый текст: менять его без уверенности не на что.

        «invalid» — модель ответила не буквой («Both translations are equally good.»,
        «A or B», мусор). Для решения это то же «не знаю», но записывается отдельно:
        сломанный формат судьи и честная неуверенность — разные неисправности, и
        считать их вместе значит не видеть ни одну.

        `traces`, если передан, получает trace_id обоих вызовов — в порядке вызова.
        """
        from prompt.builder import build_judge_prompt

        params = _params_with(infer_params, temperature=0.0, top_k=1, max_tokens=8,
                              thinking=False)

        async def once(a: str, b: str) -> str:
            raw, _reason, tid = await self._infer_call(
                state, loop, build_judge_prompt(source, a, b, names), params,
                kind="judge", string_ids=string_ids)
            if traces is not None and tid is not None:
                traces.append(tid)
            return parse_judge_answer(raw)

        first = await once(stored, fresh)     # fresh побеждает, когда ответ B
        second = await once(fresh, stored)    # fresh побеждает, когда ответ A
        if first == "?" or second == "?":
            return "invalid"
        if first == "B" and second == "A":
            return "fresh"
        if first == "A" and second == "B":
            return "stored"
        return "unsure"

    async def _judge(self, state, loop, source: str, stored: str, fresh: str,
                     infer_params, names: str = "", *, string_ids=()) -> str:
        """Какой из двух переводов живее: 'fresh' | 'stored' | 'unsure'.

        Невалидный ответ судьи здесь — «unsure»: для решения это не знание. Отличить
        его можно через _judge_detail, которым пользуется запись вердикта.
        """
        v = await self._judge_detail(state, loop, source, stored, fresh, infer_params, names,
                                     string_ids=string_ids)
        return "unsure" if v == "invalid" else v

    async def _retranslate_singly(self, batch, state, loop, infer_params, *,
                                  src_lang, tgt_lang, context, mods_context, tm_pairs,
                                  system_prompt, thinking, terminology, preserve_tokens,
                                  reviewing):
        """Перевести каждую строку батча отдельным запросом: (переводы, причины, трассы).

        Нужно ровно там, где нумерованный ответ вернулся короче батча: разложить его по
        местам уже нельзя, потому что номера могли съехать. По одной строке номер
        единственный, и съезжать нечему.

        Контекст собирается заново ДЛЯ ЭТОЙ строки: её разговор под номером (1), её
        имена, её память переводов. Раньше повтор получал контекст всего батча, и
        строка №2, ставшая единственной, видела свою беседу под номером 2, а под
        номером 1 — беседу соседки.

        Стоит это одного вызова на строку вместо одного на батч — дорого, но случается
        редко, а альтернатива это молча записанный перевод соседней строки, которого не
        видит ни одно правило.
        """
        from prompt.builder import build_prompt
        from prompt.parser import parse_numbered_output

        out: list = []
        reasons: list = []
        traces: list = []
        for b in batch:
            if self._stop:
                out.extend([""] * (len(batch) - len(out)))
                reasons.extend([None] * (len(batch) - len(reasons)))
                traces.extend([None] * (len(batch) - len(traces)))
                break
            prompt = build_prompt(
                texts=[b.get("original") or ""], src_lang=src_lang, tgt_lang=tgt_lang,
                context=build_batch_context([b], context=context, mods_context=mods_context,
                                            tm_pairs=tm_pairs),
                system_prompt=system_prompt, thinking=thinking, terminology=terminology,
                preserve_tokens=preserve_tokens,
                current=[b.get("current") or ""] if reviewing else None,
                terms=[b.get("req_terms") or ""] if reviewing else None,
            )
            try:
                raw, reason, tid = await self._infer_call(state, loop, prompt, infer_params,
                                                          kind="retry",
                                                          string_ids=[b.get("string_id")])
            except Exception as exc:
                log.error("OfflineTranslateRunner[%s]: одиночный перевод не удался: %s",
                          self._aid[:8], exc)
                raw, reason, tid = "", None, None
            got = parse_numbered_output(raw or "", 1)
            out.append(got[0] if got else "")
            reasons.append(reason)
            traces.append(tid)
        return out, reasons, traces

    async def run(self, state, loop: asyncio.AbstractEventLoop) -> None:
        """Produce translations for all pending manifest items, writing each durably.

        Retries inference failures up to MAX_PASSES; whatever still fails is left
        done=0 (the host keeps those strings pending → re-dispatchable later).
        """
        from prompt.builder  import build_prompt
        from prompt.parser   import parse_numbered_output
        from models.inference_params import InferenceParams

        meta            = self._meta
        context         = meta.get("context") or ""
        # Судья включается пакетом. Он стоит двух коротких запросов на строку и
        # нужен не везде: у имени предмета спорить не о чем.
        judging         = bool(meta.get("judge"))
        # Сколько раз спросить одну и ту же строку. Всё, что подаётся в ПРОМПТ,
        # на замерах делало хуже: контекст 46→43%, примеры стиля 25→20%. А вот
        # разнообразие ответов даёт запас — лучший из четырёх совпадает с
        # официальным переводом в полтора раза чаще одиночного. Выбирать его
        # модель умеет: генерировать разнообразие и оценивать его — разные задачи.
        candidates      = max(int(meta.get("candidates") or 1), 1)
        cand_temp       = float(meta.get("candidate_temp") or 0.7)
        mods_context: dict = meta.get("mods_context") or {}
        src_lang        = meta.get("src_lang") or "English"
        tgt_lang        = meta.get("tgt_lang") or "Russian"
        raw_params      = meta.get("params") or {}
        terminology     = meta.get("terminology") or ""
        preserve_tokens = meta.get("preserve_tokens") or []
        tm_pairs: dict  = meta.get("tm_pairs") or {}
        thinking        = raw_params.get("thinking", False)
        system_prompt   = raw_params.get("system_prompt")
        # The configured size is now an upper bound; plan_batch decides the real one from
        # the text at hand. Was a hard 4 for everything, short names included.
        batch_size_cap  = int(raw_params.get("batch_size") or _BATCH_MAX_ITEMS)
        infer_params    = InferenceParams.from_dict(raw_params)

        passes = 0
        while not self._stop:
            pending = self._store.pending_items(self._aid)
            if not pending:
                break
            # Строки с одним batch_key — подряд, чтобы каждый батч был однороден.
            pending = group_pending(pending, bool(mods_context))
            passes += 1
            if passes > MAX_PASSES:
                log.warning("OfflineTranslateRunner[%s]: giving up on %d strings after %d passes",
                            self._aid[:8], len(pending), MAX_PASSES)
                break

            log.info("OfflineTranslateRunner[%s]: pass %d, %d pending, batch cap=%d",
                     self._aid[:8], passes, len(pending), batch_size_cap)

            i = 0
            while i < len(pending) and not self._stop:
                # Backpressure: pause on disk-full or while the model is not loaded.
                if self._store.disk_full:
                    await asyncio.sleep(5.0)
                    continue
                if state.backend is None:
                    await asyncio.sleep(2.0)
                    continue
                # Outside its working hours the machine stops taking new batches. The gate
                # sits here, between batches, so whatever was in flight finishes and lands
                # in the store: pausing costs at most one batch of latency and never a
                # translation. Resuming needs no state — pending_items() picks up the rest.
                if not _schedule_allows(state):
                    if not self._off_hours:
                        self._off_hours = True
                        from work_schedule import describe
                        log.info("OfflineTranslateRunner[%s]: outside working hours (%s) — "
                                 "holding %d strings", self._aid[:8],
                                 describe(getattr(state, "schedule", None)), len(pending) - i)
                    await asyncio.sleep(_SCHEDULE_POLL_SEC)
                    continue
                if self._off_hours:
                    self._off_hours = False
                    log.info("OfflineTranslateRunner[%s]: back inside working hours, resuming",
                             self._aid[:8])

                batch     = pending[i: i + plan_batch(pending, i, batch_size_cap)]
                # Батч — ведущий отрезок строк с одним batch_key: один мод (from 2c9c1e4,
                # когда в пакете несколько модов), один говорящий, один стиль, один тип
                # записи и поля. Карточка персонажа описывает ОДНОГО, примеры стиля — ОДИН
                # род записей, и батч, смешавший два, получил бы их от первой строки.
                # group_pending выше поставил такие строки подряд, так что обрезание ничего
                # не дробит без нужды.
                if batch:
                    lead_key = batch_key(batch[0], bool(mods_context))
                    end = 1
                    while end < len(batch) and batch_key(batch[end], bool(mods_context)) == lead_key:
                        end += 1
                    batch = batch[:end]
                # И так, чтобы контекст каждой строки поместился целиком (fit_context).
                batch = fit_context(batch)
                originals = [b.get("original") or "" for b in batch]
                # A review package carries the translation already stored. Present it and
                # the model corrects rather than translates; absent, nothing changes.
                stored = [b.get("current") or "" for b in batch]
                reviewing = any(stored)
                # The rendering each line must use for the term it got wrong. Present
                # only in a terminology-fix package, and it changes the prompt from
                # "review this" to "correct this one word".
                req_terms = [b.get("req_terms") or "" for b in batch]

                full_context = build_batch_context(batch, context=context,
                                                   mods_context=mods_context,
                                                   tm_pairs=tm_pairs)

                prompt = build_prompt(
                    texts           = originals,
                    src_lang        = src_lang,
                    tgt_lang        = tgt_lang,
                    context         = full_context,
                    system_prompt   = system_prompt,
                    thinking        = thinking,
                    terminology     = terminology,
                    preserve_tokens = preserve_tokens,
                    current         = stored if reviewing else None,
                    terms           = req_terms if reviewing else None,
                )
                # A preview for the UI, not data. Untruncated, a single book chapter put
                # 12.6 KB on every heartbeat and made up 88% of the /api/workers payload —
                # sent again on every UI poll and every real-time push, twice per worker.
                self.current_text = (originals[0][:_PREVIEW_CHARS] if originals else "")

                if self._stop:
                    break

                # Модель, которая произвела этот батч, — записывается рядом с каждой
                # строкой. Берётся здесь, у вызова: модель можно сменить посреди пакета.
                producer = _producer_model(state)
                batch_ids = [b.get("string_id") for b in batch]
                _t0 = time.monotonic()
                try:
                    raw, reason, batch_trace = await self._infer_call(
                        state, loop, prompt, infer_params, kind="translate",
                        string_ids=batch_ids)
                except Exception as exc:
                    log.error("OfflineTranslateRunner[%s]: inference error: %s", self._aid[:8], exc)
                    raw, reason, batch_trace = "", None, None
                # An offline package can be the ONLY thing an agent does for days, so without
                # this its tok/s never updates and the master keeps splitting the next
                # campaign evenly instead of by real speed.
                try:
                    from remote_server import _record_throughput
                    _record_throughput(state, raw or "", time.monotonic() - _t0)
                except Exception:
                    pass

                translations = parse_numbered_output(raw or "", len(batch))
                # Почему кончилась генерация — ДЛЯ КАЖДОЙ строки, в момент её получения.
                reasons = _line_reasons(translations, reason)
                # Какой вызов дал каждую строку — чтобы её трасса была трассой ЕЁ вызова.
                trace_ids = [batch_trace] * len(batch)

                # Нумерованный список, вернувшийся короче, нельзя раскладывать по местам.
                #
                # Разборщик сопоставляет ответ с исходником по НАПЕЧАТАННОМУ номеру. Если
                # модель пропустила пункт и перенумеровала остаток, каждый ответ садится на
                # соседнюю строку — а пустым оказывается только последнее место. Найдено в
                # корпусе на пяти школах магии:
                #
                #     Alteration  → «Призыв»          ← ответ для Conjuration
                #     Conjuration → «Разрушение»      ← ответ для Destruction
                #     Destruction → «Иллюзия»         ← ответ для Illusion
                #     Illusion    → «Восстановление»  ← ответ для Restoration
                #
                # Каждая из них — нормальное русское слово на своём месте, токены целы,
                # счёт 100. Ни одно правило этого не увидит и увидеть не может: строка
                # неверна только относительно СОСЕДА по батчу.
                #
                # Поэтому недостача в ответе означает, что доверять нельзя всему батчу, а
                # не одному месту. Батч переводится заново по одной строке: там номер
                # всегда единственный и сдвинуться некуда.
                if len(batch) > 1 and any(not (t or "").strip() for t in translations):
                    log.warning(
                        "OfflineTranslateRunner[%s]: ответ короче батча (%d из %d) — "
                        "нумерация ненадёжна, переперевод по одной",
                        self._aid[:8], sum(1 for t in translations if (t or "").strip()),
                        len(batch))
                    translations, reasons, trace_ids = await self._retranslate_singly(
                        batch, state, loop, infer_params,
                        src_lang=src_lang, tgt_lang=tgt_lang, context=context,
                        mods_context=mods_context, tm_pairs=tm_pairs,
                        system_prompt=system_prompt, thinking=thinking,
                        terminology=terminology, preserve_tokens=preserve_tokens,
                        reviewing=reviewing)

                cut = [k for k, r in enumerate(reasons) if r == "length"]
                if cut:
                    log.warning("OfflineTranslateRunner[%s]: генерация упёрлась в потолок — "
                                "обрезаны строки %s из %d", self._aid[:8],
                                ", ".join(str(k + 1) for k in cut), len(batch))

                # Несколько кандидатов на одну строку, выбор — судьёй. Первый ответ
                # уже получен выше при своей температуре; остальные берутся с разбросом,
                # иначе они повторят его слово в слово и выбирать будет не из чего.
                if candidates > 1 and not reviewing:
                    pools: list[tuple[list, list, list]] = [
                        (list(translations), list(reasons), list(trace_ids))]
                    _base = _as_params(infer_params)
                    hot = _params_with(infer_params, top_k=40, temperature=max(
                        cand_temp, float(_base.get("temperature") or 0)))
                    for _ in range(candidates - 1):
                        if self._stop:
                            break
                        try:
                            raw2, reason2, tid2 = await self._infer_call(
                                state, loop, prompt, hot, kind="candidate",
                                string_ids=batch_ids)
                        except Exception as exc:                   # noqa: BLE001
                            log.warning("candidate pass failed: %s", exc)
                            break
                        got2 = parse_numbered_output(raw2 or "", len(batch))
                        pools.append((got2, _line_reasons(got2, reason2), [tid2] * len(batch)))
                    for j in range(len(batch)):
                        pool: list[str] = []
                        why: dict = {}
                        from_call: dict = {}
                        for one, one_reasons, one_traces in pools:
                            t = (one[j] if j < len(one) else "") or ""
                            t = t.strip()
                            if t and t not in pool:
                                pool.append(t)
                                why[t] = one_reasons[j] if j < len(one_reasons) else None
                                from_call[t] = one_traces[j] if j < len(one_traces) else None
                        if len(pool) < 2:
                            continue
                        winner = pool[0]
                        for rival_text in pool[1:]:
                            try:
                                v = await self._judge(state, loop,
                                                      batch[j].get("original") or "",
                                                      winner, rival_text, infer_params,
                                                      string_ids=[batch_ids[j]])
                            except Exception as exc:               # noqa: BLE001
                                log.warning("judge between candidates failed: %s", exc)
                                v = "unsure"
                            if v == "fresh":
                                winner = rival_text
                        translations[j] = winner
                        # Причина — того вызова, который дал победителя.
                        if j < len(reasons):
                            reasons[j] = why.get(winner)
                        # И трасса — того же вызова: кандидат, не первый проход.
                        if j < len(trace_ids):
                            trace_ids[j] = from_call.get(winner)

                # Судья между переводом и воротами. Численная оценка отвечает на
                # «не сломано ли», и это её работа; на «живее ли» она ответить не может
                # и молча оставляла хранимый текст. Здесь спрашивается то, чего она не
                # умеет, — и только там, где ответы РАЗНЫЕ: совпавшие спорить не о чем.
                verdicts: dict = {}
                judge_traces: dict = {}
                if judging:
                    for j, b in enumerate(batch):
                        fresh = (translations[j] if j < len(translations) else "") or ""
                        rival = (b.get("rival") or "").strip()
                        if not fresh.strip() or not rival:
                            continue
                        if fresh.strip() == rival:
                            verdicts[j] = "same"
                            continue
                        jt: list = []
                        judge_traces[j] = jt
                        try:
                            verdict = await self._judge_detail(
                                state, loop, b.get("original") or "", rival,
                                fresh.strip(), infer_params,
                                names=b.get("entities") or "",
                                string_ids=[b.get("string_id")], traces=jt)
                        except Exception as exc:                       # noqa: BLE001
                            log.warning("judge failed (%s) — verdict unsure", exc)
                            verdict = "unsure"
                        # Новый ответ доставляется ВСЕГДА, вердикт — рядом. Раньше при
                        # победе хранимого агент подменял свой перевод хранимым, и
                        # ответ модели пропадал без следа: ни применить позже, ни
                        # проверить, прав ли был судья.
                        verdicts[j] = verdict

                for j, b in enumerate(batch):
                    original    = b.get("original") or ""
                    translation = translations[j] if j < len(translations) else ""
                    if not translation:
                        continue   # leave manifest done=0 → retried next pass / next run
                    qs     = _inline_quality_score(original, translation)
                    finish = reasons[j] if j < len(reasons) else None
                    _cut_here = finish == "length"
                    # Генерация упёрлась в потолок — значит эта строка оборвана на
                    # полуслове, а не закончена. Бэкенд знает это точно; до сих пор
                    # мастер угадывал по тексту.
                    if _cut_here:
                        qs = min(qs, 60)
                    # Same gate as scripts/esp_engine.py:631 — anything the scorer is not
                    # confident about goes to review instead of silently counting as done.
                    # Untranslated passthrough scores 30 here; it used to be stored as
                    # "translated", so English text landed in the DB as finished work.
                    status = "translated" if (qs > 70 and not _cut_here) else "needs_review"
                    # DURABILITY POINT — commit before any network delivery happens.
                    seq = self._store.write_result(
                        assignment_id = self._aid,
                        string_id     = b["string_id"],
                        original      = original,
                        translation   = translation,
                        quality_score = qs,
                        status        = status,
                        string_hash   = b.get("string_hash"),
                        mod_name      = b.get("mod_name"),
                        esp_name      = b.get("esp_name"),
                        str_key       = b.get("str_key"),
                        judge         = verdicts.get(j) if judging else None,
                        rival         = (b.get("rival") or None) if judging else None,
                        finish_reason = finish,
                        model         = producer,
                        trace_id      = trace_ids[j] if j < len(trace_ids) else None,
                        judge_trace_ids = judge_traces.get(j) or None,
                    )
                    if seq is None:
                        # disk full — back off; this string stays pending for retry
                        await asyncio.sleep(5.0)
                        continue
                    self.done_count += 1

                # advance by the actual batch length (may be < batch_size when truncated at a
                # mod boundary above) so no pending item is ever skipped
                i += len(batch)

        log.info("OfflineTranslateRunner[%s]: produce finished (done=%d, cancelled=%s)",
                 self._aid[:8], self.done_count, self._stop)
