"""
Nolvus ESP Translation Engine
Based on xTranslator's _recorddefs.txt string identification logic.

Commands (standalone):
  python esp_engine.py inspect  <esp>
  python esp_engine.py export   <esp> [out.json]
  python esp_engine.py translate <esp> [progress.json]
  python esp_engine.py apply    <esp> [in.json] [--out <out.esp>]
  python esp_engine.py run      <esp>

Used programmatically by translator.cli via cmd_translate().
"""

import struct, zlib, json, re, sys, subprocess, shutil, argparse, logging
from pathlib import Path
from copy import deepcopy

log = logging.getLogger(__name__)

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')


def _get_cfg():
    from translator.config import get_config
    return get_config()


def _paths():
    cfg = _get_cfg()
    return cfg.paths


# ── Translatable field definitions (from xTranslator Data/SkyrimSE/_recorddefs.txt) ──
TRANS_FIELDS = {
    (b'FULL', None),       # Full name - any record
    (b'DESC', None),       # Description - any record
    (b'DNAM', b'MGEF'),    # Magic effect description
    (b'NAM1', b'INFO'),    # Dialogue response text
    (b'SHRT', b'NPC_'),    # NPC short name
    (b'CNAM', b'QUST'),    # Quest description
    (b'CNAM', b'BOOK'),    # Book author
    (b'TNAM', b'WOOP'),    # Word of power translation
    (b'NNAM', b'QUST'),    # Quest next stage text
    (b'ITXT', b'MESG'),    # Message button text
    (b'RDMP', b'REGN'),    # Region map name
    (b'RNAM', b'ACTI'),    # Activator verb
    (b'RNAM', b'FLOR'),    # Flora verb
    (b'RNAM', b'INFO'),    # Dialogue response label
    (b'BPTN', b'BPTD'),    # Body part name
    (b'MNAM', b'FACT'),    # Faction male rank name
    (b'FNAM', b'FACT'),    # Faction female rank name
    (b'DESC', b'LSCR'),    # Load screen text
}
# Special handled below:
# GMST:DATA  when edid.startswith('s')
# PERK:EPFD  when last_epft == 7
# PERK:EPF2  when last_epft == 4
# NOTE:TNAM  when note_data_type == 1
# VMAD:      property types 2 and 12


def is_translatable(field: bytes, record: bytes) -> bool:
    if (field, None) in TRANS_FIELDS:
        return True
    if (field, record) in TRANS_FIELDS:
        return True
    return False


# ── Binary helpers ────────────────────────────────────────────────────────────

def u32(data, off): return struct.unpack_from('<I', data, off)[0]
def u16(data, off): return struct.unpack_from('<H', data, off)[0]
def p32(v):         return struct.pack('<I', v)
def p16(v):         return struct.pack('<H', v)

def read_cstring(data: bytes) -> str:
    end = data.find(b'\x00')
    raw = data[:end] if end >= 0 else data
    for enc in ('utf-8', 'cp1252', 'latin-1'):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode('latin-1', errors='replace')

def write_cstring(s: str) -> bytes:
    return s.encode('utf-8') + b'\x00'


# ── Subrecord parser / builder ────────────────────────────────────────────────

def parse_subrecords(data: bytes):
    """
    Yield (field_type:bytes, field_data:bytes) for each subrecord.
    Handles XXXX large-size prefix.
    """
    pos = 0
    while pos + 6 <= len(data):
        ftype = data[pos:pos+4]
        fsize = u16(data, pos+4)
        pos += 6

        if ftype == b'XXXX' and fsize == 4:
            real_size = u32(data, pos)
            pos += 4
            if pos + 6 <= len(data):
                ftype2 = data[pos:pos+4]
                pos += 6
                fdata = data[pos:pos+real_size]
                pos += real_size
                yield ftype2, fdata
            continue

        fdata = data[pos:pos+fsize]
        pos += fsize
        yield ftype, fdata


def build_subrecords(fields: list) -> bytes:
    out = bytearray()
    for ftype, fdata in fields:
        size = len(fdata)
        if size > 0xFFFF:
            out += b'XXXX' + p16(4) + p32(size)
            out += ftype + p16(0) + fdata
        else:
            out += ftype + p16(size) + fdata
    return bytes(out)


# ── Record / GRUP parser ──────────────────────────────────────────────────────

