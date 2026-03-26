"""Single-mod translation — upload a ZIP, translate strings, download result."""
from __future__ import annotations
import io
import logging
import shutil
import time
import uuid
import zipfile
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, send_file

bp = Blueprint("single_mod", __name__, url_prefix="/api/single-mod")
log = logging.getLogger(__name__)

ESP_EXTS = {".esp", ".esm", ".esl"}


def _sessions() -> dict:
    return current_app.config.setdefault("SINGLE_MOD_SESSIONS", {})


def _get_session(session_id: str) -> dict | None:
    s = _sessions().get(session_id)
    if s:
        s["last_access"] = time.time()
    return s


# ── Upload ─────────────────────────────────────────────────────────────────────

@bp.route("/upload", methods=["POST"])
def upload():
    """Accept a mod ZIP, extract it, parse ESP strings → SQLite, return session_id."""
    cfg  = current_app.config.get("TRANSLATOR_CFG")
    repo = current_app.config.get("STRING_REPO")
    if not cfg or not repo:
        return jsonify({"error": "Server not configured"}), 500

    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "No file uploaded"}), 400
    if not f.filename.lower().endswith(".zip"):
        return jsonify({"error": "Only ZIP files are supported"}), 400

    session_id = str(uuid.uuid4())
    mod_name   = f"__single__{session_id}"
    base       = cfg.paths.temp_dir / "single_mode" / session_id
    source_dir = base / "source"
    output_dir = base / "output"
    source_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    original_zip_name = f.filename
    try:
        zip_bytes = f.read()
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            zf.extractall(source_dir)
            esp_rels = [m.filename for m in zf.infolist()
                        if Path(m.filename).suffix.lower() in ESP_EXTS
                        and not m.filename.endswith("/")]
    except Exception as exc:
        shutil.rmtree(str(base), ignore_errors=True)
        return jsonify({"error": f"Failed to extract ZIP: {exc}"}), 400

    if not esp_rels:
        shutil.rmtree(str(base), ignore_errors=True)
        return jsonify({"error": "No ESP/ESM/ESL files found in ZIP"}), 400

    from scripts.esp_engine import extract_all_strings, needs_translation as _nt

    total_strings = 0
    esp_summaries = []
    for rel in esp_rels:
        esp_path = source_dir / rel
        if not esp_path.exists():
            continue
        esp_name = Path(rel).name
        try:
            raw_strings, _ = extract_all_strings(esp_path)
        except Exception as exc:
            log.warning("single upload: failed to parse %s: %s", esp_name, exc)
            esp_summaries.append({"esp": esp_name, "count": 0, "error": str(exc)})
            continue

        strings = []
        for s in raw_strings:
            text = s.get("text", "")
            if not _nt(text):
                s = {**s, "translation": text, "status": "translated", "quality_score": 100}
            strings.append(s)

        count = repo.bulk_insert_strings(mod_name, esp_name, strings)
        total_strings += count
        esp_summaries.append({"esp": esp_name, "count": count})

    _sessions()[session_id] = {
        "dir":               str(base),
        "source_dir":        str(source_dir),
        "output_dir":        str(output_dir),
        "esp_rels":          esp_rels,
        "original_zip_name": original_zip_name,
        "mod_name":          mod_name,
        "created_at":        time.time(),
        "last_access":       time.time(),
    }

    return jsonify({
        "ok":         True,
        "session_id": session_id,
        "mod_name":   mod_name,
        "esp_files":  esp_summaries,
        "total":      total_strings,
        "zip_name":   original_zip_name,
    })


# ── Strings ────────────────────────────────────────────────────────────────────

@bp.route("/<session_id>/strings")
def get_strings(session_id: str):
    """Paginated strings — same shape as /mods/<name>/strings."""
    s = _get_session(session_id)
    if not s:
        return jsonify({"error": "Session not found"}), 404
    repo = current_app.config.get("STRING_REPO")
    if not repo:
        return jsonify({"error": "No DB"}), 500

    mod_name = s["mod_name"]
    esp_name = request.args.get("esp") or None
    status   = request.args.get("status") or None
    q        = request.args.get("q") or None
    scope    = request.args.get("scope") or None
    limit    = int(request.args.get("limit", 100))
    offset   = int(request.args.get("offset", 0))

    rows, total = repo.get_strings(
        mod_name, esp_name=esp_name, status=status, q=q,
        scope=scope, limit=limit, offset=offset,
    )
    return jsonify({"strings": rows, "total": total})


