"""
Full core validation test for Nolvus Translator.
Tests all components against: A Cat's Life mod (ACatsLife.esp)
Run with: venv\Scripts\python test_core.py
"""
import sys, os, json, time, shutil, traceback
from pathlib import Path

ROOT    = Path(__file__).parent
# Set TEST_MOD_DIR env var to point at a mod folder, or use config.yaml mods_dir
_default_mod = os.getenv("TEST_MOD_DIR", "")
if not _default_mod:
    try:
        sys.path.insert(0, str(ROOT))
        from translator.config import load_config as _lc
        _default_mod = str(_lc().paths.mods_dir / "A Cat's Life")
    except Exception:
        _default_mod = "."
MOD_DIR = Path(_default_mod)
ESP     = MOD_DIR / "ACatsLife.esp"
BSA     = MOD_DIR / "ACatsLife.bsa"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

PASS = "[PASS]"
FAIL = "[FAIL]"
SKIP = "[SKIP]"

results = []

def run_test(name, fn):
    print(f"\n{'='*60}")
    print(f"TEST: {name}")
    print('='*60)
    t0 = time.time()
    try:
        fn()
        elapsed = time.time() - t0
        print(f"  --> {PASS}  ({elapsed:.2f}s)")
        results.append((name, "PASS", elapsed))
    except Exception as e:
        elapsed = time.time() - t0
        traceback.print_exc()
        print(f"  --> {FAIL}: {e}  ({elapsed:.2f}s)")
        results.append((name, f"FAIL: {e}", elapsed))


# ─────────────────────────────────────────────────────────────────────────────
# TEST 1 — Config loading
# ─────────────────────────────────────────────────────────────────────────────
def test_config():
    from translator.config import load_config
    cfg = load_config()
    assert cfg.paths.mods_dir.exists(), f"mods_dir not found: {cfg.paths.mods_dir}"
    assert cfg.paths.bsarch_exe.exists(), f"bsarch not found: {cfg.paths.bsarch_exe}"
    print(f"  mods_dir    : {cfg.paths.mods_dir}")
    print(f"  backup_dir  : {cfg.paths.backup_dir}")
    print(f"  bsarch_exe  : {cfg.paths.bsarch_exe}")
    print(f"  model_cache : {cfg.paths.model_cache_dir}")
    print(f"  nexus_key   : {'SET' if cfg.nexus.api_key and cfg.nexus.api_key != 'YOUR_NEXUS_API_KEY_HERE' else 'NOT SET'}")
    return cfg

run_test("Config loading", test_config)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 2 — ESP: inspect raw structure
# ─────────────────────────────────────────────────────────────────────────────
def test_esp_inspect():
    import struct
    with open(ESP, "rb") as f:
        data = f.read()
    magic = data[:4]
    assert magic == b"TES4", f"Bad magic: {magic}"
    data_size = struct.unpack_from("<I", data, 4)[0]
    flags     = struct.unpack_from("<I", data, 8)[0]
    form_id   = struct.unpack_from("<I", data, 12)[0]
    print(f"  File size   : {len(data):,} bytes")
    print(f"  TES4 data   : {data_size} bytes")
    print(f"  Flags       : 0x{flags:08x}")
    print(f"  Localized   : {bool(flags & 0x80)}")
    print(f"  FormID      : 0x{form_id:08X}")

run_test("ESP raw structure", test_esp_inspect)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 3 — ESP string extraction
# ─────────────────────────────────────────────────────────────────────────────
_esp_strings = []

def test_esp_extract():
    from scripts.esp_engine import extract_all_strings
    global _esp_strings

    # extract_all_strings returns (list[dict], is_localized: bool)
    result = extract_all_strings(ESP)
    string_list, is_localized = result
    _esp_strings = string_list

    print(f"  Total strings : {len(string_list)}")
    print(f"  Is localized  : {is_localized}")
    from collections import Counter
    by_field = Counter()
    by_type  = Counter()
    for s in string_list:
        by_type[s.get("rec_type", "?")]   += 1
        by_field[s.get("field_type", "?")] += 1
    print(f"  By record type: {dict(by_type.most_common(6))}")
    print(f"  By field:       {dict(by_field.most_common(6))}")
    print("  First 5 strings:")
    for s in string_list[:5]:
        fid   = s.get("form_id", "?")
        field = s.get("field_type", "?")
        text  = s.get("text", "")
        print(f"    [{fid}][{field}] => {repr(text[:70])}")