class Record:
    __slots__ = ('rtype', 'data_size', 'flags', 'form_id',
                 'header_rest', 'data', 'offset')

    def __init__(self, rtype, data_size, flags, form_id, header_rest, data, offset):
        self.rtype       = rtype
        self.data_size   = data_size
        self.flags       = flags
        self.form_id     = form_id
        self.header_rest = header_rest
        self.data        = data
        self.offset      = offset

    @property
    def compressed(self):
        return bool(self.flags & 0x00040000)

    def decompressed_data(self):
        if not self.compressed:
            return self.data
        uncomp_size = u32(self.data, 0)
        return zlib.decompress(self.data[4:])

    def recompress(self, raw: bytes) -> bytes:
        compressed = zlib.compress(raw, 6)
        return p32(len(raw)) + compressed

    def to_bytes(self, new_data: bytes = None) -> bytes:
        payload = new_data if new_data is not None else self.data
        return (self.rtype + p32(len(payload)) +
                p32(self.flags) + p32(self.form_id) +
                self.header_rest + payload)


def iter_esp(data: bytes, offset: int = 0, end: int = None):
    if end is None:
        end = len(data)
    while offset < end:
        if offset + 24 > end:
            break
        rtype = data[offset:offset+4]
        if rtype == b'GRUP':
            grp_size  = u32(data, offset+4)
            grp_bytes = data[offset:offset+grp_size]
            yield 'grup', offset, grp_bytes
            offset += grp_size
        else:
            data_size   = u32(data, offset+4)
            flags       = u32(data, offset+8)
            form_id     = u32(data, offset+12)
            header_rest = data[offset+16:offset+24]
            payload     = data[offset+24:offset+24+data_size]
            rec = Record(rtype, data_size, flags, form_id, header_rest, payload, offset)
            yield 'rec', offset, rec
            offset += 24 + data_size


# ── VMAD (Papyrus script property strings) ────────────────────────────────────

def parse_vmad_strings(data: bytes) -> list:
    """
    Parse VMAD subrecord, return list of (len_prefix_offset, old_length, text).
    Extracts property type 2 (single string) and type 12 (string array).
    Mirrors xTranslator TESVT_VMAD.pas ReadProperties / getLenString logic.
    """
    n = len(data)
    pos = [0]
    strings = []

    def read_u8():
        if pos[0] >= n: raise ValueError("truncated")
        v = data[pos[0]]; pos[0] += 1; return v

    def read_u16():
        if pos[0] + 2 > n: raise ValueError("truncated")
        v = u16(data, pos[0]); pos[0] += 2; return v

    def read_u32():
        if pos[0] + 4 > n: raise ValueError("truncated")
        v = u32(data, pos[0]); pos[0] += 4; return v

    def skip(count): pos[0] += count

    def skip_len_string():
        length = read_u16(); skip(length)

    def get_len_string():
        off = pos[0]
        length = read_u16()
        raw = data[pos[0]:pos[0] + length]; pos[0] += length
        for enc in ('utf-8', 'cp1252', 'latin-1'):
            try: text = raw.decode(enc); break
            except Exception: continue
        else:
            text = raw.decode('latin-1', errors='replace')
        strings.append((off, length, text))

    def read_properties():
        skip_len_string()           # prop name
        ptype = read_u8()
        if version >= 4: read_u8()  # status byte (only version >= 4)
        if   ptype == 0:  pass
        elif ptype == 1:  skip(8)
        elif ptype == 2:  get_len_string()
        elif ptype == 3:  skip(4)
        elif ptype == 4:  skip(4)
        elif ptype == 5:  skip(1)
        elif ptype == 11: count = read_u32(); skip(count * 8)
        elif ptype == 12:
            count = read_u32()
            for _ in range(count): get_len_string()
        elif ptype == 13: count = read_u32(); skip(count * 4)
        elif ptype == 14: count = read_u32(); skip(count * 4)
        elif ptype == 15: count = read_u32(); skip(count)
        else: raise ValueError(f"unknown VMAD prop type {ptype}")

    def read_script(read_name=True):
        if read_name: skip_len_string()     # script name
        if version >= 4: read_u8()          # status byte (only version >= 4)
        pcount = read_u16()
        for _ in range(pcount): read_properties()

    try:
        version = read_u16()                # version (2-5; SE uses 5)
        read_u16()                          # objFormat (1 or 2)
        scount = read_u16()
        for _ in range(scount): read_script()
    except Exception:
        pass    # partial parse is fine

    return strings


