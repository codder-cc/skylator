"""Nexus downloads — /api/nexus/*

A thin HTTP shell over translator.nexus. It owns two pieces of process state:

  _BATCHES  — DownloadManager instances keyed by batch id, so a POST can start work and
              later GETs can report on it without blocking the request thread
  _STORE    — the shared NxmTicketStore; whatever receives nxm:// URLs (the protocol
              handler, or a paste in the UI) drops them here and the waiting download
              worker picks its own up

Batches are in-memory by design: a download that did not finish before a restart is
resumed by re-submitting it, and the part-files on disk make that cheap.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
import uuid
from pathlib import Path

from flask import Blueprint, Response, current_app, jsonify, request, stream_with_context

from translator.nexus import (
    DownloadRequest, NexusAuthError, NexusClient, NexusError, NxmTicketStore,
    build_manager,
)
from translator.web.routes.utils import safe_under

log = logging.getLogger(__name__)

bp = Blueprint("nexus_rt", __name__, url_prefix="/api/nexus")

_STORE   = NxmTicketStore()
_BATCHES: dict[str, dict] = {}
_LOCK    = threading.Lock()

# Keep the last few finished batches around for the UI, but do not let a long-running
# server accumulate every download it ever ran.
_MAX_BATCHES = 20


# ── helpers ───────────────────────────────────────────────────────────────────


def _cfg():
    cfg = current_app.config.get("TRANSLATOR_CFG")
    if cfg is None:
        raise NexusError("No config loaded")
    return cfg


def _client() -> NexusClient:
    cfg = _cfg()
    return NexusClient(
        api_key     = cfg.nexus.api_key,
        game        = cfg.nexus.game,
        timeout     = cfg.nexus.request_timeout_sec,
        max_retries = cfg.nexus.max_retries,
    )


def _fail(exc: Exception, status: int = 500):
    """Map a stack error onto an HTTP status the frontend can branch on."""
    from werkzeug.exceptions import HTTPException

    from translator.nexus import (
        FileResolutionError, NexusNotFound, NexusPremiumRequired, NexusRateLimited,
    )
    if isinstance(exc, HTTPException):
        # safe_under() and friends abort() with a status that is already correct --
        # wrapping it here would turn a deliberate 400 into a generic 500.
        raise exc
    if isinstance(exc, NexusAuthError):
        status = 401
    elif isinstance(exc, NexusNotFound):
        status = 404
    elif isinstance(exc, NexusPremiumRequired):
        status = 402          # Payment Required — literally what Nexus is saying
    elif isinstance(exc, NexusRateLimited):
        status = 429
    elif isinstance(exc, (FileResolutionError, ValueError, KeyError)):
        status = 400
    log.warning("nexus route error: %s", exc)
    return jsonify({"ok": False, "error": str(exc), "kind": type(exc).__name__}), status


def _dest_dir(raw: str | None) -> Path:
    """Resolve a caller-supplied destination, confined to the configured download dir.

    An unchecked dest_dir would let any POST write an attacker-chosen archive anywhere
    the server process can reach, so a relative sub-path is the most a request may pick.
    """
    base = Path(_cfg().nexus.download_dir)
    if not raw:
        return base
    candidate = Path(raw)
    if candidate.is_absolute():
        # Absolute paths are honoured only when they already sit under the configured
        # root — that covers "the ARCHIVE folder" without opening the whole filesystem.
        base_r = base.resolve()
        cand_r = candidate.resolve()
        if cand_r != base_r and base_r not in cand_r.parents:
            raise ValueError(f"dest_dir must be under {base}")
        return cand_r
    return safe_under(base, str(candidate))


def _prune_batches() -> None:
    """Drop the oldest finished batches once the cap is exceeded."""
    if len(_BATCHES) <= _MAX_BATCHES:
        return
    finished = sorted(
        ((bid, b) for bid, b in _BATCHES.items() if b["finished_at"]),
        key=lambda kv: kv[1]["finished_at"],
    )
    for bid, _ in finished[: len(_BATCHES) - _MAX_BATCHES]:
        _BATCHES.pop(bid, None)


# ── account ───────────────────────────────────────────────────────────────────


def _browser():
    """The process-wide BrowserSession, or None when the route is off/unavailable."""
    cfg = _cfg()
    if not getattr(cfg.nexus, "browser_enabled", True):
        return None
    try:
        from translator.nexus.browser import BrowserSession
        return BrowserSession.for_config(cfg)
    except Exception as exc:                      # websockets missing, bad profile path
        log.warning("browser session unavailable: %s", exc)
        return None


@bp.route("/account")
def account():
    """GET /api/nexus/account — key validity, Premium status, quota, and who is needed.

    `unattended` is the field the UI actually acts on. A Premium key needs nobody. A
    free key needs nobody either, as long as the browser profile holds a Nexus session
    — and when it does not, that is the one thing to ask the user for.
    """
    try:
        client = _client()
        info   = client.validate()
        cfg    = _cfg()

        browser = _browser()
        # status() does not start Chrome, so this stays a cheap call on a page load.
        bstatus = browser.status() if browser is not None else {"running": False,
                                                                "logged_in": False,
                                                                "ready": False}
        mode    = cfg.nexus.link_mode
        premium = bool(info.get("is_premium"))
        uses_browser = mode in ("auto", "browser") and browser is not None and not premium

        info.update({
            "ok":         True,
            "game":       client.game,
            "rate_limit": client.rate_limit.as_dict(),
            "link_mode":  mode,
            "download_dir": str(cfg.nexus.download_dir),
            "browser":    bstatus,
            "uses_browser": uses_browser,
            # "ready", not "logged_in": the profile keeps the session across restarts,
            # so a browser that simply is not running right now needs nothing from the
            # user. Confirm it for certain with GET /api/nexus/browser?verify=1.
            "unattended": premium or (uses_browser and bool(bstatus.get("ready"))),
        })
        return jsonify(info)
    except Exception as exc:
        return _fail(exc)


# ── browse / resolve ──────────────────────────────────────────────────────────


@bp.route("/mods/<int:mod_id>")
def mod_info(mod_id: int):
    """GET /api/nexus/mods/<id> — name, author, version, summary."""
    try:
        d = _client().mod(mod_id)
        return jsonify({"ok": True, "mod": {
            "mod_id":      d.get("mod_id"),
            "name":        d.get("name"),
            "author":      d.get("author"),
            "version":     d.get("version"),
            "summary":     d.get("summary"),
            "picture_url": d.get("picture_url"),
            "available":   d.get("available", True),
            "status":      d.get("status"),
        }})
    except Exception as exc:
        return _fail(exc)


@bp.route("/mods/<int:mod_id>/files")
def mod_files(mod_id: int):
    """GET /api/nexus/mods/<id>/files?categories=main,update — every uploaded file."""
    try:
        cats = tuple(c.strip() for c in (request.args.get("categories") or "").split(",") if c.strip())
        files = _client().files(mod_id, categories=cats)
        return jsonify({"ok": True, "mod_id": mod_id, "files": [
            {"file_id": f.file_id, "name": f.name, "file_name": f.file_name,
             "version": f.version, "mod_version": f.mod_version,
             "category": f.category_name, "size_bytes": f.size_bytes,
             "uploaded_timestamp": f.uploaded_timestamp, "is_primary": f.is_primary}
            for f in files
        ]})
    except Exception as exc:
        return _fail(exc)


@bp.route("/resolve", methods=["POST"])
def resolve():
    """POST /api/nexus/resolve — dry-run the file choice without downloading.

    Body: {mod_id, file_id?, file_name?, version?, categories?, allow_newest?}
    Lets the UI show "this is what I would fetch" before committing to a batch.
    """
    try:
        from translator.nexus import FileResolver
        d      = request.get_json(silent=True) or {}
        req    = DownloadRequest.from_dict(d)
        client = _client()
        f = FileResolver(client).resolve(
            req.mod_id, file_id=req.file_id, file_name=req.file_name,
            version=req.version, categories=req.categories,
            allow_newest=req.allow_newest)
        return jsonify({"ok": True, "mod_id": req.mod_id, "file": {
            "file_id": f.file_id, "name": f.name, "file_name": f.file_name,
            "version": f.version, "category": f.category_name,
            "size_bytes": f.size_bytes, "is_primary": f.is_primary}})
    except Exception as exc:
        return _fail(exc)


# ── downloads ─────────────────────────────────────────────────────────────────


@bp.route("/downloads", methods=["POST"])
def create_batch():
    """POST /api/nexus/downloads — queue a batch and start it.

    Body: {items: [{mod_id, file_id?, file_name?, version?, label?}, ...], dest_dir?}
    Returns immediately with a batch id; poll /downloads/<id> or subscribe to its
    stream. Nothing here blocks, so a 3 800-mod list does not hold a worker thread.
    """
    try:
        d     = request.get_json(silent=True) or {}
        raw   = d.get("items") or []
        if not raw:
            return jsonify({"ok": False, "error": "no items"}), 400
        dest  = _dest_dir(d.get("dest_dir"))
        reqs  = [DownloadRequest.from_dict(x) for x in raw]

        batch_id = uuid.uuid4().hex[:12]
        events: queue.Queue = queue.Queue(maxsize=2000)

        def _on_event(item):
            # Never block a download worker on a slow or absent SSE reader.
            try:
                events.put_nowait(item.as_dict())
            except queue.Full:
                pass

        mgr = build_manager(_cfg(), dest_dir=dest, on_event=_on_event, store=_STORE)
        mgr.submit(reqs)

        entry = {"id": batch_id, "manager": mgr, "events": events,
                 "created_at": time.time(), "finished_at": 0.0, "dest_dir": str(dest)}
        with _LOCK:
            _BATCHES[batch_id] = entry
            _prune_batches()

        def _run():
            try:
                mgr.run(block=True)
            except Exception:
                log.exception("batch %s crashed", batch_id)
            finally:
                entry["finished_at"] = time.time()
                try:
                    events.put_nowait({"_batch_done": True})
                except queue.Full:
                    pass

        threading.Thread(target=_run, name=f"nexus-batch-{batch_id}", daemon=True).start()
        return jsonify({"ok": True, "batch_id": batch_id, "count": len(reqs),
                        "dest_dir": str(dest)})
    except Exception as exc:
        return _fail(exc)


@bp.route("/downloads")
def list_batches():
    with _LOCK:
        entries = list(_BATCHES.values())
    return jsonify({"ok": True, "batches": [
        {"id": e["id"], "created_at": e["created_at"], "finished_at": e["finished_at"],
         "dest_dir": e["dest_dir"], **e["manager"].snapshot()["counts"]}
        for e in sorted(entries, key=lambda e: e["created_at"], reverse=True)
    ]})


@bp.route("/downloads/<batch_id>")
def batch_status(batch_id: str):
    with _LOCK:
        entry = _BATCHES.get(batch_id)
    if entry is None:
        return jsonify({"ok": False, "error": "unknown batch"}), 404
    snap = entry["manager"].snapshot()
    snap.update({"ok": True, "batch_id": batch_id, "dest_dir": entry["dest_dir"],
                 "finished_at": entry["finished_at"]})
    return jsonify(snap)


@bp.route("/downloads/<batch_id>/stream")
def batch_stream(batch_id: str):
    """SSE — per-item state and progress as they change."""
    with _LOCK:
        entry = _BATCHES.get(batch_id)
    if entry is None:
        return jsonify({"ok": False, "error": "unknown batch"}), 404
    events: queue.Queue = entry["events"]
    mgr = entry["manager"]

    @stream_with_context
    def generate():
        # Replay current state first so a late subscriber is not left with a blank list
        # until the next progress tick.
        yield f"data: {json.dumps(mgr.snapshot())}\n\n"
        while True:
            try:
                data = events.get(timeout=15)
            except Exception:
                yield ": ping\n\n"
                continue
            if data.get("_batch_done"):
                yield f"data: {json.dumps(mgr.snapshot())}\n\n"
                yield "event: done\ndata: {}\n\n"
                return
            yield f"data: {json.dumps(data)}\n\n"

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@bp.route("/downloads/<batch_id>/cancel", methods=["POST"])
def cancel_batch(batch_id: str):
    with _LOCK:
        entry = _BATCHES.get(batch_id)
    if entry is None:
        return jsonify({"ok": False, "error": "unknown batch"}), 404
    return jsonify({"ok": True, "cancelled": entry["manager"].cancel_all()})


@bp.route("/downloads/<batch_id>/items/<item_id>/cancel", methods=["POST"])
def cancel_item(batch_id: str, item_id: str):
    with _LOCK:
        entry = _BATCHES.get(batch_id)
    if entry is None:
        return jsonify({"ok": False, "error": "unknown batch"}), 404
    return jsonify({"ok": entry["manager"].cancel(item_id)})


# ── nxm handoff ───────────────────────────────────────────────────────────────


@bp.route("/nxm", methods=["POST"])
def deposit_nxm():
    """POST /api/nexus/nxm — hand a Mod Manager Download link to the waiting worker.

    Body: {url: "nxm://skyrimspecialedition/mods/1/files/2?key=..&expires=.."}

    This is the endpoint the nxm:// protocol handler calls. Registering Skylator as that
    handler is what turns "click download on the site" into an unattended transfer —
    the click stays manual because the key Nexus mints is bound to that click.
    """
    d   = request.get_json(silent=True) or {}
    url = (d.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "no url"}), 400
    try:
        ticket = _STORE.put(url)
        return jsonify({"ok": True, "mod_id": ticket.mod_id, "file_id": ticket.file_id,
                        "expires_in": max(0, ticket.expires - int(time.time()))})
    except Exception as exc:
        return _fail(exc, status=400)


@bp.route("/nxm/pending")
def pending_nxm():
    return jsonify({"ok": True, "tickets": _STORE.pending()})


@bp.route("/nxm", methods=["DELETE"])
def clear_nxm():
    return jsonify({"ok": True, "cleared": _STORE.clear()})


# ── browser session ───────────────────────────────────────────────────────────


@bp.route("/browser")
def browser_status():
    """GET /api/nexus/browser — is Chrome there, is it up, is the profile signed in.

    Launches nothing by default: a dashboard poll must not start a browser. `logged_in`
    is therefore null unless Chrome happens to be running, and `ready` answers the
    question the UI actually has — does this need the user or not.

    `?verify=1` starts Chrome and checks the session for real.
    """
    browser = _browser()
    if browser is None:
        return jsonify({"ok": True, "enabled": False, "ready": False,
                        "reason": "nexus.browser_enabled is false, or websockets/Chrome "
                                  "is missing on this host"})
    try:
        verify = request.args.get("verify") in ("1", "true", "yes")
        status = browser.verify() if verify else browser.status()
        return jsonify({"ok": True, "enabled": True, "verified": verify, **status})
    except Exception as exc:
        return _fail(exc)


@bp.route("/browser/login", methods=["POST"])
def browser_login():
    """POST /api/nexus/browser/login — open the window and wait for a sign-in.

    Body: {wait_seconds?}. This is the only endpoint in the whole stack that expects a
    human, and it is called once per profile: the Nexus cookie outlives the process, so
    afterwards every download runs unattended.

    It blocks for as long as the caller allows, which is why the default comes from
    config rather than from the request — a UI that opens this without a timeout would
    hold a worker thread until someone noticed.
    """
    browser = _browser()
    if browser is None:
        return jsonify({"ok": False, "error": "browser link minting is not available "
                                              "on this host"}), 409
    d    = request.get_json(silent=True) or {}
    wait = d.get("wait_seconds")
    try:
        who = browser.login(float(wait) if wait is not None else None)
        return jsonify({"ok": True, "session": who, **browser.status()})
    except Exception as exc:
        return _fail(exc)


@bp.route("/browser/close", methods=["POST"])
def browser_close():
    """POST /api/nexus/browser/close — shut Chrome down, keeping the profile.

    The session is in the profile directory, not in the process, so closing costs
    nothing but the few seconds of the next launch.
    """
    browser = _browser()
    if browser is None:
        return jsonify({"ok": True, "closed": False})
    try:
        browser.close()
        return jsonify({"ok": True, "closed": True})
    except Exception as exc:
        return _fail(exc)


@bp.route("/browser/mint", methods=["POST"])
def browser_mint():
    """POST /api/nexus/browser/mint — mint one signed CDN link and hand it back.

    Body: {mod_id, file_id?, file_name?, version?}. Downloads do this themselves; this
    endpoint exists so the route can be proved end to end without moving gigabytes,
    which is what you want when a Nexus change breaks it at three in the morning.
    """
    browser = _browser()
    if browser is None:
        return jsonify({"ok": False, "error": "browser link minting is not available "
                                              "on this host"}), 409
    try:
        from translator.nexus import FileResolver

        d      = request.get_json(silent=True) or {}
        req    = DownloadRequest.from_dict(d)
        client = _client()
        f = FileResolver(client).resolve(
            req.mod_id, file_id=req.file_id, file_name=req.file_name,
            version=req.version, categories=req.categories,
            allow_newest=req.allow_newest)
        link = browser.mint(client.game_id(), f.file_id)
        return jsonify({"ok": True, "mod_id": req.mod_id, "file_id": f.file_id,
                        "file_name": f.file_name, "url": link.url, **link.as_dict()})
    except Exception as exc:
        return _fail(exc)


# ── search ────────────────────────────────────────────────────────────────────
#
# Search is not part of the public API v1 at all -- it lives on Nexus's GraphQL
# endpoint. See translator/nexus/search.py for the measured schema.


def _search():
    from translator.nexus.search import build_search
    return build_search(_cfg())


@bp.route("/search")
def search_mods():
    """GET /api/nexus/search?q=&language=&author=&category=&count=&offset=&mode=

    mode: "stemmed" (default, what a person means by searching), "exact" (the title
    verbatim) or "contains" (substring of the title).

    Note for anyone extending this: Nexus's WILDCARD operator takes a bare substring.
    Wrapping the term in asterisks returns an empty page rather than an error.
    """
    try:
        q      = (request.args.get("q") or "").strip()
        mode   = (request.args.get("mode") or "stemmed").lower()
        count  = min(int(request.args.get("count") or 20), 100)
        offset = max(int(request.args.get("offset") or 0), 0)
        common = {
            "game":          request.args.get("game") or None,
            "language":      request.args.get("language") or None,
            "author":        request.args.get("author") or None,
            "category":      request.args.get("category") or None,
            "include_adult": request.args.get("adult") in ("1", "true", "yes"),
        }

        s = _search()
        if mode == "contains":
            if not q:
                return jsonify({"ok": False, "error": "mode=contains needs q"}), 400
            hits, total = s.contains(q, count=count, offset=offset, **common)
        else:
            hits, total = s.search(q, exact=(mode == "exact"), count=count,
                                   offset=offset, **common)

        return jsonify({"ok": True, "query": q, "mode": mode, "total": total,
                        "count": len(hits), "offset": offset,
                        "results": [h.as_dict() for h in hits]})
    except Exception as exc:
        return _fail(exc)


@bp.route("/random")
def random_mod():
    """GET /api/nexus/random?count=&seed=&min_downloads=&language=

    Nexus sorts randomly server-side, so this does not page through six figures of mods
    to pick one. `min_downloads` defaults above zero because a uniform sample of
    everything ever uploaded is mostly abandoned one-file experiments.
    """
    try:
        count = min(int(request.args.get("count") or 1), 50)
        seed  = request.args.get("seed")
        hits  = _search().random(
            game          = request.args.get("game") or None,
            seed          = int(seed) if seed else None,
            count         = count,
            language      = request.args.get("language") or None,
            min_downloads = int(request.args.get("min_downloads") or 1000),
            include_adult = request.args.get("adult") in ("1", "true", "yes"),
        )
        return jsonify({"ok": True, "count": len(hits),
                        "results": [h.as_dict() for h in hits]})
    except Exception as exc:
        return _fail(exc)


@bp.route("/translations")
def find_translations():
    """GET /api/nexus/translations?mod_id=|name=&language=Russian&count=

    The query this whole module exists for: somebody has already translated this mod,
    and their strings are worth more to the dictionary than anything a model invents.

    Each result carries the parts of its score -- how much of the source title it
    carries, whether it carries it contiguously, whether it announces itself as a
    translation -- because the language filter alone is too generous: a patch that
    merely mentions the mod is in the same result set as its actual translation.
    """
    try:
        language = request.args.get("language") or "Russian"
        count    = min(int(request.args.get("count") or 10), 50)
        game     = request.args.get("game") or None
        s        = _search()

        mod_id = request.args.get("mod_id")
        if mod_id:
            source, hits = s.translations_of_mod(int(mod_id), language=language,
                                                 game=game, count=count)
            if source is None:
                return jsonify({"ok": False,
                                "error": f"mod {mod_id} not found on Nexus"}), 404
        else:
            name = (request.args.get("name") or "").strip()
            if not name:
                return jsonify({"ok": False, "error": "need mod_id or name"}), 400
            source, hits = None, s.translations_of(name, language=language,
                                                   game=game, count=count)

        return jsonify({"ok": True, "language": language,
                        "source": source.as_dict() if source else
                                  {"name": request.args.get("name")},
                        "count": len(hits),
                        "results": [h.as_dict() for h in hits]})
    except Exception as exc:
        return _fail(exc)


# ── Translate From Mod ────────────────────────────────────────────────────────
#
# Someone has already translated this mod. These endpoints find their upload, read the
# strings out of it and line them up against ours. Planning and applying are separate
# calls on purpose: the plan carries the conflicts, and the decision to overwrite
# finished work belongs to a person looking at them, not to a default.

_PLANS: dict[str, dict] = {}
_PLAN_LOCK = threading.Lock()
_MAX_PLANS = 10


def _repo():
    repo = current_app.config.get("STRING_REPO")
    if repo is None:
        raise NexusError("the translation store is not loaded")
    return repo


def _global_dict():
    """The cross-mod dictionary, when the app has one.

    A human translation of an English original is exactly what it is for, and feeding
    it there is what makes one donor help every other mod that shares a phrase.
    """
    return current_app.config.get("GLOBAL_DICT")


def _remember_plan(report) -> str:
    """Keep a plan server-side so applying it does not re-download the donor."""
    plan_id = uuid.uuid4().hex[:12]
    with _PLAN_LOCK:
        _PLANS[plan_id] = {"report": report, "at": time.time()}
        if len(_PLANS) > _MAX_PLANS:
            for k, _ in sorted(_PLANS.items(), key=lambda kv: kv[1]["at"])[
                    : len(_PLANS) - _MAX_PLANS]:
                _PLANS.pop(k, None)
    return plan_id


@bp.route("/donors")
def donor_candidates():
    """GET /api/nexus/donors?mod=<our mod name>&language=Russian

    Who has published a translation of this mod. Thin wrapper over /translations that
    speaks in terms of our installed mod rather than a search string.
    """
    try:
        mod = (request.args.get("mod") or "").strip()
        if not mod:
            return jsonify({"ok": False, "error": "need mod"}), 400
        language = request.args.get("language") or "Russian"
        from translator.nexus.search import build_search

        search = build_search(_cfg())

        # Search on the mod's real Nexus title when we can learn it. A Nolvus folder is
        # named by whoever built the modlist -- "Adamant" where Nexus says "Adamant - A
        # Perk Overhaul" -- and the ranking compares titles, so the folder name quietly
        # costs matches. meta.ini already carries the mod id that resolves it.
        search_name, source_name, nexus_id = mod, "folder", None
        scanner = current_app.config.get("SCANNER")
        if scanner is not None:
            try:
                info = scanner.get_mod(mod)
                nexus_id = getattr(info, "nexus_mod_id", None) if info else None
            except Exception:
                log.debug("scanner lookup failed for %s", mod, exc_info=True)
        if nexus_id:
            try:
                hit = search.by_mod_id(int(nexus_id))
                if hit and hit.name:
                    search_name, source_name = hit.name, "nexus"
            except Exception as exc:
                log.info("could not resolve the Nexus title for %s: %s", mod, exc)

        hits = search.translations_of(
            search_name, language=language,
            count=min(int(request.args.get("count") or 10), 50))
        return jsonify({"ok": True, "mod": mod, "language": language,
                        "searched_as": search_name, "name_source": source_name,
                        "nexus_mod_id": nexus_id,
                        "count": len(hits), "results": [h.as_dict() for h in hits]})
    except Exception as exc:
        return _fail(exc)


@bp.route("/transfer/plan", methods=["POST"])
def transfer_plan():
    """POST /api/nexus/transfer/plan — download a donor and report what it would give.

    Body: {mod, donor_mod_id | archive_path, language?, donor_file_id?, esp_map?,
           keep_archive?}

    Writes nothing. The response carries counts and a sample; `plan_id` hands the same
    parsed plan to /transfer/apply so accepting it costs no second download.
    """
    try:
        from translator.nexus import translate_from_mod as tfm

        d   = request.get_json(silent=True) or {}
        mod = (d.get("mod") or "").strip()
        if not mod:
            return jsonify({"ok": False, "error": "need mod"}), 400
        if not (d.get("donor_mod_id") or d.get("archive_path")):
            return jsonify({"ok": False,
                            "error": "need donor_mod_id or archive_path"}), 400

        cfg = _cfg()
        report = tfm.run(
            cfg, _repo(), mod_name=mod,
            donor_mod_id  = int(d["donor_mod_id"]) if d.get("donor_mod_id") else None,
            donor_file_id = int(d["donor_file_id"]) if d.get("donor_file_id") else None,
            archive_path  = d.get("archive_path"),
            language      = d.get("language") or "Russian",
            apply_changes = False,
            esp_map       = d.get("esp_map") or None,
            keep_archive  = bool(d.get("keep_archive", False)),
            staging_dir   = getattr(cfg.nexus, "donor_dir", None),
        )
        plan_id = _remember_plan(report)
        return jsonify({"ok": True, "plan_id": plan_id,
                        **report.as_dict(sample=int(d.get("sample") or 200))})
    except Exception as exc:
        return _fail(exc)


@bp.route("/transfer/plan/<plan_id>")
def transfer_plan_detail(plan_id: str):
    """GET /api/nexus/transfer/plan/<id>?action=conflict&offset=&limit=

    Page through a plan's candidates. A large mod produces tens of thousands, and the
    plan response only samples them.
    """
    with _PLAN_LOCK:
        entry = _PLANS.get(plan_id)
    if entry is None:
        return jsonify({"ok": False, "error": "unknown or expired plan"}), 404

    plan = entry["report"].plan
    action = request.args.get("action")
    rows   = plan.by_action(action) if action else plan.candidates
    offset = max(int(request.args.get("offset") or 0), 0)
    limit  = min(int(request.args.get("limit") or 100), 1000)
    return jsonify({"ok": True, "plan_id": plan_id, "action": action,
                    "total": len(rows), "offset": offset,
                    "candidates": [c.as_dict() for c in rows[offset:offset + limit]]})


@bp.route("/transfer/apply", methods=["POST"])
def transfer_apply():
    """POST /api/nexus/transfer/apply — write a planned merge into the store.

    Body: {plan_id, overwrite?, status?, only_keys?, confirmed_only?}

    `overwrite=false` (the default) takes only the strings we have nothing for. `status`
    is "needs_review" by default, so a donor's work arrives as a proposal; "translated"
    accepts it outright. `only_keys` is a list of [esp_name, key] pairs for the case
    where the user ticked specific rows.

    `confirmed_only=true` is the unattended policy, and it exists because a donor is a
    second opinion rather than an authority: on the class where the answer is knowable it
    beat us 258 times and lost 90, so taking every conflict would break eighty-odd strings
    to mend two hundred. It writes what we have nothing for, plus the conflicts where the
    official localisation itself confirms the donor — two independent authorities agreeing
    against one machine translation. Everything else stays untouched and remains a
    decision for a person looking at it.
    """
    try:
        d = request.get_json(silent=True) or {}
        plan_id = d.get("plan_id")
        with _PLAN_LOCK:
            entry = _PLANS.get(plan_id)
        if entry is None:
            return jsonify({"ok": False, "error": "unknown or expired plan"}), 404

        from translator.nexus import merge as merge_mod

        report = entry["report"]
        only_keys = d.get("only_keys") or None
        overwrite = bool(d.get("overwrite", False))
        confirmed = 0
        if d.get("confirmed_only"):
            from translator.validation.authority import load_official
            rows = merge_mod.confirmed_by_official(report.plan, load_official())
            confirmed = len(rows)
            # Пустые у нас строки берём всегда — это чистый выигрыш; поверх готового
            # текста пишем только подтверждённое. Оба случая требуют overwrite, потому
            # что второй — это именно расхождение.
            only_keys = ([(c.esp_name, c.key) for c in report.plan.by_action(merge_mod.FILL)]
                         + [(c.esp_name, c.key) for c, _k, _n in rows])
            overwrite = True

        # Запись переноса заводится ДО применения, чтобы у строк была куда ссылаться:
        # номер нужен в момент записи, а не после. Итоги дописываются следом.
        from translator.nexus import provenance as prov
        repo = _repo()
        status = d.get("status") or "needs_review"
        policy = ("confirmed_only" if d.get("confirmed_only")
                  else "only_keys" if d.get("only_keys")
                  else "overwrite" if overwrite else "fill_only")
        import_id = None
        try:
            import_id = prov.record_import(repo.db, report, {}, policy, status)
        except Exception as exc:                                   # noqa: BLE001
            log.warning("could not record donor provenance: %s", exc)

        result = merge_mod.apply(
            repo, report.plan,
            overwrite   = overwrite,
            status      = status,
            only_keys   = only_keys,
            translated_by = prov.mark(import_id) if import_id else None,
            global_dict = _global_dict(),
        )
        if d.get("confirmed_only"):
            result["confirmed_by_official"] = confirmed
        if import_id:
            try:
                repo.db.execute(
                    "UPDATE donor_imports SET applied=?, confirmed=? WHERE import_id=?",
                    (int(result.get("applied") or 0), int(confirmed), import_id))
                repo.db.commit()
            except Exception as exc:                               # noqa: BLE001
                log.warning("could not finish donor provenance row: %s", exc)
            result["import_id"] = import_id
        report.applied = result

        # The strings are in the store now; the mod's cached counts are stale.
        scanner = current_app.config.get("SCANNER")
        if scanner is not None:
            try:
                scanner.invalidate(report.mod_name)
            except Exception:
                log.debug("scanner invalidate failed", exc_info=True)

        return jsonify({"ok": True, "mod": report.mod_name, **result})
    except Exception as exc:
        return _fail(exc)


@bp.route("/transfer/plan/<plan_id>", methods=["DELETE"])
def transfer_plan_drop(plan_id: str):
    with _PLAN_LOCK:
        return jsonify({"ok": _PLANS.pop(plan_id, None) is not None})


@bp.route("/transfer/oneshot", methods=["POST"])
def transfer_oneshot():
    """POST /api/nexus/transfer/oneshot — find, download, merge and clean up in one call.

    Body: {mod, language?, donor_mod_id?, overwrite?, status?}

    Without donor_mod_id this picks the best-ranked translation itself. Nothing is left
    behind: the archive and everything unpacked from it are deleted once the strings
    are out.
    """
    try:
        from translator.nexus import translate_from_mod as tfm
        from translator.nexus.search import build_search

        d   = request.get_json(silent=True) or {}
        mod = (d.get("mod") or "").strip()
        if not mod:
            return jsonify({"ok": False, "error": "need mod"}), 400
        language = d.get("language") or "Russian"
        cfg      = _cfg()

        donor_id = d.get("donor_mod_id")
        chosen   = None
        if not donor_id:
            hits = build_search(cfg).translations_of(mod, language=language, count=5)
            if not hits:
                return jsonify({"ok": False,
                                "error": f"no published {language} translation found "
                                         f"for {mod!r}"}), 404
            chosen   = hits[0]
            donor_id = chosen.mod.mod_id

        report = tfm.run(
            cfg, _repo(), mod_name=mod, donor_mod_id=int(donor_id), language=language,
            apply_changes = True,
            overwrite     = bool(d.get("overwrite", False)),
            status        = d.get("status") or "needs_review",
            global_dict   = _global_dict(),
            staging_dir   = getattr(cfg.nexus, "donor_dir", None),
        )
        scanner = current_app.config.get("SCANNER")
        if scanner is not None:
            try:
                scanner.invalidate(mod)
            except Exception:
                log.debug("scanner invalidate failed", exc_info=True)

        out = report.as_dict(sample=int(d.get("sample") or 20))
        if chosen is not None:
            out["chosen_donor"] = chosen.as_dict()
        return jsonify({"ok": True, **out})
    except Exception as exc:
        return _fail(exc)


# ── transfer as a job ─────────────────────────────────────────────────────────
#
# /transfer/plan holds the request open while a donor downloads and unpacks, which is
# fine for a 300 kB archive and wrong for a 300 MB one. This runs the same flow on the
# job queue instead, so it reports through the SSE stream every other long operation in
# the app already uses, survives the page being closed, and shows up in Jobs.

_STEP_ORDER = ["download", "extract", "harvest", "plan", "apply"]


@bp.route("/transfer/job", methods=["POST"])
def transfer_job():
    """POST /api/nexus/transfer/job — queue a Translate From Mod run.

    Body: {mod, donor_mod_id?, language?, apply?, overwrite?, status?, keep_archive?,
           include_mcm?, include_swf?}

    Returns {job_id, plan_id} immediately. Watch /jobs/<job_id>/stream for progress and
    read the finished plan from /api/nexus/transfer/plan/<plan_id>.

    Without donor_mod_id the best-ranked published translation is chosen, exactly as
    /transfer/oneshot does — the difference here is only who waits.
    """
    try:
        from translator.nexus import translate_from_mod as tfm
        from translator.nexus.search import build_search

        d   = request.get_json(silent=True) or {}
        mod = (d.get("mod") or "").strip()
        if not mod:
            return jsonify({"ok": False, "error": "need mod"}), 400

        jm = current_app.config.get("JOB_MANAGER")
        if jm is None:
            return jsonify({"ok": False, "error": "the job manager is not loaded"}), 503

        cfg      = _cfg()
        repo     = _repo()
        gd       = _global_dict()
        language = d.get("language") or "Russian"
        do_apply = bool(d.get("apply", False))
        plan_id  = uuid.uuid4().hex[:12]

        def _work(job):
            job.progress.total   = len(_STEP_ORDER)
            job.progress.current = 0
            job.progress.message = "starting"

            def say(step, message):
                # The flow reports by name, not by number; mapping it here keeps the
                # progress bar honest without the flow knowing what a job is.
                if step in _STEP_ORDER:
                    job.progress.current = _STEP_ORDER.index(step) + 1
                job.progress.sub_step = step
                job.progress.message  = message
                job.add_log(f"[{step}] {message}")

            donor_id = d.get("donor_mod_id")
            if not donor_id:
                say("download", f"looking for a published {language} translation")
                hits = build_search(cfg).translations_of(mod, language=language, count=5)
                if not hits:
                    raise RuntimeError(f"no published {language} translation found "
                                       f"for {mod!r}")
                donor_id = hits[0].mod.mod_id
                job.add_log(f"chose {donor_id} — {hits[0].mod.name} "
                            f"(score {hits[0].score:.2f})")

            report = tfm.run(
                cfg, repo, mod_name=mod, donor_mod_id=int(donor_id), language=language,
                apply_changes = do_apply,
                overwrite     = bool(d.get("overwrite", False)),
                status        = d.get("status") or "needs_review",
                keep_archive  = bool(d.get("keep_archive", False)),
                include_mcm   = bool(d.get("include_mcm", True)),
                include_swf   = bool(d.get("include_swf", True)),
                global_dict   = gd if do_apply else None,
                staging_dir   = getattr(cfg.nexus, "donor_dir", None),
                on_progress   = say,
            )

            with _PLAN_LOCK:
                _PLANS[plan_id] = {"report": report, "at": time.time()}

            counts = report.plan.counts if report.plan is not None else {}
            job.add_log(f"plan: {counts}")
            if do_apply:
                job.add_log(f"applied: {report.applied}")
                scanner = current_app.config.get("SCANNER")
                if scanner is not None:
                    try:
                        scanner.invalidate(mod)
                    except Exception:
                        log.debug("scanner invalidate failed", exc_info=True)
            job.progress.current = len(_STEP_ORDER)
            job.progress.message = (f"{report.applied.get('applied', 0)} written"
                                    if do_apply else f"{counts}")
            job.result = json.dumps({"plan_id": plan_id, **report.as_dict(sample=0)})

        job = jm.create(
            name     = f"Translate From Mod — {mod}",
            job_type = "nexus_transfer",
            params   = {"mod_name": mod, "language": language, "apply": do_apply,
                        "plan_id": plan_id},
            fn       = _work,
        )
        return jsonify({"ok": True, "job_id": job.id, "plan_id": plan_id})
    except Exception as exc:
        return _fail(exc)


# ── settings ──────────────────────────────────────────────────────────────────


@bp.route("/settings", methods=["GET"])
def get_settings():
    """GET /api/nexus/settings — the persisted nexus block, as the UI needs it."""
    try:
        nx = _cfg().nexus
        return jsonify({"ok": True,
                        "download_dir":        str(nx.download_dir or ""),
                        "donor_dir":           str(getattr(nx, "donor_dir", "") or ""),
                        "game":                nx.game,
                        "link_mode":           nx.link_mode,
                        "max_concurrent":      nx.max_concurrent,
                        "browser_enabled":     getattr(nx, "browser_enabled", True),
                        "browser_profile_dir": str(getattr(nx, "browser_profile_dir", "") or ""),
                        "archive_tool_path":   getattr(nx, "archive_tool_path", "")})
    except Exception as exc:
        return _fail(exc)


# Only these may be written from the UI. A settings endpoint that accepted any key would
# let a request point the API at another host or hand the browser a different profile.
_WRITABLE = {"download_dir", "donor_dir", "link_mode", "max_concurrent",
             "browser_enabled", "archive_tool_path"}


@bp.route("/settings", methods=["POST"])
def save_settings():
    """POST /api/nexus/settings — persist changes into config.yaml and reload them.

    Writing config.yaml rather than holding the value in the browser is the difference
    between a setting and a preference that evaporates on refresh. The file is rewritten
    through yaml.safe_load/safe_dump of the whole document, so unrelated keys survive.
    """
    try:
        d = request.get_json(silent=True) or {}
        changes = {k: v for k, v in d.items() if k in _WRITABLE}
        if not changes:
            return jsonify({"ok": False,
                            "error": f"nothing writable in the request; allowed: "
                                     f"{sorted(_WRITABLE)}"}), 400

        if "download_dir" in changes:
            raw = str(changes["download_dir"]).strip()
            if not raw:
                return jsonify({"ok": False, "error": "download_dir cannot be empty"}), 400
            target = Path(raw)
            try:
                target.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                return jsonify({"ok": False,
                                "error": f"cannot use {target}: {exc}"}), 400
            if not os.access(target, os.W_OK):
                return jsonify({"ok": False,
                                "error": f"{target} is not writable"}), 400
            changes["download_dir"] = str(target)

        cfg_path = Path(current_app.config.get("CONFIG_PATH")
                        or _cfg().__dict__.get("_source_path")
                        or "config.yaml")
        if not cfg_path.exists():
            return jsonify({"ok": False,
                            "error": f"config file not found at {cfg_path}"}), 500

        # Edited line by line, not round-tripped through safe_dump: config.yaml is a
        # file the user annotates, and a dump would save the setting and delete their
        # comments in the same breath.
        from translator.config_edit import ConfigEditError, set_values
        try:
            set_values(cfg_path, "nexus", changes)
        except ConfigEditError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

        # Reload so the running process uses the new value without a restart, and drop
        # the shared browser session: its profile path may have just changed.
        from translator.config import reload_config
        new_cfg = reload_config(cfg_path)
        current_app.config["TRANSLATOR_CFG"] = new_cfg
        try:
            from translator.nexus.browser import BrowserSession
            BrowserSession.reset_shared()
        except Exception:
            log.debug("could not reset the browser session", exc_info=True)

        log.info("nexus settings updated: %s", sorted(changes))
        return jsonify({"ok": True, "saved": changes,
                        "download_dir": str(new_cfg.nexus.download_dir)})
    except Exception as exc:
        return _fail(exc)


@bp.route("/imports")
def donor_imports():
    """GET /api/nexus/imports — что и откуда мы взяли.

    Список переносов, свежие первыми: наш мод, мод-донор, архив с его версией, сколько
    строк предложено и сколько принято, по какому правилу и когда. Это и отчёт, и то, с
    чем не стыдно поделиться: чужой перевод без указания источника — чужая работа без
    имени автора.
    """
    try:
        from translator.nexus import provenance as prov
        rows = prov.summary(_repo().db)
        return jsonify({"ok": True, "count": len(rows), "imports": rows})
    except Exception as exc:
        return _fail(exc)


@bp.route("/imports/string/<int:string_id>")
def donor_import_of_string(string_id: int):
    """GET /api/nexus/imports/string/<id> — откуда взялась одна конкретная строка."""
    try:
        from translator.nexus import provenance as prov
        got = prov.import_of(_repo().db, string_id)
        if not got:
            return jsonify({"ok": True, "string_id": string_id, "import": None,
                            "note": "не от донора, или перенос был до учёта происхождения"})
        return jsonify({"ok": True, "string_id": string_id, "import": got})
    except Exception as exc:
        return _fail(exc)