run_test("ESP string extraction", test_esp_extract)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 4 — ESP dry-run translate (no models — just extract+cache write)
# ─────────────────────────────────────────────────────────────────────────────
def test_esp_dry_run():
    from scripts.esp_engine import cmd_translate
    print("  Running cmd_translate dry_run=True ...")
    cmd_translate(ESP, ESP, MOD_DIR, dry_run=True)
    cache = ROOT / "cache/translation_cache.json"
    if cache.exists():
        data  = json.loads(cache.read_text(encoding="utf-8"))
        n_esp = sum(len(v) for v in data.values())
        print(f"  Cache entries after dry-run: {n_esp}")
    else:
        print("  No cache file written (dry_run skips write)")

run_test("ESP dry-run translate", test_esp_dry_run)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 5 — BSA detection
# ─────────────────────────────────────────────────────────────────────────────
def test_bsa_detect():
    assert BSA.exists(), f"BSA not found: {BSA}"
    size = BSA.stat().st_size
    with open(BSA, "rb") as f:
        magic = f.read(4)
    assert magic == b"BSA\x00", f"Bad BSA magic: {magic}"
    print(f"  BSA file    : {BSA.name}")
    print(f"  Size        : {size:,} bytes ({size//1024//1024} MB)")
    print(f"  Magic       : OK (BSA\\x00)")

run_test("BSA file detection", test_bsa_detect)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 6 — BSA unpack (checks bsarch, unpacks to temp)
# ─────────────────────────────────────────────────────────────────────────────
_bsa_temp = ROOT / "cache/test_bsa_unpack"

def test_bsa_unpack():
    from translator.config import load_config
    cfg = load_config()
    bsarch = cfg.paths.bsarch_exe
    assert bsarch.exists(), f"BSArch not found: {bsarch}"

    temp = _bsa_temp
    if temp.exists():
        shutil.rmtree(str(temp))
    temp.mkdir(parents=True, exist_ok=True)

    import subprocess
    result = subprocess.run(
        [str(bsarch), "unpack", str(BSA), str(temp), "-q", "-mt"],
        capture_output=True, text=True, timeout=60
    )
    print(f"  Return code : {result.returncode}")
    if result.stdout.strip(): print(f"  stdout      : {result.stdout.strip()[:200]}")
    if result.stderr.strip(): print(f"  stderr      : {result.stderr.strip()[:200]}")

    files = list(temp.rglob("*"))
    total_size = sum(f.stat().st_size for f in files if f.is_file())
    print(f"  Files       : {len([f for f in files if f.is_file()])}")
    print(f"  Total size  : {total_size:,} bytes")

    # Look for MCM translation files
    mcm_files = list(temp.rglob("*_english.txt")) + list(temp.rglob("*_russian.txt"))
    print(f"  MCM files   : {len(mcm_files)}")
    for f in mcm_files[:5]:
        print(f"    {f.relative_to(temp)}")

    assert result.returncode == 0, f"BSArch failed: {result.stderr}"

run_test("BSA unpack via BSArch", test_bsa_unpack)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 7 — MCM translation file scan
# ─────────────────────────────────────────────────────────────────────────────
def test_mcm_scan():
    from scripts.translate_mcm import cmd_translate_mcm
    print("  Running MCM scan (dry_run=True) ...")
    cmd_translate_mcm(MOD_DIR, dry_run=True)
    print("  MCM dry-run complete")
    # Also check inside unpacked BSA
    if _bsa_temp.exists():
        txts = list(_bsa_temp.rglob("*_english.txt"))
        print(f"  English MCM files in BSA: {len(txts)}")
        for t in txts[:3]:
            lines = t.read_text(encoding="utf-16-le", errors="replace").splitlines()
            translatable = [l for l in lines if "\t" in l and not l.startswith("#")]
            print(f"    {t.name}: {len(translatable)} translatable lines")

