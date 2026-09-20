"""Кто произносит реплику — из плагинов, а не из догадки и не из вики.

У нас есть текст реплики и её FormID, и не было ничего о говорящем. Пол брался из имени
папки озвучки (`MaleNord` → мужчина), и это покрывало 15% строк: персональный голос вроде
`BergrisarVoice` про пол не говорит ничего, а таких голосов в паке большинство.

Но папка озвучки — это EDID записи VTYP, и на неё ссылается поле VTCK записи NPC_. То
есть связь «реплика → персонаж» уже лежит в плагинах, и её достаточно прочитать:

    реплика INFO ──(имя файла .fuz)──▶ тип голоса ──(VTCK)──▶ NPC_ ──▶ пол, раса, имя

Замер на 3DNPC: 759 записей NPC_, 666 из них с типом голоса, и `Bergrisar` находится
по `BergrisarVoice` точным совпадением с папкой озвучки.

ЧТО ЧИТАЕТСЯ И ПОЧЕМУ ИМЕННО ЭТО

    ACBS  флаг пола — единственное место, где пол записан прямо, а не угадывается;
    VTCK  тип голоса — ключ, которым реплика сходится с персонажем;
    RNAM  раса — за ней стоит речевая манера (аргониане и каджиты говорят иначе);
    FULL  имя — то, как игра называет персонажа на экране;
    EDID  имя в редакторе — нужно, когда FULL пуст или лежит в .STRINGS.

РАЗРЕШЕНИЕ ССЫЛОК

Старший байт FormID — это индекс плагина в СОБСТВЕННОМ списке мастеров записи, а не
глобальный. `RNAM = 0x00013746` в 3DNPC.esp означает расу из нулевого мастера, то есть
из Skyrim.esm, и искать её среди записей 3DNPC бессмысленно. Поэтому у каждого плагина
читается список MAST из TES4, и ссылка разрешается в пару (плагин-владелец, младшие 24
бита). Без этого раса не находится ни у одного NPC, а выглядит это как «в моде нет рас».

КЭШ

Полный обход плагинов пака — это гигабайты чтения, и он не меняется, пока не меняются
файлы. Результат складывается в cache/npc_index.json с отметкой размера и времени, как
это уже сделано для списка озвучки в BSA.
"""
from __future__ import annotations

import json
import logging
import struct
import sys
from pathlib import Path

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.esp_engine import iter_esp, parse_subrecords, u32  # noqa: E402

PLUGIN_SUFFIXES = (".esp", ".esm", ".esl")
CACHE_NAME = "npc_index.json"

# ACBS: первый u32 — флаги, младший бит означает «женщина».
_ACBS_FEMALE = 0x00000001


def _cstr(raw: bytes | None) -> str:
    """Строка плагина. UTF-8 пробуется первой, cp1252 — запасной.

    Плагины, которым мы уже применили перевод, несут русский в UTF-8, и чтение их как
    cp1252 даёт «Ð‘ÐµÑ€Ð³» вместо «Бергрисар». Обратный порядок безопасен: cp1252
    принимает любой байт, поэтому первым он проглотил бы и UTF-8.
    """
    if not raw:
        return ""
    head = raw.split(b"\0", 1)[0]
    try:
        return head.decode("utf-8")
    except UnicodeDecodeError:
        return head.decode("cp1252", "replace")


def _masters(data: bytes) -> list[str]:
    """Список мастеров плагина в порядке объявления — он же порядок старших байтов."""
    out: list[str] = []
    for kind, _pos, obj in iter_esp(data, 0, min(len(data), 1 << 20)):
        if kind != "rec" or obj.rtype != b"TES4":
            break
        for ft, fd in parse_subrecords(obj.data):
            if ft == b"MAST":
                out.append(_cstr(fd).lower())
        break
    return out


def _owner(form_id: int, plugin: str, masters: list[str]) -> tuple[str, int]:
    """(плагин-владелец, младшие 24 бита) для ссылки внутри этого плагина."""
    hi = (form_id >> 24) & 0xFF
    local = form_id & 0x00FFFFFF
    if hi < len(masters):
        return masters[hi], local
    return plugin, local


def read_plugin(path: Path) -> dict:
    """Записи NPC_, VTYP и RACE одного плагина, со ссылками, разрешёнными в пары."""
    data = path.read_bytes()
    plugin = path.name.lower()
    masters = _masters(data)
    npcs: list[dict] = []
    vtyp: dict[str, str] = {}
    races: dict[str, str] = {}

    def walk(buf: bytes, off: int, end: int) -> None:
        for kind, pos, obj in iter_esp(buf, off, end):
            if kind == "grup":
                walk(buf, pos + 24, pos + len(obj))
                continue
            if obj.rtype not in (b"NPC_", b"VTYP", b"RACE"):
                continue
            try:
                body = obj.decompressed_data()
            except Exception:
                continue
            f: dict[bytes, bytes] = {}
            for ft, fd in parse_subrecords(body):
                f.setdefault(ft, fd)          # первое вхождение: FULL бывает не одно
            local = obj.form_id & 0x00FFFFFF
            if obj.rtype == b"VTYP":
                vtyp[f"{plugin}:{local:06X}"] = _cstr(f.get(b"EDID"))
            elif obj.rtype == b"RACE":
                races[f"{plugin}:{local:06X}"] = _cstr(f.get(b"EDID"))
            else:
                acbs = f.get(b"ACBS")
                sex = None
                if acbs and len(acbs) >= 4:
                    sex = "f" if (u32(acbs, 0) & _ACBS_FEMALE) else "m"
                # FULL — имя на экране, EDID — имя в редакторе. Модели нужно первое
                # («Говорит: Берг»), но в локализованном плагине FULL это числовой
                # идентификатор строки, а не текст, и тогда остаётся EDID.
                full = f.get(b"FULL")
                name = ""
                if full and len(full) != 4:
                    name = _cstr(full)
                rec = {"plugin": plugin, "form_id": f"{obj.form_id:08X}",
                       "edid": _cstr(f.get(b"EDID")), "name": name, "sex": sex}
                for tag, name in ((b"VTCK", "voice"), (b"RNAM", "race")):
                    raw = f.get(tag)
                    if raw and len(raw) >= 4:
                        own, loc = _owner(u32(raw, 0), plugin, masters)
                        rec[name] = f"{own}:{loc:06X}"
                npcs.append(rec)

    walk(data, 0, len(data))
    return {"npcs": npcs, "vtyp": vtyp, "races": races}