# ── Manual edit ────────────────────────────────────────────────────────────────

@bp.route("/<session_id>/strings/update", methods=["POST"])
def update_string(session_id: str):
    s = _get_session(session_id)
    if not s:
        return jsonify({"error": "Session not found"}), 404
    cfg  = current_app.config.get("TRANSLATOR_CFG")
    repo = current_app.config.get("STRING_REPO")

    data        = request.get_json() or {}
    key_str     = data.get("key", "")
    esp_name    = data.get("esp", "")
    translation = data.get("translation", "")

    from translator.web.workers import save_translation
    cache_path = cfg.paths.translation_cache if cfg else Path("cache/translation_cache.json")
    qs, status = save_translation(
        Path(s["source_dir"]).parent,
        s["mod_name"],
        cache_path,
        esp_name, key_str, translation,
        cfg=cfg, repo=repo,
    )
    return jsonify({"ok": True, "quality_score": qs, "status": status})


# ── Translate-one (AI) ─────────────────────────────────────────────────────────

@bp.route("/<session_id>/strings/translate-one", methods=["POST"])
def translate_one(session_id: str):
    """Synchronously translate a single string via AI (pull-mode worker)."""
    s = _get_session(session_id)
    if not s:
        return jsonify({"error": "Session not found"}), 404

    cfg  = current_app.config.get("TRANSLATOR_CFG")
    repo = current_app.config.get("STRING_REPO")
    if not cfg:
        return jsonify({"ok": False, "error": "No config"}), 500

    data     = request.get_json() or {}
    key_str  = data.get("key", "")
    esp_name = data.get("esp", "")
    original = data.get("original", "")
    force_ai = data.get("force_ai", False)
    mod_name = s["mod_name"]

    from translator.models.inference_params import InferenceParams
    params = InferenceParams.from_dict(data.get("params") or {})

    if not original or original.startswith("[LOC:"):
        return jsonify({"ok": False, "error": "Cannot translate this string"}), 400

    from scripts.esp_engine import needs_translation as _nt
    if not _nt(original):
        from translator.web.workers import save_translation
        save_translation(
            Path(s["source_dir"]).parent, mod_name,
            cfg.paths.translation_cache, esp_name, key_str, original,
            cfg=cfg, repo=repo,
        )
        return jsonify({"ok": True, "translation": original,
                        "quality_score": 100, "status": "translated",
                        "source": "untranslatable"})

    xlogs: list[str] = []

    # ── Global dict fast-path ─────────────────────────────────────────────────
    use_gd = getattr(getattr(cfg, "translation", None), "use_global_dict", True) and not force_ai
    if use_gd:
        gd = current_app.config.get("GLOBAL_DICT")
        if gd:
            existing = gd.get(original)
            if existing:
                from scripts.esp_engine import strip_echo
                cleaned = strip_echo(existing)
                if cleaned != existing:
                    gd.add(original, cleaned)
                    gd.save()
                    existing = cleaned
                xlogs.append("source: global dict hit")
                from translator.web.workers import save_translation
                save_translation(
                    Path(s["source_dir"]).parent, mod_name,
                    cfg.paths.translation_cache, esp_name, key_str, existing,
                    cfg=cfg, repo=repo,
                )
                try:
                    jm = current_app.config["JOB_MANAGER"]
                    jm.record_completed_job(
                        name      = f"Translate: {original[:60]}",
                        job_type  = "translate_one",
                        params    = {"mod_name": mod_name, "esp": esp_name, "key": key_str},
                        result    = existing,
                        log_lines = xlogs,
                        string_updates = [{"key": key_str, "esp": esp_name,
                                           "translation": existing,
                                           "status": "translated", "quality_score": None}],
                    )
                except Exception:
                    pass
                return jsonify({"ok": True, "translation": existing,
                                "quality_score": None, "from_dict": True, "logs": xlogs})

    try:
        import time as _time
        from scripts.esp_engine import (prepare_for_ai, restore_from_ai,
                                        compute_string_status as _css)
        from translator.web.workers import save_translation

        xlogs.append(f"input: {original[:100]}")

        # ── Backend selection ─────────────────────────────────────────────────
        data_machines = data.get("machines") or []
        pull_backend  = None
        registry      = current_app.config.get("WORKER_REGISTRY")
        if registry and data_machines:
            from translator.web.pull_backend import RegistryPullBackend
            from translator.web.worker_registry import WorkerRegistry
            src_lang = getattr(getattr(cfg, "translation", None), "source_lang", "English")
            tgt_lang = getattr(getattr(cfg, "translation", None), "target_lang", "Russian")
            for label in data_machines:
                worker = registry.get(label)
                if worker and (_time.time() - worker.last_seen) < WorkerRegistry.HEARTBEAT_TTL:
                    pull_backend = RegistryPullBackend(
                        label=label, registry=registry,
                        source_lang=src_lang, target_lang=tgt_lang)
                    xlogs.append(f"backend: pull-mode [{label}]")
                    break

        if pull_backend is None:
            return jsonify({"ok": False,
                            "error": "No inference workers online. Start a worker server and connect it to this host.",
                            "logs": xlogs}), 503

        # ── Core translation ──────────────────────────────────────────────────
        ai_texts, ai_meta = prepare_for_ai([original])
        masked = ai_texts[0]
        _t0  = _time.monotonic()
        raw  = pull_backend.translate(ai_texts, context="", params=params)
        _elapsed = _time.monotonic() - _t0
        trans_list = restore_from_ai(raw, ai_meta)
        trans = trans_list[0] if trans_list else ""
        qs, _tok_ok, tok_issues, status = _css(original, trans)

        if not trans:
            return jsonify({"ok": False, "error": "Empty response from AI", "logs": xlogs}), 500

        if trans.strip() == masked and masked != original.strip():
            return jsonify({"ok": False,
                            "error": "Remote server failed — returned input unchanged",
                            "logs": xlogs}), 500

        xlogs.append(f"translated: {trans[:120]}")
        if tok_issues:
            xlogs.append(f"token_issues: {'; '.join(tok_issues)}")
        xlogs.append(f"status={status} qs={qs}")

        save_translation(
            Path(s["source_dir"]).parent, mod_name,
            cfg.paths.translation_cache, esp_name, key_str, trans,
            cfg=cfg, quality_score=qs, status=status, repo=repo,
        )
        gd = current_app.config.get("GLOBAL_DICT")
        if gd:
            gd.add(original, trans)
            gd.save()

        _tps = round(getattr(pull_backend, "_last_tps", 0.0), 2)
        _tokens = max(1, round(_elapsed * _tps)) if _tps > 0 else 0
        try:
            jm = current_app.config["JOB_MANAGER"]
            jm.record_completed_job(
                name             = f"Translate: {original[:60]}",
                job_type         = "translate_one",
                params           = {"mod_name": mod_name, "esp": esp_name, "key": key_str},
                result           = trans,
                log_lines        = xlogs,
                string_updates   = [{"key": key_str, "esp": esp_name,
                                     "translation": trans,
                                     "status": status, "quality_score": qs}],
                tokens_generated = _tokens,
                tps_avg          = _tps,
                worker_label     = getattr(pull_backend, "_label", ""),
                elapsed_sec      = _elapsed,
            )
        except Exception:
            pass

        return jsonify({"ok": True, "translation": trans, "quality_score": qs,
                        "status": status, "token_issues": tok_issues,
                        "from_dict": False, "logs": xlogs})
    except Exception as exc:
        xlogs.append(f"exception: {exc}")
        log.exception("single translate-one %s | %s", key_str, exc)
        return jsonify({"ok": False, "error": str(exc), "logs": xlogs}), 500