run_test("MCM file scan", test_mcm_scan)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 8 — ESP context extractor (lightweight EDID scan)
# ─────────────────────────────────────────────────────────────────────────────
def test_esp_context():
    from translator.context.esp_context import EspContextExtractor
    extractor = EspContextExtractor(ESP)
    records   = extractor.all_records()
    print(f"  Records in context: {len(records)}")
    for i, (fid, info) in enumerate(list(records.items())[:5]):
        print(f"    0x{fid:08X}: {info}")
    return records

run_test("ESP context extractor (EDID map)", test_esp_context)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 9 — Context builder (no Nexus needed)
# ─────────────────────────────────────────────────────────────────────────────
def test_context_builder():
    from translator.context.builder import ContextBuilder
    builder = ContextBuilder()
    ctx = builder.get_mod_context(MOD_DIR)
    print(f"  Context string ({len(ctx)} chars):")
    print(f"    {repr(ctx[:200])}")

run_test("Context builder", test_context_builder)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 10 — Nexus fetcher (API key required — may skip)
# ─────────────────────────────────────────────────────────────────────────────
def test_nexus_fetch():
    from translator.config import load_config
    from translator.context.nexus_fetcher import NexusFetcher
    cfg = load_config()
    if not cfg.nexus.api_key or cfg.nexus.api_key == "YOUR_NEXUS_API_KEY_HERE":
        print("  SKIPPED — Nexus API key not set in config.yaml")
        return
    fetcher = NexusFetcher()
    print("  Fetching mod #37250 (A Cat's Life) from Nexus...")
    desc = fetcher.fetch_mod_description(MOD_DIR) or ""
    print(f"  Description ({len(desc)} chars): {repr(desc[:200])}")

run_test("Nexus Mods API fetch", test_nexus_fetch)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 11 — BART summarizer (CPU, ~500 MB model download on first run)
# ─────────────────────────────────────────────────────────────────────────────
def test_summarizer():
    try:
        from translator.context.summarizer import NeuralSummarizer
    except ImportError as e:
        print(f"  SKIPPED — {e}")
        return

    sample = (
        "A Cat's Life adds a fully voiced female khajiit companion named Tsanji. "
        "She is found in the Sleeping Giant Inn in Riverwood. Tsanji has custom "
        "dialogue, a personal quest, and over 600 lines of voice acting. She levels "
        "with the player and can be married. The mod also adds a custom home for her."
    )
    print(f"  Input ({len(sample)} chars): {sample[:80]}...")
    summarizer = NeuralSummarizer()
    summary = summarizer.summarize(sample)
    print(f"  Summary ({len(summary)} chars): {repr(summary)}")

run_test("BART neural summarizer", test_summarizer)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 12 — GPU detection
# ─────────────────────────────────────────────────────────────────────────────
def test_gpu():
    import torch
    print(f"  torch       : {torch.__version__}")
    print(f"  cuda avail  : {torch.cuda.is_available()}")
    if torch.cuda.is_available():
        dev   = torch.cuda.current_device()
        props = torch.cuda.get_device_properties(dev)
        total = props.total_memory // 1024 // 1024
        print(f"  GPU         : {torch.cuda.get_device_name(dev)}")
        print(f"  VRAM        : {total} MB")
        print(f"  Compute cap : {props.major}.{props.minor}")
        # Quick CUDA sanity: create a tensor on GPU
        t = torch.ones(100, 100, device="cuda")
        print(f"  CUDA tensor : {t.shape} on {t.device} - OK")
        del t
        torch.cuda.empty_cache()
    else:
        print("  CUDA not available — neural translation will be CPU-only")

run_test("GPU / CUDA detection", test_gpu)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 13 — Similarity (Jaccard)
# ─────────────────────────────────────────────────────────────────────────────
def test_similarity():
    from translator.ensemble.similarity import jaccard_similarity
    pairs = [
        ("Кошачья жизнь",  "Кошачья жизнь",    1.0),   # identical
        ("Кот спит",       "Кот не спит",       None),  # similar
        ("Кот",            "Совсем другое",     None),  # different
        ("",               "",                  1.0),   # empty
    ]
    print("  Jaccard similarity tests:")
    for a, b, expected in pairs:
        score = jaccard_similarity(a, b)
        status = "OK" if expected is None or abs(score - expected) < 0.01 else "MISMATCH"
        print(f"    [{status}] {repr(a)[:20]} vs {repr(b)[:20]}  => {score:.3f}")