def rewrite_vmad_strings(data: bytes, translations: dict) -> tuple:
    """
    Rebuild VMAD bytes with translated strings.
    translations: {vmad_str_idx: new_text}
    Returns (new_data, changed:bool).
    """
    try:
        strings = parse_vmad_strings(data)
    except Exception:
        return data, False

    patches = [(off, length, translations[i])
               for i, (off, length, _) in enumerate(strings)
               if i in translations and translations[i]]
    if not patches:
        return data, False

    result = bytearray(data)
    for off, old_len, new_text in sorted(patches, key=lambda x: -x[0]):
        new_bytes = new_text.encode('utf-8')
        result = (result[:off] + p16(len(new_bytes)) +
                  new_bytes + result[off + 2 + old_len:])
    return bytes(result), True


# ── String extraction ─────────────────────────────────────────────────────────

def extract_strings_from_record(rec: Record, localized: bool) -> list:
    if rec.rtype == b'TES4':
        return []
    try:
        raw = rec.decompressed_data()
    except Exception:
        return []

    results = []
    edid = None
    last_epft = None
    note_data_type = None
    field_index = 0

    for ftype, fdata in parse_subrecords(raw):
        if ftype == b'EDID':
            edid = read_cstring(fdata)
        if ftype == b'EPFT' and rec.rtype == b'PERK':
            last_epft = fdata[0] if fdata else None
        if ftype == b'DATA' and rec.rtype == b'NOTE':
            note_data_type = fdata[0] if fdata else None

        if ftype == b'VMAD' and not localized:
            vmad_strs = parse_vmad_strings(fdata)
            for si, (off, length, text) in enumerate(vmad_strs):
                if text.strip() and any(c.isalpha() for c in text):
                    results.append({
                        'form_id':      f'{rec.form_id:08X}',
                        'rec_type':     rec.rtype.decode('ascii', errors='?'),
                        'field_type':   'VMAD',
                        'text':         text,
                        'translation':  '',
                        'field_index':  field_index,
                        'vmad_str_idx': si,
                    })

        if localized:
            if is_translatable(ftype, rec.rtype) and len(fdata) == 4:
                sid = u32(fdata, 0)
                if sid > 0:
                    results.append({
                        'form_id':    f'{rec.form_id:08X}',
                        'rec_type':   rec.rtype.decode('ascii', errors='?'),
                        'field_type': ftype.decode('ascii', errors='?'),
                        'string_id':  sid,
                        'text':       f'[LOC:{sid}]',
                        'field_index': field_index,
                    })
        else:
            should_extract = False

            if is_translatable(ftype, rec.rtype):
                should_extract = True

            if ftype == b'DATA' and rec.rtype == b'GMST':
                should_extract = edid is not None and edid.startswith('s')

            if ftype == b'EPFD' and rec.rtype == b'PERK':
                should_extract = (last_epft == 7)

            if ftype == b'EPF2' and rec.rtype == b'PERK':
                should_extract = (last_epft == 4)

            if ftype == b'TNAM' and rec.rtype == b'NOTE':
                should_extract = (note_data_type == 1)

            if should_extract and fdata:
                text = read_cstring(fdata)
                if text.strip() and any(c.isalpha() for c in text):
                    results.append({
                        'form_id':     f'{rec.form_id:08X}',
                        'rec_type':    rec.rtype.decode('ascii', errors='?'),
                        'field_type':  ftype.decode('ascii', errors='?'),
                        'text':        text,
                        'translation': '',
                        'field_index': field_index,
                    })

        field_index += 1

    return results


def extract_all_strings(esp_path: Path) -> tuple:
    data = esp_path.read_bytes()
    flags    = u32(data, 8)
    localized = bool(flags & 0x80)
    all_strings = []

    def walk(buf, off, end):
        for kind, pos, obj in iter_esp(buf, off, end):
            if kind == 'rec':
                all_strings.extend(extract_strings_from_record(obj, localized))
            elif kind == 'grup':
                walk(buf, pos+24, pos+len(obj))

    walk(data, 0, len(data))
    return all_strings, localized


# ── ESP rewriter ──────────────────────────────────────────────────────────────

