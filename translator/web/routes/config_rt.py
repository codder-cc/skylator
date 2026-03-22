"""Config editor — view and save config.yaml via web."""
from __future__ import annotations
from pathlib import Path
from flask import (Blueprint, current_app, jsonify,
                   redirect, request)
import yaml

bp = Blueprint("config_rt", __name__, url_prefix="/config")

_CONFIG_FILE = Path(__file__).parent.parent.parent.parent / "config.yaml"


@bp.route("/")
def config_page():
    if not request.headers.get("Accept", "").startswith("application/json"):
        return redirect("/app/config")
    return jsonify({"yaml": _read_raw()})


@bp.route("/save", methods=["POST"])
def save_config():
    data     = request.get_json() or {}
    raw_yaml = data.get("yaml", "")
    if not raw_yaml:
        return jsonify({"error": "Empty YAML"}), 400

    # Validate YAML
    try:
        parsed = yaml.safe_load(raw_yaml)
    except yaml.YAMLError as exc:
        return jsonify({"error": f"YAML parse error: {exc}"}), 400

    # Save
    try:
        _CONFIG_FILE.write_text(raw_yaml, encoding="utf-8")
        # Reload config singleton
        import translator.config as tc
        tc._config = None
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/raw")
def raw_config():
    return current_app.response_class(
        _read_raw(), mimetype="text/plain"
    )


@bp.route("/validate", methods=["POST"])
def validate_config():
    data     = request.get_json() or {}
    raw_yaml = data.get("yaml", "")
    try:
        yaml.safe_load(raw_yaml)
        return jsonify({"ok": True})
    except yaml.YAMLError as exc:
        return jsonify({"ok": False, "error": str(exc)})


def _read_raw() -> str:
    if _CONFIG_FILE.exists():
        return _CONFIG_FILE.read_text(encoding="utf-8")
    example = _CONFIG_FILE.parent / "config.yaml.example"
    if example.exists():
        return example.read_text(encoding="utf-8")
    return "# config.yaml not found\n"