run_test("Jaccard similarity", test_similarity)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 14 — Prompt builder
# ─────────────────────────────────────────────────────────────────────────────
def test_prompt_builder():
    from translator.prompt.builder import build_prompt, build_arbiter_prompt
    context  = "Mod: A Cat's Life — khajiit companion mod | Record: [NPC_] EDID: Tsanji"
    texts    = ["A hungry cat", "You look tired, traveler", "Sleeping Giant Inn"]
    prompt_hymt = build_prompt(texts, "en", "ru", context=context, model_type="hymt")
    prompt_qwen = build_prompt(texts, "en", "ru", context=context, model_type="qwen")
    cands_a  = ["Голодная кошка", "Ты выглядишь усталым", "Гостиница Спящего гиганта"]
    cands_b  = ["Голодный кот",   "Устало выглядишь",     "Спящий великан"]
    prompt_arb = build_arbiter_prompt(texts, cands_a, cands_b, "en", "ru", context=context)
    print(f"  HY-MT prompt : {len(prompt_hymt)} chars")
    print(f"  Qwen prompt  : {len(prompt_qwen)} chars")
    print(f"  Arbiter      : {len(prompt_arb)} chars")
    print(f"  Preview HY-MT: {repr(prompt_hymt[:120])}")

run_test("Prompt builder", test_prompt_builder)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 15 — Prompt parser
# ─────────────────────────────────────────────────────────────────────────────
def test_prompt_parser():
    from translator.prompt.parser import parse_numbered_output
    raw = """1. Голодная кошка
2. Ты выглядишь усталым, путник
3. Гостиница Спящего гиганта"""
    parsed = parse_numbered_output(raw, expected=3)
    print(f"  Parsed {len(parsed)} strings:")
    for i, s in enumerate(parsed):
        print(f"    [{i+1}] {repr(s)}")
    assert len(parsed) == 3

run_test("Prompt parser", test_prompt_parser)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 16 — Web scanner (mod detection)
# ─────────────────────────────────────────────────────────────────────────────
def test_web_scanner():
    from translator.config import load_config
    from translator.web.mod_scanner import ModScanner
    cfg     = load_config()
    scanner = ModScanner(cfg.paths.mods_dir, cfg.paths.translation_cache, cfg.paths.nexus_cache)
    mod     = scanner.get_mod("A Cat's Life")
    assert mod is not None, "Mod not found by scanner"
    print(f"  Mod name    : {mod.folder_name}")
    print(f"  ESP files   : {[f.name for f in mod.esp_files]}")
    print(f"  BSA files   : {[f.name for f in mod.bsa_files]}")
    print(f"  Status      : {mod.status}")
    print(f"  Nexus ID    : {mod.nexus_mod_id}")
    print(f"  Is localized: {any(f.is_localized for f in mod.esp_files)}")

run_test("Web scanner — mod detection", test_web_scanner)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 17 — Backup & restore
# ─────────────────────────────────────────────────────────────────────────────
def test_backup_restore():
    from translator.config import load_config
    import shutil, time
    cfg        = load_config()
    backup_dir = cfg.paths.backup_dir
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts         = time.strftime("%Y%m%d_%H%M%S")
    dest       = backup_dir / f"A Cat's Life__{ts}__test"
    print(f"  Creating backup: {dest.name}")
    shutil.copytree(str(MOD_DIR), str(dest))
    assert dest.exists()
    backed_files = list(dest.rglob("*"))
    print(f"  Backed up {len(backed_files)} items")
    # Verify contents match
    orig_files = {f.name for f in MOD_DIR.rglob("*")}
    back_files = {f.name for f in dest.rglob("*")}
    assert orig_files == back_files, "Backup contents differ!"
    print(f"  Contents match: OK")
    # Cleanup test backup
    shutil.rmtree(str(dest))
    print(f"  Cleanup: done")