# ── Bulk translate job ─────────────────────────────────────────────────────────

@bp.route("/<session_id>/translate", methods=["POST"])
def translate_bulk(session_id: str):
    """Create a translate_strings job for this session's mod."""
    s = _get_session(session_id)
    if not s:
        return jsonify({"error": "Session not found"}), 404

    cfg      = current_app.config.get("TRANSLATOR_CFG")
    repo     = current_app.config.get("STRING_REPO")
    jm       = current_app.config.get("JOB_MANAGER")
    stats_m  = current_app.config.get("STATS_MGR")
    res_m    = current_app.config.get("RESERVATION_MGR")
    tc       = current_app.config.get("TRANSLATION_CACHE")
    if not cfg or not jm:
        return jsonify({"error": "Server not configured"}), 500

    data     = request.get_json() or {}
    machines = data.get("machines") or []
    mod_name = s["mod_name"]

    backends = None
    registry = current_app.config.get("WORKER_REGISTRY")
    if registry and machines:
        import time as _time
        from translator.web.pull_backend import RegistryPullBackend
        from translator.web.worker_registry import WorkerRegistry
        src_lang = getattr(getattr(cfg, "translation", None), "source_lang", "English")
        tgt_lang = getattr(getattr(cfg, "translation", None), "target_lang", "Russian")
        backends = [
            (label, RegistryPullBackend(label=label, registry=registry,
                                        source_lang=src_lang, target_lang=tgt_lang))
            for label in machines
            if (w := registry.get(label))
            and (_time.time() - w.last_seen) < WorkerRegistry.HEARTBEAT_TTL
        ]

    from translator.web.workers import translate_strings_worker

    def run(job):
        translate_strings_worker(
            job, cfg, mod_name,
            scope="all", force=False,
            backends=backends, repo=repo,
            stats_mgr=stats_m,
            reservation_mgr=res_m,
            translation_cache=tc,
        )

    job = jm.create(
        name     = f"Single: {s['original_zip_name']}",
        job_type = "translate_strings",
        params   = {"mod_name": mod_name},
        fn       = run,
    )
    return jsonify({"ok": True, "job_id": job.id})


