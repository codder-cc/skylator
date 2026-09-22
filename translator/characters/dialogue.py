"""Кому адресована реплика и что было сказано до неё.

Разборщик плагина берёт только текстовые поля: ответ НПС (`INFO/NAM1`), вашу реплику
(`DIAL/FULL`), подпись варианта (`INFO/RNAM`). Связи между ними он не читал никогда,
поэтому модель получала строку и не знала ни кто её говорит, ни кому, ни что было
сказано минуту назад. Она переводила слова — ничего другого ей не давали.

Показательный случай, с которого это началось. Игрок отвечает Элдавин:

    You brute. A good vintage wine is wasted on your tongue.
    Ты грубиян. Хорошее выдержанное вино растрачивается на твой язык.

Элдавин — женщина, и система это знает точно (флаг пола в NPC_, тип голоса
`EldawynVoice`). Но «грубиян» стоит в реплике ИГРОКА, а пол говорящего к адресату
отношения не имеет. Знание было — оно не доходило до промпта.

ЧТО В ПЛАГИНЕ УЖЕ ЛЕЖИТ

    GRUP типа 7   «дети темы»: в заголовке группы стоит FormID своей DIAL, то есть
                  связь «ваша реплика → чьи ответы» записана прямо там;
    INFO/PNAM     предыдущая реплика — цепочка разговора;
    порядок       записи INFO внутри группы идут в порядке проигрывания.

Отсюда берутся обе вещи, которых не хватало: адресат вашей реплики (кто отвечает на
эту тему) и сам разговор (что было до и что будет после).

ПОЧЕМУ АДРЕСАТ — ЭТО ГОВОРЯЩИЙ ОТВЕТА

Тема озвучки не имеет: звуковой файл привязан к INFO, а не к DIAL. Поэтому пол
собеседника берётся у того, кто на эту тему ОТВЕЧАЕТ. Когда отвечающих несколько и
они разного пола — молчим: сказать модели про одного из двоих хуже, чем не сказать
ни про кого. Это то же правило, что и в карточках говорящего.

КЭШ

Обход плагинов пака — гигабайты чтения, и он не меняется, пока не меняются файлы.
Результат складывается в cache/dialogue_index.json с отметкой размера и времени, как
уже сделано для NPC и для списка озвучки.
"""
from __future__ import annotations

import json
import logging
import sys
import threading
from pathlib import Path

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.esp_engine import iter_esp, parse_subrecords, u32  # noqa: E402

PLUGIN_SUFFIXES = (".esp", ".esm", ".esl")
CACHE_NAME = "dialogue_index.json"

# Тип группы «дети темы»: её label — это FormID записи DIAL, которой принадлежат
# лежащие внутри INFO. Единственное место, где связь темы с ответами записана прямо.
_TOPIC_CHILDREN = 7


def _masters(data: bytes) -> list[str]:
    """Список мастеров плагина — он же порядок старших байтов в ссылках."""
    out: list[str] = []
    for kind, _pos, obj in iter_esp(data, 0, min(len(data), 1 << 20)):
        if kind != "rec" or obj.rtype != b"TES4":
            break
        for ft, fd in parse_subrecords(obj.data):
            if ft == b"MAST":
                out.append(fd.split(b"\0", 1)[0].decode("cp1252", "replace").lower())
        break
    return out


def _owner(form_id: int, plugin: str, masters: list[str]) -> str:
    """«плагин:FORMID» для ссылки внутри этого плагина.

    Старший байт — индекс в СОБСТВЕННОМ списке мастеров, а не глобальный: мод может
    дописывать ответы к ванильной теме, и тогда её FormID принадлежит Skyrim.esm.
    """
    hi = (form_id >> 24) & 0xFF
    local = form_id & 0x00FFFFFF
    owner = masters[hi] if hi < len(masters) else plugin
    return f"{owner}:{local:06X}"


def read_plugin(path: Path) -> dict:
    """{'topics': {тема: [ответы]}, 'prev': {ответ: предыдущий}} одного плагина."""
    data = path.read_bytes()
    plugin = path.name.lower()
    masters = _masters(data)
    topics: dict[str, list[str]] = {}
    prev: dict[str, str] = {}
    # Одна и та же тема зовётся по-разному с двух сторон. Мод, дописывающий ответы к
    # ванильной теме, держит её FormID со своим старшим байтом, а граф хранит её под
    # владельцем — «skyrim.esm:02137B». Без этой таблицы такая тема не находится, и
    # выглядит это как «у реплики нет ответов».
    alias: dict[str, str] = {}

    def walk(buf: bytes, off: int, end: int, topic: str | None) -> None:
        for kind, pos, obj in iter_esp(buf, off, end):
            if kind == "grup":
                # obj здесь — байты группы целиком, вместе с её 24-байтным заголовком.
                gtype = u32(obj, 12)
                inner = topic
                if gtype == _TOPIC_CHILDREN:
                    raw = u32(obj, 8)
                    inner = _owner(raw, plugin, masters)
                    alias[f"{plugin}:{raw & 0x00FFFFFF:06X}"] = inner
                walk(buf, pos + 24, pos + len(obj), inner)
                continue
            if obj.rtype != b"INFO" or topic is None:
                continue
            key = f"{plugin}:{obj.form_id & 0x00FFFFFF:06X}"
            topics.setdefault(topic, []).append(key)
            try:
                body = obj.decompressed_data()
            except Exception:
                continue
            for ft, fd in parse_subrecords(body):
                if ft == b"PNAM" and len(fd) >= 4:
                    prev[key] = _owner(u32(fd, 0), plugin, masters)
                    break

    walk(data, 0, len(data), None)
    return {"topics": topics, "prev": prev, "alias": alias}