def _stamp(p: Path) -> str:
    st = p.stat()
    return f"{st.st_size}:{int(st.st_mtime)}"


def scan(mods_dir: Path, cache_path: Path | None = None,
         progress=None, game_data: Path | None = None) -> dict:
    """Пройти плагины пака и собрать индекс. Кэш — по размеру и времени файла.

    `game_data` — папка Data самой игры. Без неё индекс получается наполовину пустым, и
    молча: VTYP ванильных голосов и все записи RACE лежат в Skyrim.esm, поэтому раса не
    находится ни у одного персонажа, а `MaleNord` не находится вовсе. Замер до её
    добавления: 0 рас из 42 221 записи NPC_.
    """
    cache: dict = {}
    if cache_path and cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            cache = {}

    plugins = sorted(p for p in mods_dir.glob("*/*")
                     if p.suffix.lower() in PLUGIN_SUFFIXES)
    if game_data and game_data.is_dir():
        # Мастера игры идут ПЕРВЫМИ: моды на них ссылаются, а не наоборот.
        plugins = sorted((p for p in game_data.glob("*")
                          if p.suffix.lower() in PLUGIN_SUFFIXES)) + plugins
    changed = False
    npcs: list[dict] = []
    vtyp: dict[str, str] = {}
    races: dict[str, str] = {}

    for i, p in enumerate(plugins, 1):
        key = f"{p.parent.name}/{p.name}"
        stamp = _stamp(p)
        hit = cache.get(key)
        if not hit or hit.get("stamp") != stamp:
            try:
                got = read_plugin(p)
            except Exception as exc:
                log.debug("npc_index: %s unreadable: %s", key, exc)
                got = {"npcs": [], "vtyp": {}, "races": {}}
            cache[key] = {"stamp": stamp, **got}
            changed = True
        entry = cache[key]
        npcs.extend(entry.get("npcs") or [])
        vtyp.update(entry.get("vtyp") or {})
        races.update(entry.get("races") or {})
        if progress and i % 200 == 0:
            progress(i, len(plugins))

    if changed and cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

    return {"npcs": npcs, "vtyp": vtyp, "races": races, "plugins": len(plugins)}


def by_voice_type(index: dict) -> dict:
    """Тип голоса (в нижнем регистре) → что известно о персонаже.

    Голос, за которым стоит НЕ ОДИН персонаж, возвращается со `shared: True` и без пола:
    `MaleNord` — это сотни NPC, и приписывать их реплики одному из них нельзя. Именно
    ради различения общего и персонального голоса это и считается.
    """
    vtyp, races = index["vtyp"], index["races"]
    buckets: dict[str, list] = {}
    for n in index["npcs"]:
        ref = n.get("voice")
        name = vtyp.get(ref or "")
        if not name:
            continue
        buckets.setdefault(name.lower(), []).append(n)

    out: dict[str, dict] = {}
    for vt, group in buckets.items():
        sexes = {n["sex"] for n in group if n["sex"]}
        racs = {races.get(n.get("race") or "") for n in group}
        racs.discard(None)
        racs.discard("")
        # Считать надо ПЕРСОНАЖЕЙ, а не записи. Патч, меняющий Бергу уровень, заводит
        # ещё одну запись NPC_ с тем же EDID — по записям Берг выглядел как трое разных
        # людей, и голос объявлялся общим. Персонаж — это EDID; при его отсутствии
        # запись считается отдельной, чтобы не склеить безымянных.
        who = {n["edid"].lower() if n["edid"] else f"?{n['plugin']}:{n['form_id']}"
               for n in group}
        out[vt] = {
            "voice_type": vt,
            "npc_count": len(who),
            "shared": len(who) > 1,
            # Пол однозначен, пока говорящий один или все они одного пола: у общего
            # типа вроде MaleNord это по-прежнему верно и полезно.
            "sex": next(iter(sexes)) if len(sexes) == 1 else None,
            "race": next(iter(racs)) if len(racs) == 1 else None,
            "names": sorted({n.get("name") for n in group if n.get("name")})[:4],
            "edids": sorted({n["edid"] for n in group if n["edid"]})[:8],
            "plugins": sorted({n["plugin"] for n in group})[:4],
        }
    return out