def apply_translations_to_record(rec: Record, trans_map: dict):
    if rec.rtype == b'TES4':
        return None
    try:
        raw = rec.decompressed_data()
    except Exception:
        return None

    new_fields = []
    changed = False
    last_epft = None
    field_index = 0
    form_id_str  = f'{rec.form_id:08X}'
    rec_type_str = rec.rtype.decode('ascii', errors='?')

    for ftype, fdata in parse_subrecords(raw):
        if ftype == b'EPFT' and rec.rtype == b'PERK':
            last_epft = fdata[0] if fdata else None

        if ftype == b'VMAD':
            vmad_key = (form_id_str, rec_type_str, 'VMAD', field_index)
            if vmad_key in trans_map:
                new_fdata, vmad_changed = rewrite_vmad_strings(fdata, trans_map[vmad_key])
                if vmad_changed:
                    new_fields.append((ftype, new_fdata))
                    changed = True
                    field_index += 1
                    continue

        key = (form_id_str, rec_type_str,
               ftype.decode('ascii', errors='?'),
               field_index)

        if key in trans_map:
            translation = trans_map[key]
            if translation and translation.strip():
                new_fields.append((ftype, write_cstring(translation)))
                changed = True
                field_index += 1
                continue

        new_fields.append((ftype, fdata))
        field_index += 1

    if not changed:
        return None

    new_raw = build_subrecords(new_fields)
    new_payload = rec.recompress(new_raw) if rec.compressed else new_raw
    return rec.to_bytes(new_payload)


def rewrite_esp(esp_path: Path, trans_map: dict, out_path: Path):
    data = esp_path.read_bytes()

    def rewrite_chunk(buf, off, end) -> bytes:
        out = bytearray()
        for kind, pos, obj in iter_esp(buf, off, end):
            if kind == 'rec':
                new_rec = apply_translations_to_record(obj, trans_map)
                if new_rec is not None:
                    out += new_rec
                else:
                    out += buf[pos:pos+24+obj.data_size]
            elif kind == 'grup':
                grp_size = u32(buf, pos+4)
                inner    = rewrite_chunk(buf, pos+24, pos+grp_size)
                new_grp_size = 24 + len(inner)
                out += buf[pos:pos+4]
                out += p32(new_grp_size)
                out += buf[pos+8:pos+24]
                out += inner
        return bytes(out)

    new_data = rewrite_chunk(data, 0, len(data))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(new_data)
    print(f"Written: {out_path}  ({len(new_data):,} bytes, was {len(data):,})")


# ── Translation ───────────────────────────────────────────────────────────────

def translate_batch(texts: list, context: str = '', progress_cb=None) -> list:
    """Delegate to translator.pipeline (ensemble)."""
    try:
        from translator.pipeline import translate_batch as _tb
        return _tb(texts, context, progress_cb=progress_cb)
    except Exception as e:
        log.error("translate_batch error: %s", e)
        return list(texts)


def needs_translation(text: str) -> bool:
    if not text or not text.strip():
        return False
    t = text.strip()
    # MCM $KEY tokens — resolved at runtime from MCM txt files, not AI-translatable
    if t.startswith('$'):
        return False
    # Code identifiers: single token with underscore OR internal CamelCase uppercase
    # e.g. "SKI_FavoritesManagerInstance", "ACL_SettingsQuest", "SkyUI", "TES5Edit"
    # NOT: "Whiterun", "Falkreath" (simple capitalized proper nouns)
    if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]+', t):
        if '_' in t or re.search(r'[A-Z]', t[1:]):
            return False
    # All-uppercase labels / abbreviations (≥2 letters): "SKY UI", "SKI MCM", "N/A", "SKSE"
    letters = [c for c in t if c.isalpha()]
    if len(letters) >= 2 and all(c.isupper() for c in letters):
        return False
    # Version strings: "v1.2.3", "1.0.2b"
    if re.fullmatch(r'v?\d+(\.\d+)+\w*', t, re.IGNORECASE):
        return False
    cyrillic = sum(1 for c in t if '\u0400' <= c <= '\u04ff')
    if cyrillic > len(t) * 0.3:
        return False
    return bool(re.search(r'[a-zA-Z]', t))