def _stamp(p: Path) -> str:
    st = p.stat()
    return f"{st.st_size}:{int(st.st_mtime)}"


def scan(mods_dir: Path, cache_path: Path | None = None,
         progress=None, game_data: Path | None = None) -> dict:
    """Пройти плагины и собрать граф диалогов. Кэш — по размеру и времени файла."""
    cache: dict = {}
    if cache_path and cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            cache = {}

    plugins = sorted(p for p in mods_dir.glob("*/*")
                     if p.suffix.lower() in PLUGIN_SUFFIXES)
    if game_data and game_data.is_dir():
        plugins = sorted(p for p in game_data.glob("*")
                         if p.suffix.lower() in PLUGIN_SUFFIXES) + plugins

    fresh: dict = {}
    for i, p in enumerate(plugins):
        if progress:
            progress(i, len(plugins), p.name)
        try:
            stamp = _stamp(p)
        except OSError:
            continue
        old = cache.get(str(p))
        if isinstance(old, dict) and old.get("stamp") == stamp:
            fresh[str(p)] = old
            continue
        try:
            got = read_plugin(p)
        except Exception as exc:                                   # noqa: BLE001
            log.debug("dialogue: %s не читается (%s)", p.name, exc)
            continue
        got["stamp"] = stamp
        fresh[str(p)] = got

    if cache_path:
        try:
            cache_path.write_text(json.dumps(fresh, ensure_ascii=False),
                                  encoding="utf-8")
        except Exception as exc:                                   # noqa: BLE001
            log.warning("dialogue: кэш не записан: %s", exc)
    return fresh


def merge(cache: dict) -> dict:
    """Свести кэш всех плагинов в общие карты."""
    topics: dict[str, list[str]] = {}
    prev: dict[str, str] = {}
    alias: dict[str, str] = {}
    for entry in cache.values():
        if not isinstance(entry, dict):
            continue
        for topic, infos in (entry.get("topics") or {}).items():
            topics.setdefault(topic, []).extend(infos)
        prev.update(entry.get("prev") or {})
        alias.update(entry.get("alias") or {})
    return {"topics": topics, "prev": prev, "alias": alias}


# -- доступ для промпта и ворот записи ---------------------------------------------

_LOCK = threading.Lock()
_STATE: dict | None = None


def load(mods_dir: Path | None = None, game_data: Path | None = None,
         force: bool = False) -> dict:
    """Граф диалогов, собранный один раз на процесс."""
    global _STATE
    with _LOCK:
        if _STATE is not None and not force:
            return _STATE
        cache_path = _ROOT / "cache" / CACHE_NAME
        cache: dict = {}
        if cache_path.exists() and not force:
            try:
                cache = json.loads(cache_path.read_text(encoding="utf-8"))
            except Exception as exc:                               # noqa: BLE001
                log.warning("dialogue: кэш не читается (%s)", exc)
        if not cache and mods_dir is not None:
            cache = scan(mods_dir, cache_path, game_data=game_data)
        _STATE = merge(cache)
        log.info("dialogue: %d тем, %d ответов", len(_STATE["topics"]),
                 sum(len(v) for v in _STATE["topics"].values()))
        return _STATE


def _key(esp_name: str, form_id: str) -> str:
    return f"{(esp_name or '').lower()}:{(form_id or '').upper()[-6:]}"


def answers_to(esp_name: str, form_id: str, state: dict | None = None) -> list[str]:
    """Ответы на эту тему — «plugin:FORMID», в порядке проигрывания."""
    st = state if state is not None else load()
    k = _key(esp_name, form_id)
    return (st.get("topics") or {}).get((st.get("alias") or {}).get(k, k), [])


def addressee_gender(esp_name: str, form_id: str, state: dict | None = None) -> str | None:
    """Пол того, к кому обращена эта реплика игрока, или None.

    Тема озвучки не имеет — звук привязан к ответу. Поэтому пол собеседника берётся у
    того, кто на тему ОТВЕЧАЕТ. Отвечающих бывает несколько; если они разного пола,
    молчим: сказать модели про одного из двоих хуже, чем не сказать ни про кого.
    """
    from translator.characters import speakers as _sp
    got = set()
    for ans in answers_to(esp_name, form_id, state):
        plug, fid = ans.split(":", 1)
        g = _sp.gender_for(plug, fid)
        if g:
            got.add(g)
    return next(iter(got)) if len(got) == 1 else None


def addressee_gender_for(esp_name: str, form_id: str, rec_type: str | None,
                         field_type: str | None) -> str | None:
    """Пол собеседника для этой строки, или None — если строка не к кому-то обращена.

    Реплики игрока лежат в двух местах, и адресат у них берётся по-разному:

        DIAL/FULL   тема разговора — отвечает тот, чьи INFO лежат в её группе;
        INFO/RNAM   подпись варианта — отвечает говорящий этого же INFO.

    Ответ НПС (INFO/NAM1) обращён к игроку, а его пол неизвестен, и здесь мы молчим.
    """
    if rec_type == "DIAL" and field_type == "FULL":
        return addressee_gender(esp_name, form_id)
    if rec_type == "INFO" and field_type == "RNAM":
        from translator.characters import speakers as _sp
        return _sp.gender_for(esp_name, form_id)
    return None
