"""
Translate MCM interface translation files from English to Russian.

Standalone usage:
  python translate_mcm.py --list
  python translate_mcm.py --mod "SunHelm"
  python translate_mcm.py --dry-run
  python translate_mcm.py

Programmatic usage via translator.cli:
  from scripts.translate_mcm import cmd_translate_mcm
  cmd_translate_mcm(mod_folder, dry_run=False)
"""

import argparse
import subprocess
import json
import re
import shutil
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')


def _get_cfg():
    from translator.config import get_config
    return get_config()


def _paths():
    return _get_cfg().paths


# ── file I/O ──────────────────────────────────────────────────────────────────

def read_trans_file(path: Path):
    """Return ([(key, value), ...], bom_bytes). Handles UTF-16 LE/BE and UTF-8."""
    raw = path.read_bytes()
    if raw[:2] == b'\xff\xfe':
        text, bom = raw[2:].decode('utf-16-le'), b'\xff\xfe'
    elif raw[:2] == b'\xfe\xff':
        text, bom = raw[2:].decode('utf-16-be'), b'\xff\xfe'
    else:
        text, bom = raw.decode('utf-8-sig', errors='replace'), b'\xff\xfe'

    pairs = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if '\t' in line:
            k, _, v = line.partition('\t')
            pairs.append((k.strip(), v.strip()))
        else:
            pairs.append((line, ''))
    return pairs, bom


def backup_if_exists(path: Path):
    paths = _paths()
    if path.exists():
        try:
            rel = path.relative_to(paths.mods_dir)
        except ValueError:
            rel = Path(path.name)
        dest = paths.backup_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)