run_test("Backup & restore", test_backup_restore)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 18 — Translation cache read/write
# ─────────────────────────────────────────────────────────────────────────────
def test_translation_cache():
    from translator.config import load_config
    cfg        = load_config()
    cache_path = cfg.paths.translation_cache
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    # Write a test entry
    test_key  = "('00001000', 'NPC_', 'FULL', 0)"
    test_val  = "Тсандж"  # test Cyrillic
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    cache.setdefault("ACatsLife", {})[test_key] = test_val
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")

    # Read back
    cache2 = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cache2["ACatsLife"][test_key] == test_val
    print(f"  Written and read back: {test_key} => {repr(test_val)}")
    print(f"  Cache file: {cache_path}")
    print(f"  Total entries: {sum(len(v) for v in cache2.values())}")

run_test("Translation cache read/write", test_translation_cache)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 19 — Model path resolution (no download)
# ─────────────────────────────────────────────────────────────────────────────
def test_model_resolution():
    import translator.config as cfg_mod
    cfg_mod._config = None
    from translator.config import load_config
    cfg = load_config()
    ens = cfg.ensemble

    for label, mc in [("32B full", ens.model_b), ("14B lite", ens.model_b_lite)]:
        if mc is None:
            print(f"  {label}: NOT CONFIGURED")
            continue
        local = cfg.paths.model_cache_dir / mc.local_dir_name / mc.gguf_filename
        exists = local.exists()
        print(f"  {label}")
        print(f"    repo_id       : {mc.repo_id}")
        print(f"    gguf_filename : {mc.gguf_filename}")
        print(f"    local path    : {local}")
        print(f"    on disk       : {'YES' if exists else 'NO — will download on first use'}")

run_test("Model GGUF resolution", test_model_resolution)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 20 — Web Flask routes
# ─────────────────────────────────────────────────────────────────────────────
def test_flask_routes():
    from translator.web.app import create_app
    app = create_app()
    cli = app.test_client()
    routes = [
        ("/",              200),
        ("/mods/",         200),
        ("/jobs/",         200),
        ("/tools/",        200),
        ("/backups/",      200),
        ("/config/",       200),
        ("/logs/",         200),
        ("/terminology/",  200),
        ("/api/stats",     200),
        ("/api/gpu",       200),
        ("/api/models/status", 200),
    ]
    all_ok = True
    for url, expected in routes:
        r    = cli.get(url)
        ok   = r.status_code == expected
        flag = "OK  " if ok else "FAIL"
        print(f"  [{flag}] {r.status_code} GET {url}")
        if not ok:
            all_ok = False
    assert all_ok, "Some Flask routes failed"

run_test("Flask web routes (all 11)", test_flask_routes)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 21 — Adaptive model routing (config parse + split logic, no model load)
# ─────────────────────────────────────────────────────────────────────────────
def test_adaptive_routing():
    import translator.config as cfg_mod
    cfg_mod._config = None
    from translator.config import load_config
    cfg = load_config()
    ens = cfg.ensemble

    print(f"  adaptive_threshold : {ens.adaptive_threshold}")
    assert ens.model_b_lite is not None, "model_b_lite not configured"
    print(f"  model_b (32B) : {ens.model_b.gguf_filename}")
    print(f"  model_b_lite  : {ens.model_b_lite.gguf_filename}")
    assert "32B" in ens.model_b.repo_id
    assert "14B" in ens.model_b_lite.repo_id

    threshold = ens.adaptive_threshold
    sample = [
        "Cat",
        "A Cat's Life adds a fully voiced khajiit companion",
        "A Cat's Life is a mod that adds Tsanji, a fully voiced female khajiit "
        "companion found in the Sleeping Giant Inn in Riverwood. She has custom "
        "dialogue, a personal quest, and over 600 lines of voice acting.",
    ]
    short = [t for t in sample if len(t) < threshold]
    long  = [t for t in sample if len(t) >= threshold]
    for t in sample:
        tag = "14B" if len(t) < threshold else "32B"
        print(f"    [{tag}] ({len(t):3d} chars) {t[:60]}")
    assert len(short) == 2 and len(long) == 1

