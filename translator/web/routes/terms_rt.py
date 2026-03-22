"""Terminology editor — manage skyrim_terms.json."""
from __future__ import annotations
import json
from pathlib import Path
from flask import (Blueprint, abort, current_app, jsonify,
                   redirect, request)

bp = Blueprint("terms_rt", __name__, url_prefix="/terminology")


@bp.route("/")
def terms_page():
    if not request.headers.get("Accept", "").startswith("application/json"):
        return redirect("/app/terminology")
    return jsonify({"terms": _load_terms(current_app)})


@bp.route("/save", methods=["POST"])
def save_terms():
    data = request.get_json() or {}
    terms = data.get("terms", {})
    try:
        path = _terms_path(current_app)
        path.write_text(json.dumps(terms, ensure_ascii=False, indent=2), encoding="utf-8")
        return jsonify({"ok": True, "count": len(terms)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/add", methods=["POST"])
def add_term():
    data = request.get_json() or {}
    en   = data.get("en", "").strip()
    ru   = data.get("ru", "").strip()
    if not en or not ru:
        return jsonify({"error": "en and ru required"}), 400

    terms = _load_terms(current_app)
    terms[en] = ru
    _save(current_app, terms)
    return jsonify({"ok": True})


@bp.route("/delete", methods=["POST"])
def delete_term():
    data = request.get_json() or {}
    en   = data.get("en", "")
    terms = _load_terms(current_app)
    terms.pop(en, None)
    _save(current_app, terms)
    return jsonify({"ok": True})


def _terms_path(app) -> Path:
    cfg = app.config.get("TRANSLATOR_CFG")
    if cfg:
        return cfg.paths.skyrim_terms
    return Path(__file__).parent.parent.parent.parent / "data/skyrim_terms.json"


def _load_terms(app) -> dict:
    p = _terms_path(app)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(app, terms: dict):
    p = _terms_path(app)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(terms, ensure_ascii=False, indent=2), encoding="utf-8")