def write_trans_file(path: Path, pairs, bom=b'\xff\xfe', dry_run=False):
    if dry_run:
        return
    backup_if_exists(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines   = [f"{k}\t{v}" if v else k for k, v in pairs]
    content = '\r\n'.join(lines) + '\r\n'
    path.write_bytes(bom + content.encode('utf-16-le'))


# ── translation ───────────────────────────────────────────────────────────────

def needs_translation(value: str) -> bool:
    if not value:
        return False
    if re.match(r'^[\d\s.,\-+%]+$', value):
        return False
    return bool(re.search(r'[a-zA-Z]', value))


def translate_batch(texts: list, context: str = '') -> list:
    if not texts:
        return []
    try:
        from translator.pipeline import translate_batch as _tb
        return _tb(texts, context)
    except Exception as e:
        print(f"    [WARN] translate_batch error: {e}, keeping originals")
        return list(texts)


# ── BSA extraction ────────────────────────────────────────────────────────────

_unpacked_bsas: dict = {}


def get_from_bsa(bsa_path: str, file_in_bsa: str) -> Path:
    paths = _paths()
    if bsa_path not in _unpacked_bsas:
        extract_dir = paths.temp_dir / Path(bsa_path).stem
        extract_dir.mkdir(parents=True, exist_ok=True)
        try:
            r = subprocess.run(
                [str(paths.bsarch_exe), 'unpack', bsa_path, str(extract_dir), '-q', '-mt'],
                capture_output=True, text=True, timeout=300
            )
        except subprocess.TimeoutExpired:
            print("    [WARN] BSArch timed out")
            return None
        except Exception as e:
            print(f"    [WARN] BSArch error: {e}")
            return None
        if r.returncode != 0:
            print(f"    [WARN] BSArch failed: {r.stderr[:120]}")
            return None
        _unpacked_bsas[bsa_path] = extract_dir

    extract_dir = _unpacked_bsas[bsa_path]
    name = Path(file_in_bsa.replace('\\', '/')).name
    matches = list(extract_dir.rglob(name))
    return matches[0] if matches else None


def repack_bsa(bsa_path: str, extract_dir: Path):
    """Repack a modified BSA (call after translating files extracted from it)."""
    paths = _paths()
    bsa = Path(bsa_path)
    # Store backup under backup_dir / <mod_relative_path> (same structure as ESP)
    try:
        rel = bsa.relative_to(paths.mods_dir)
    except ValueError:
        rel = Path(bsa.name)
    backup_dest = paths.backup_dir / rel
    if not backup_dest.exists():
        backup_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(bsa, backup_dest)
        print(f"    Backed up BSA to {backup_dest}")
    try:
        r = subprocess.run(
            [str(paths.bsarch_exe), 'pack', str(extract_dir), bsa_path, '-sse', '-mt'],
            capture_output=True, text=True, timeout=600
        )
        if r.returncode != 0:
            print(f"    [WARN] BSArch repack failed: {r.stderr[:120]}")
        else:
            print(f"    Repacked BSA: {bsa.name}")
    except Exception as e:
        print(f"    [WARN] BSArch repack error: {e}")


# ── core translation of one file ──────────────────────────────────────────────

def translate_one(en_path: Path, ru_path: Path, context: str = '', dry_run=False) -> bool:
    try:
        pairs, bom = read_trans_file(en_path)
    except Exception as e:
        print(f"    [ERROR] cannot read source: {e}")
        return False

    cfg = _get_cfg()
    batch_size = cfg.ensemble.model_a.batch_size

    indices, texts = [], []
    for i, (k, v) in enumerate(pairs):
        if needs_translation(v):
            indices.append(i)
            texts.append(v)

    print(f"    {len(pairs)} entries, {len(texts)} need translation")

    if not texts:
        try:
            write_trans_file(ru_path, pairs, bom, dry_run)
            print(f"    {'[DRY] would write' if dry_run else 'Written'} (no translation needed)")
        except Exception as e:
            print(f"    [ERROR] write failed: {e}")
            return False
        return True

    translated_all = []
    for batch_start in range(0, len(texts), batch_size):
        batch = texts[batch_start:batch_start + batch_size]
        n     = batch_start // batch_size + 1
        total = (len(texts) + batch_size - 1) // batch_size
        print(f"    batch {n}/{total} ({len(batch)} strings)...")
        if dry_run:
            translated_all.extend(batch)
        else:
            try:
                translated_all.extend(translate_batch(batch, context))
            except Exception as e:
                print(f"    [ERROR] translate_batch: {e}, keeping originals")
                translated_all.extend(batch)

    result = list(pairs)
    for idx, translated in zip(indices, translated_all):
        k, _ = result[idx]
        result[idx] = (k, translated)

    try:
        write_trans_file(ru_path, result, bom, dry_run)
        if dry_run:
            print(f"    [DRY] would write: {ru_path}")
    except Exception as e:
        print(f"    [ERROR] write failed: {e}")
        return False

    return True


# ── cmd_translate_mcm (called by translator.cli) ──────────────────────────────

def cmd_translate_mcm(mod_folder: Path, dry_run: bool = False):
    """
    Scan mod_folder for MCM translation files and translate them.
    Handles both loose files and BSA-embedded files.
    """
    paths = _paths()

    # Get mod context
    context = ''
    try:
        from translator.pipeline import get_mod_context
        context = get_mod_context(mod_folder)
    except Exception:
        pass

    # Look for loose MCM files: interface/translations/*_english.txt
    loose_en = list(mod_folder.rglob("interface/translations/*_english.txt"))
    bsa_files = list(mod_folder.glob("*.bsa"))

    if not loose_en and not bsa_files:
        return

    print(f"\nMCM: {mod_folder.name}")

    # Process loose files
    for en_path in loose_en:
        mod_name = en_path.stem.replace('_english', '')
        ru_path  = en_path.parent / f"{mod_name}_russian.txt"
        print(f"  Loose: {en_path.name}")
        translate_one(en_path, ru_path, context=context, dry_run=dry_run)

    # Process BSA files
    for bsa in bsa_files:
        # Extract, look for english MCM files, translate, repack
        extract_dir = paths.temp_dir / bsa.stem
        extract_dir.mkdir(parents=True, exist_ok=True)
        try:
            r = subprocess.run(
                [str(paths.bsarch_exe), 'unpack', str(bsa), str(extract_dir), '-q', '-mt'],
                capture_output=True, text=True, timeout=300
            )
            if r.returncode != 0:
                print(f"  [WARN] BSArch unpack failed: {bsa.name}")
                continue
        except Exception as e:
            print(f"  [WARN] BSArch error: {e}")
            continue

        bsa_en_files = list(extract_dir.rglob("*_english.txt"))
        if not bsa_en_files:
            shutil.rmtree(extract_dir, ignore_errors=True)
            continue

        any_changed = False
        for en_path in bsa_en_files:
            mod_name = en_path.stem.replace('_english', '')
            ru_path  = en_path.parent / f"{mod_name}_russian.txt"
            print(f"  BSA [{bsa.name}]: {en_path.name}")
            ok = translate_one(en_path, ru_path, context=context, dry_run=dry_run)
            if ok and not dry_run:
                any_changed = True

        if any_changed:
            # Repack BSA
            repack_bsa(str(bsa), extract_dir)

        shutil.rmtree(extract_dir, ignore_errors=True)


# ── progress tracking (for standalone batch mode) ─────────────────────────────

def load_progress(progress_path: Path) -> set:
    try:
        if progress_path.exists():
            return set(json.loads(progress_path.read_text('utf-8')))
    except Exception:
        pass
    return set()


def save_progress(done: set, progress_path: Path):
    try:
        progress_path.write_text(json.dumps(sorted(done), indent=2), encoding='utf-8')
    except Exception as e:
        print(f"    [WARN] could not save progress: {e}")


def load_items(missing_path: Path, mod_filter=None):
    try:
        items = json.loads(missing_path.read_text('utf-8'))
    except Exception as e:
        print(f"[ERROR] Cannot read {missing_path}: {e}")
        return []

    seen, deduped = set(), []
    for item in items:
        key = item['ru_path'].lower().replace('\\', '/')
        if key not in seen:
            seen.add(key)
            deduped.append(item)

    if mod_filter:
        f = mod_filter.lower()
        deduped = [x for x in deduped if f in x['mod'].lower()]
        print(f"Filter '{mod_filter}' matches {len(deduped)} item(s)")

    return deduped


# ── standalone CLI ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Translate Nolvus MCM files to Russian')
    parser.add_argument('--list',    action='store_true')
    parser.add_argument('--mod',     metavar='NAME')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    paths       = _paths()
    missing     = paths.mods_dir.parent / "Scripts" / "missing_translations.json"
    progress_f  = paths.mods_dir.parent / "Scripts" / "translation_progress.json"

    items = load_items(missing, args.mod)

    if args.list or not items:
        done = load_progress(progress_f)
        print(f"\n{'MOD':<50} {'FILE':<40} {'STATUS'}")
        print('-' * 100)
        for item in load_items(missing):
            status = 'DONE' if item['ru_path'].lower() in done else 'PENDING'
            fname  = Path(item['ru_path']).name
            print(f"  {item['mod']:<48} {fname:<40} {status}")
        return

    done = load_progress(progress_f)
    remaining = [x for x in items if x['ru_path'].lower() not in done]

    if args.dry_run:
        print(f"[DRY RUN] Would translate {len(remaining)} file(s)\n")
    else:
        print(f"Pending: {len(remaining)}")

    paths.temp_dir.mkdir(parents=True, exist_ok=True)
    paths.backup_dir.mkdir(parents=True, exist_ok=True)

    bsa_mod_folders: set[str] = set()

    try:
        for i, item in enumerate(remaining, 1):
            ru_path = Path(item['ru_path'])
            print(f"\n[{i}/{len(remaining)}] {item['mod']}")
            print(f"  -> {ru_path.name}")

            context = ''
            try:
                from translator.pipeline import get_mod_context
                mod_folder = Path(item.get('mod_folder', ru_path.parent))
                context = get_mod_context(mod_folder)
            except Exception:
                pass

            try:
                if item['type'] == 'loose':
                    en_path = Path(item['en_path'])
                    if not en_path.exists():
                        print("    SKIP: source not found")
                        continue
                    ok = translate_one(en_path, ru_path, context=context, dry_run=args.dry_run)

                elif item['type'] == 'bsa':
                    print(f"    Extract from: {Path(item['bsa']).name}")
                    extracted = get_from_bsa(item['bsa'], item['en_file_in_archive'])
                    if not extracted:
                        print("    SKIP: BSA extraction failed")
                        continue
                    ok = translate_one(extracted, ru_path, context=context, dry_run=args.dry_run)
                    if ok and not args.dry_run:
                        bsa_mod_folders.add(item['bsa'])
                else:
                    continue

            except KeyboardInterrupt:
                print("\n\nInterrupted. Progress saved.")
                break
            except Exception as e:
                print(f"    [ERROR] unexpected: {e}")
                ok = False

            if ok and not args.dry_run:
                done.add(item['ru_path'].lower())
                save_progress(done, progress_f)

    finally:
        # Repack any modified BSAs
        for bsa_path in bsa_mod_folders:
            extract_dir = _unpacked_bsas.get(bsa_path)
            if extract_dir:
                repack_bsa(bsa_path, extract_dir)

        if not args.dry_run and paths.temp_dir.exists():
            try:
                shutil.rmtree(paths.temp_dir)
            except Exception:
                pass

    print("\nDone.")


if __name__ == '__main__':
    main()