def quality_score(original: str, translation: str) -> int:
    """
    Heuristic quality score 0–100 for a translation.
    Used to flag potentially bad translations in the strings editor.
    """
    if not translation or not translation.strip():
        return 0
    score = 100
    # Length ratio — Russian is typically 10-30% longer than English
    ratio = len(translation) / max(len(original), 1)
    if ratio > 5.0 or ratio < 0.15:
        score -= 40
    elif ratio > 3.0 or ratio < 0.3:
        score -= 20
    elif ratio > 2.0 or ratio < 0.5:
        score -= 10
    # Skyrim inline token preservation
    token_re = re.compile(r'<[A-Za-z][^>]*>|\[PageBreak\]|\\n|%[dis%]|\[CRLF\]', re.IGNORECASE)
    orig_tokens = token_re.findall(original)
    trans_tokens = token_re.findall(translation)
    missing = max(0, len(orig_tokens) - len(trans_tokens))
    score -= missing * 25
    # Control characters
    if re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', translation):
        score -= 30
    # Encoding artifacts (Windows-1252 double-decode)
    if any(art in translation for art in ("â€", "Ã©", "Ã ", "Â ")):
        score -= 40
    # Untranslated (still all Latin, same as original)
    if translation.strip() == original.strip():
        score -= 50
    # Still mostly Latin (bad translation — should be mostly Cyrillic for Russian)
    latin = sum(1 for c in translation if c.isascii() and c.isalpha())
    cyrillic = sum(1 for c in translation if '\u0400' <= c <= '\u04ff')
    if len(translation) > 10 and latin > 0 and cyrillic == 0:
        score -= 30
    return max(0, min(100, score))


def translate_strings(strings: list, progress_path: Path = None, context: str = '',
                      progress_cb=None) -> list:
    """
    Translate all strings in one pipeline call (model loads once, not per batch).
    progress_cb(done, total) called after translation completes.
    """
    # Load incremental progress from disk
    done: dict[str, str] = {}
    if progress_path and progress_path.exists():
        try:
            saved = json.loads(progress_path.read_text('utf-8'))
            done  = {s['text']: s['translation'] for s in saved if s.get('translation')}
            log.info("Resuming: %d strings already translated", len(done))
        except Exception:
            pass

    to_do    = [(i, s) for i, s in enumerate(strings)
                if needs_translation(s['text']) and not s.get('translation')]
    uncached = [(i, s) for i, s in to_do if s['text'] not in done]

    log.info("Strings needing translation: %d / %d  (%d cached)",
             len(to_do), len(strings), len(to_do) - len(uncached))

    if uncached:
        texts = [s['text'] for _, s in uncached]
        log.info("Sending %d strings to pipeline (model loads once)...", len(texts))
        cached_count = len(to_do) - len(uncached)
        total_todo   = len(to_do)

        def _inner_cb(batch_done, _batch_total):
            if progress_cb:
                progress_cb(cached_count + batch_done, total_todo)

        translated = translate_batch(texts, context, progress_cb=_inner_cb)
        for (i, s), t in zip(uncached, translated):
            done[s['text']] = t

    # Apply all translations back
    for i, s in to_do:
        if s['text'] in done:
            strings[i]['translation'] = done[s['text']]

    # Add quality scores to all translated strings
    for s in strings:
        if s.get('translation'):
            s['quality_score'] = quality_score(s['text'], s['translation'])

    if progress_path:
        progress_path.write_text(
            json.dumps(strings, ensure_ascii=False, indent=2), encoding='utf-8')

    n_done = sum(1 for s in strings if s.get('translation'))
    log.info("Translation complete: %d / %d strings", n_done, len(strings))
    if progress_cb and not uncached:
        # No uncached strings — fire once at the end
        progress_cb(n_done, len(strings))

    return strings


# ── CLI command functions (called by translator.cli) ─────────────────────────

def cmd_inspect(esp_path: Path):
    strings, localized = extract_all_strings(esp_path)
    print(f"ESP: {esp_path.name}")
    print(f"Localized: {localized}")
    print(f"Translatable strings: {len(strings)}")
    needs = sum(1 for s in strings if needs_translation(s['text']))
    print(f"Needs translation: {needs}")
    for s in strings[:50]:
        print(f"  [{s['rec_type']}:{s['form_id']}] {s['field_type']}: {s['text']!r}")
    if len(strings) > 50:
        print(f"  ... and {len(strings)-50} more")


