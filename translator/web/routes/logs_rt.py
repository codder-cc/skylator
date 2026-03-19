"""Log viewer — tail log file and stream via SSE."""
from __future__ import annotations
import time
from pathlib import Path
from flask import (Blueprint, Response, current_app,
                   render_template, stream_with_context)

bp = Blueprint("logs_rt", __name__, url_prefix="/logs")


@bp.route("/")
def logs_page():
    cfg = current_app.config.get("TRANSLATOR_CFG")
    log_path = str(cfg.paths.log_file) if cfg else "logs/translator.log"
    # Read last 200 lines
    lines = _tail(Path(log_path), 200)
    return render_template("logs.html", lines=lines, log_path=log_path)


@bp.route("/stream")
def stream_logs():
    """SSE stream — tail the log file in real time."""
    cfg      = current_app.config.get("TRANSLATOR_CFG")
    log_path = cfg.paths.log_file if cfg else Path("logs/translator.log")

    @stream_with_context
    def generate():
        pos = log_path.stat().st_size if log_path.exists() else 0
        while True:
            try:
                if log_path.exists():
                    size = log_path.stat().st_size
                    if size > pos:
                        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                            f.seek(pos)
                            new_data = f.read()
                            pos = f.tell()
                        for line in new_data.splitlines():
                            line = line.strip()
                            if line:
                                yield f"data: {line}\n\n"
            except Exception:
                pass
            time.sleep(1)
            yield ": ping\n\n"

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no"})


def _tail(path: Path, n: int) -> list[str]:
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        return [l.rstrip() for l in lines[-n:]]
    except Exception:
        return []