run_test("Adaptive routing config + split logic", test_adaptive_routing)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 22 — 32B GGUF load + inference (requires model on disk)
# ─────────────────────────────────────────────────────────────────────────────
def test_32b_inference():
    import translator.config as cfg_mod
    cfg_mod._config = None
    from translator.config import load_config
    cfg = load_config()
    from translator.models.llamacpp_backend import LlamaCppBackend
    import torch

    mc = cfg.ensemble.model_b
    gguf_path = cfg.paths.model_cache_dir / mc.local_dir_name / mc.gguf_filename
    if not gguf_path.exists():
        print(f"  SKIPPED — 32B GGUF not on disk yet: {gguf_path.name}")
        return

    print(f"  Loading: {mc.gguf_filename}")
    backend = LlamaCppBackend(model_cfg=mc)
    backend.load()
    try:
        vram_gb = torch.cuda.memory_allocated() / 1024**3
        print(f"  VRAM after load : {vram_gb:.1f} GB")
        texts = ["A Cat's Life adds a khajiit companion named Tsanji to the Sleeping Giant Inn in Riverwood."]
        result = backend.translate(texts, context="Skyrim companion mod")
        print(f"  Result: {repr(result[0])}")
        assert result and result[0] and result[0] != texts[0]
    finally:
        backend.unload()
        print(f"  VRAM after unload: {torch.cuda.memory_allocated()/1024**3:.1f} GB")

run_test("32B GGUF load + inference", test_32b_inference)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 23 — Full adaptive pipeline (14B + 32B), skips if GGUFs not on disk
# ─────────────────────────────────────────────────────────────────────────────
def test_adaptive_pipeline():
    import translator.config as cfg_mod
    cfg_mod._config = None
    from translator.config import load_config
    cfg = load_config()
    ens = cfg.ensemble

    def gguf_ready(mc):
        return (cfg.paths.model_cache_dir / mc.local_dir_name / mc.gguf_filename).exists()

    if not gguf_ready(ens.model_b) or not gguf_ready(ens.model_b_lite):
        missing = []
        if not gguf_ready(ens.model_b):      missing.append("32B")
        if not gguf_ready(ens.model_b_lite): missing.append("14B")
        print(f"  SKIPPED — GGUFs not ready yet: {', '.join(missing)} still downloading")
        return

    from translator.ensemble.pipeline import EnsemblePipeline
    texts = [
        "Cat", "Sleeping Giant Inn", "Store",
        "Tsanji is a fully voiced female khajiit companion found in the Sleeping Giant Inn. "
        "She has custom dialogue, a personal quest, and can be married.",
        "You look tired, traveler. Come, sit with me a while. The road is long and the nights grow cold.",
    ]
    short = [t for t in texts if len(t) < 200]
    long  = [t for t in texts if len(t) >= 200]
    print(f"  {len(short)} short → 14B,  {len(long)} long → 32B")

    pipeline = EnsemblePipeline()
    res = pipeline.translate(texts, context="A Cat's Life — khajiit companion mod")

    for src, tgt in zip(texts, res):
        tag = "14B" if len(src) < 200 else "32B"
        print(f"  [{tag}] {repr(src[:45]):50s} → {repr(tgt[:55])}")

    assert len(res) == len(texts) and all(res)

run_test("Adaptive pipeline (14B + 32B)", test_adaptive_pipeline)


# ─────────────────────────────────────────────────────────────────────────────
# SUMMARY
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "="*60)
print("TEST SUMMARY")
print("="*60)
passed  = sum(1 for _, s, _ in results if s == "PASS")
failed  = sum(1 for _, s, _ in results if s.startswith("FAIL"))
for name, status, elapsed in results:
    flag = "PASS" if status == "PASS" else "FAIL"
    print(f"  [{flag}]  {name:40s}  {elapsed:.2f}s")
    if status.startswith("FAIL"):
        print(f"         {status[5:]}")
print()
print(f"  {passed}/{len(results)} tests passed")
if failed:
    print(f"  {failed} FAILED")

# Cleanup temp BSA unpack
if _bsa_temp.exists():
    shutil.rmtree(str(_bsa_temp))
    print("  BSA temp cleaned up")