# ── Download ───────────────────────────────────────────────────────────────────

@bp.route("/<session_id>/download")
def download(session_id: str):
    """Apply translations to ESP copies, repack ZIP, stream to browser."""
    s = _get_session(session_id)
    if not s:
        return jsonify({"error": "Session not found"}), 404

    repo = current_app.config.get("STRING_REPO")
    if not repo:
        return jsonify({"error": "No DB"}), 500

    mod_name   = s["mod_name"]
    source_dir = Path(s["source_dir"])
    output_dir = Path(s["output_dir"])
    esp_rels   = s["esp_rels"]

    from scripts.esp_engine import cmd_apply_from_strings

    for rel in esp_rels:
        esp_name = Path(rel).name
        strings  = repo.get_all_strings(mod_name, esp_name=esp_name)
        src      = source_dir / rel
        out      = output_dir / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        if src.exists() and strings:
            try:
                cmd_apply_from_strings(src, out, strings)
            except Exception as exc:
                log.warning("single download: apply failed for %s: %s", esp_name, exc)
                shutil.copy2(str(src), str(out))
        elif src.exists():
            shutil.copy2(str(src), str(out))

    # Copy non-ESP files from source → output (skip already-written ESP copies)
    for f in source_dir.rglob("*"):
        if not f.is_file():
            continue
        rel_f = f.relative_to(source_dir)
        out_f = output_dir / rel_f
        if out_f.exists():
            continue
        out_f.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(f), str(out_f))

    stem = Path(s["original_zip_name"]).stem
    buf  = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(output_dir.rglob("*")):
            if f.is_file():
                zf.write(f, str(f.relative_to(output_dir)))
    buf.seek(0)

    return send_file(
        buf,
        mimetype      = "application/zip",
        as_attachment = True,
        download_name = f"{stem}_ru.zip",
    )


# ── Delete session ─────────────────────────────────────────────────────────────

@bp.route("/<session_id>", methods=["DELETE"])
def delete_session(session_id: str):
    sessions = _sessions()
    s        = sessions.pop(session_id, None)
    repo     = current_app.config.get("STRING_REPO")
    mod_name = s["mod_name"] if s else f"__single__{session_id}"

    if repo:
        try:
            repo.db.execute("DELETE FROM strings WHERE mod_name=?", (mod_name,))
            repo.db.commit()
        except Exception as exc:
            log.warning("single delete: DB cleanup failed: %s", exc)

    if s:
        shutil.rmtree(s["dir"], ignore_errors=True)

    return jsonify({"ok": True})
