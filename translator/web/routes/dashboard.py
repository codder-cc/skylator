"""Dashboard — main overview page."""
from __future__ import annotations
from flask import Blueprint, current_app, jsonify, redirect, request

bp = Blueprint("dashboard", __name__)


@bp.route("/")
def index():
    # Browser request → redirect to React SPA
    if not request.headers.get("Accept", "").startswith("application/json"):
        return redirect("/app/")

    scanner = current_app.config["SCANNER"]
    jm      = current_app.config["JOB_MANAGER"]
    cfg     = current_app.config.get("TRANSLATOR_CFG")

    stats    = scanner.get_stats()
    jobs     = [j.to_dict() for j in jm.list_jobs(limit=10)]
    gpu_info = _gpu_info()

    return jsonify({"stats": stats, "jobs": jobs, "gpu_info": gpu_info})


def _gpu_info() -> dict:
    try:
        import torch
        if torch.cuda.is_available():
            dev = torch.cuda.current_device()
            total = torch.cuda.get_device_properties(dev).total_memory
            used  = torch.cuda.memory_allocated(dev)
            free  = total - used
            return {
                "name":       torch.cuda.get_device_name(dev),
                "total_mb":   total  // 1024 // 1024,
                "used_mb":    used   // 1024 // 1024,
                "free_mb":    free   // 1024 // 1024,
                "pct":        round(used / total * 100, 1) if total else 0,
                "available":  True,
            }
    except Exception:
        pass
    return {"available": False, "name": "Not available"}