def cmd_export(esp_path: Path, out_path: Path):
    strings, localized = extract_all_strings(esp_path)
    out_path.write_text(json.dumps(strings, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"Exported {len(strings)} strings to {out_path}")


def _update_caches(esp_path: Path, strings: list, mod_folder: Path = None) -> None:
    """
    Update translation_cache.json (for scanner translated count)
    and _string_counts.json (for scanner total count) after translation.
    Called after translate_strings() completes.
    """
    try:
        paths = _paths()

        # 1. translation_cache.json — stores translated strings keyed by esp stem
        cache_path = paths.translation_cache
        cache = json.loads(cache_path.read_text('utf-8')) if cache_path.exists() else {}
        esp_key = esp_path.stem.lower()
        cache.setdefault(esp_key, {})
        for s in strings:
            if s.get('translation') and s['translation'].strip():
                key = str((s['form_id'], s['rec_type'], s['field_type'], s['field_index']))
                cache[esp_key][key] = s['translation']
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding='utf-8')
        log.info("Cache updated: %d translated entries for %s",
                 len(cache.get(esp_key, {})), esp_path.stem)

        # 2. _string_counts.json — stores total string count keyed by mod/esp
        counts_path = paths.translation_cache.parent / "_string_counts.json"
        counts = json.loads(counts_path.read_text('utf-8')) if counts_path.exists() else {}
        mod_name = mod_folder.name if mod_folder else esp_path.parent.name
        counts_key = f"{mod_name}/{esp_path.name}"
        try:
            size = esp_path.stat().st_size
        except OSError:
            size = 0
        counts[counts_key] = {"size": size, "count": len(strings)}
        counts_path.write_text(json.dumps(counts, ensure_ascii=False, indent=2), encoding='utf-8')
        log.info("Count cache updated: %d strings for %s", len(strings), counts_key)

    except Exception as exc:
        log.warning("Could not update caches: %s", exc)


def _build_trans_map(strings: list) -> dict:
    """Build trans_map dict from a strings list (for rewrite_esp)."""
    trans_map = {}
    for s in strings:
        if not (s.get('translation') and s['translation'].strip()):
            continue
        if s['field_type'] == 'VMAD':
            vkey = (s['form_id'], s['rec_type'], 'VMAD', s['field_index'])
            trans_map.setdefault(vkey, {})[s['vmad_str_idx']] = s['translation']
        else:
            key = (s['form_id'], s['rec_type'], s['field_type'], s['field_index'])
            trans_map[key] = s['translation']
    return trans_map


def _backup_esp(esp_path: Path, out_path: Path) -> None:
    """Create a backup of esp_path in the backup_dir if not already backed up."""
    if out_path != esp_path:
        return
    try:
        paths = _paths()
        try:
            rel = esp_path.relative_to(paths.mods_dir)
        except ValueError:
            rel = Path(esp_path.name)
        backup_dest = paths.backup_dir / rel
        if not backup_dest.exists():
            backup_dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(esp_path, backup_dest)
            log.info("Backed up original to %s", backup_dest)
    except Exception as exc:
        log.warning("Backup failed: %s", exc)


def cmd_translate(esp_path: Path, out_path: Path, mod_folder: Path = None,
                  dry_run: bool = False, progress_cb=None, apply_esp: bool = True):
    """
    Translate an ESP.
    apply_esp=True  → full pipeline: AI translate + rewrite ESP binary (default).
    apply_esp=False → translate-only: AI translate + save .trans.json, do NOT write ESP.
    mod_folder: used for Nexus context lookup and cache keys.
    """
    json_path = esp_path.with_suffix('.trans.json')

    if json_path.exists():
        strings = json.loads(json_path.read_text('utf-8'))
        log.info("Loaded %d strings from %s", len(strings), json_path.name)
    else:
        log.info("Extracting strings from %s ...", esp_path.name)
        strings, _ = extract_all_strings(esp_path)
        log.info("Extracted %d strings", len(strings))

    context = ''
    if mod_folder:
        try:
            from translator.pipeline import get_mod_context
            context = get_mod_context(mod_folder)
            if context:
                log.info("Context: %s...", context[:120])
        except Exception as exc:
            log.warning("get_mod_context failed: %s", exc)

    if dry_run:
        needs = sum(1 for s in strings if needs_translation(s['text']))
        log.info("[DRY RUN] %d strings, %d need translation — no writes", len(strings), needs)
        return

    log.info("Starting translation of %s ...", esp_path.name)
    strings = translate_strings(strings, progress_path=json_path, context=context,
                                progress_cb=progress_cb)

    done = sum(1 for s in strings if s.get('translation'))
    log.info("Done: %d / %d strings translated in %s", done, len(strings), esp_path.name)

    if not apply_esp:
        # Update caches with CURRENT (unchanged) ESP size
        _update_caches(esp_path, strings, mod_folder)
        log.info("Translate-only mode — ESP binary NOT modified")
        return

    # Apply to ESP binary
    trans_map = _build_trans_map(strings)
    log.info("Applying %d translations to %s ...", len(trans_map), esp_path.name)
    _backup_esp(esp_path, out_path)
    rewrite_esp(esp_path, trans_map, out_path)
    # Update caches AFTER rewrite so stored size matches the new ESP file
    _update_caches(esp_path, strings, mod_folder)


