"""
Skylator Translation Server — standalone launcher.

Usage:
    python server.py
    python server.py --host 0.0.0.0 --port 8765
    python server.py --config /path/to/config.yaml
    python server.py --model-path /path/to/model.gguf --gpu-layers -1
    python server.py --no-mdns
    python server.py --log-level DEBUG

The server loads a GGUF model (via llama-cpp-python) and exposes a REST API
for batch translation. Supports both Windows (CUDA) and macOS (Metal GPU).
Announces itself on the LAN via mDNS (_skylator._tcp.local.) by default.
"""
from __future__ import annotations
import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))


def _setup_logging(level_name: str = "INFO") -> None:
    level   = getattr(logging, level_name.upper(), logging.INFO)
    fmt     = "%(asctime)s [%(levelname)s] %(name)s — %(message)s"
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(fmt, "%H:%M:%S"))
    handler.setLevel(level)
    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)
    for noisy in ("urllib3", "httpx", "filelock", "huggingface_hub",
                  "zeroconf", "multipart", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main():
    parser = argparse.ArgumentParser(
        description="Skylator Translation Server",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--host", default="0.0.0.0",
        help="Bind host (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port", type=int, default=8765,
        help="Bind port (default: 8765)",
    )
    parser.add_argument(
        "--config", default=None,
        help="Path to config.yaml (default: auto-detect server_config.yaml then config.yaml)",
    )
    parser.add_argument(
        "--model-path", default=None,
        help="Override GGUF model path (skips config model_b)",
    )
    parser.add_argument(
        "--gpu-layers", type=int, default=None,
        help="n_gpu_layers override (-1 = all layers on GPU, 0 = CPU only)",
    )
    parser.add_argument(
        "--no-mdns", action="store_true",
        help="Disable mDNS service announcement",
    )
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args()

    _setup_logging(args.log_level)
    log = logging.getLogger(__name__)

    # ── Load config ───────────────────────────────────────────────────────────
    model_cfg       = None
    translation_cfg = None

    config_file = Path(args.config) if args.config else None
    if config_file is None:
        for candidate in [ROOT / "server_config.yaml", ROOT / "config.yaml"]:
            if candidate.exists():
                config_file = candidate
                break

    if config_file and config_file.exists():
        from translator.config import load_config
        cfg             = load_config(config_file)
        model_cfg       = cfg.ensemble.model_b
        translation_cfg = cfg.translation
        log.info("Config loaded: %s", config_file)
    else:
        log.warning("No config file found — model must be specified via --model-path")

    # ── Override model path from CLI ──────────────────────────────────────────
    if args.model_path:
        import dataclasses
        from translator.config import ModelConfig
        model_cfg = ModelConfig(
            repo_id        = "",
            local_dir_name = str(Path(args.model_path).parent),
            gguf_filename  = Path(args.model_path).name,
            n_gpu_layers   = args.gpu_layers if args.gpu_layers is not None else -1,
        )
        log.info("Model override: %s", args.model_path)
    elif args.gpu_layers is not None and model_cfg is not None:
        import dataclasses
        model_cfg = dataclasses.replace(model_cfg, n_gpu_layers=args.gpu_layers)

    if model_cfg is None:
        log.error("No model configured. Provide --config or --model-path.")
        sys.exit(1)

    # ── Platform info ─────────────────────────────────────────────────────────
    import platform as _plat
    log.info("Platform: %s  Python: %s", _plat.system(), sys.version.split()[0])

    # ── Build FastAPI app ─────────────────────────────────────────────────────
    from translator.remote.server import create_server_app
    app = create_server_app(
        model_cfg       = model_cfg,
        translation_cfg = translation_cfg,
        mdns_enabled    = not args.no_mdns,
        mdns_host       = args.host if args.host != "0.0.0.0" else "",
        mdns_port       = args.port,
    )

    print(f"\n{'=' * 60}")
    print(f"  Skylator Translation Server")
    print(f"  http://{args.host}:{args.port}")
    print(f"  Docs: http://{args.host}:{args.port}/docs")
    print(f"  mDNS: {'disabled' if args.no_mdns else 'enabled (_skylator._tcp.local.)'}")
    print(f"{'=' * 60}\n")

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level.lower())


if __name__ == "__main__":
    main()
