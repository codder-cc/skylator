"""
Nolvus Translator Web UI — launch script.

Usage:
    python web_server.py                  # default: 127.0.0.1:5000
    python web_server.py --host 0.0.0.0   # listen on all interfaces
    python web_server.py --port 8080
    python web_server.py --debug
    python web_server.py --log-level DEBUG
"""
from __future__ import annotations
import argparse
import logging
import sys
import os
from pathlib import Path

# Make sure the project root is on sys.path
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))


def _setup_logging(level_name: str = "INFO") -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    fmt   = "%(asctime)s [%(levelname)s] %(name)s — %(message)s"
    datefmt = "%H:%M:%S"

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    handler.setLevel(level)

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)

    # Quiet truly noisy third-party loggers
    for noisy in ("urllib3", "httpx", "filelock", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # Filter werkzeug to suppress high-frequency polling endpoints from request log
    class _PollFilter(logging.Filter):
        _SKIP = ("/api/gpu", "/api/jobs", "/jobs/stream", ": ping")
        def filter(self, record):
            msg = record.getMessage()
            return not any(s in msg for s in self._SKIP)

    logging.getLogger("werkzeug").addFilter(_PollFilter())


def main():
    parser = argparse.ArgumentParser(description="Nolvus Translator Web UI")
    parser.add_argument("--host",      default="127.0.0.1")
    parser.add_argument("--port",      type=int, default=5000)
    parser.add_argument("--debug",     action="store_true")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        help="Console log verbosity (default: INFO)")
    args = parser.parse_args()

    _setup_logging(args.log_level)

    log = logging.getLogger(__name__)
    log.info("Starting Nolvus Translator Web UI")

    from translator.web.app import create_app
    app = create_app()

    print(f"\n{'='*60}")
    print(f"  Nolvus Translator Web UI")
    print(f"  http://{args.host}:{args.port}   [log level: {args.log_level}]")
    print(f"{'='*60}\n")

    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


if __name__ == "__main__":
    main()