def cmd_apply_from_trans(esp_path: Path, out_path: Path = None, mod_folder: Path = None):
    """
    Apply translations from .trans.json to ESP binary (no AI translation).
    Used for the separate "Apply & Write ESP" pipeline step.
    """
    if out_path is None:
        out_path = esp_path
    json_path = esp_path.with_suffix('.trans.json')
    if not json_path.exists():
        log.warning("No .trans.json for %s — run translate step first", esp_path.name)
        return 0

    strings = json.loads(json_path.read_text('utf-8'))
    trans_map = _build_trans_map(strings)
    log.info("Applying %d translations to %s ...", len(trans_map), esp_path.name)
    _backup_esp(esp_path, out_path)
    rewrite_esp(esp_path, trans_map, out_path)
    done = sum(1 for s in strings if s.get('translation'))
    log.info("Applied %d/%d translations to %s", done, len(strings), esp_path.name)
    _update_caches(esp_path, strings, mod_folder)
    return done


def cmd_apply(esp_path: Path, json_path: Path, out_path: Path = None):
    strings = json.loads(json_path.read_text('utf-8'))
    trans_map = _build_trans_map(strings)
    log.info("Applying %d translations...", len(trans_map))
    if out_path is None:
        out_path = esp_path
    rewrite_esp(esp_path, trans_map, out_path)


def cmd_run(esp_path: Path):
    json_path = esp_path.with_suffix('.trans.json')
    print("=== EXPORT ===")
    cmd_export(esp_path, json_path)
    print("\n=== TRANSLATE ===")
    cmd_translate(esp_path, esp_path, esp_path.parent)
    print(f"\nDone. JSON saved at {json_path}")


# ── Standalone CLI ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Nolvus ESP Translation Engine')
    sub = parser.add_subparsers(dest='cmd', required=True)

    p_inspect = sub.add_parser('inspect')
    p_inspect.add_argument('esp')

    p_export = sub.add_parser('export')
    p_export.add_argument('esp')
    p_export.add_argument('json', nargs='?')

    p_trans = sub.add_parser('translate')
    p_trans.add_argument('esp')
    p_trans.add_argument('json', nargs='?')

    p_apply = sub.add_parser('apply')
    p_apply.add_argument('esp')
    p_apply.add_argument('json', nargs='?')
    p_apply.add_argument('--out')

    p_run = sub.add_parser('run')
    p_run.add_argument('esp')

    args     = parser.parse_args()
    esp_path = Path(args.esp)
    if not esp_path.exists():
        print(f"ERROR: {esp_path} not found"); sys.exit(1)

    if args.cmd == 'inspect':
        cmd_inspect(esp_path)
    elif args.cmd == 'export':
        out = Path(args.json) if args.json else esp_path.with_suffix('.trans.json')
        cmd_export(esp_path, out)
    elif args.cmd == 'translate':
        cmd_translate(esp_path, esp_path, esp_path.parent)
    elif args.cmd == 'apply':
        jp  = Path(args.json) if args.json else esp_path.with_suffix('.trans.json')
        out = Path(args.out) if getattr(args, 'out', None) else None
        cmd_apply(esp_path, jp, out)
    elif args.cmd == 'run':
        cmd_run(esp_path)


if __name__ == '__main__':
    main()
