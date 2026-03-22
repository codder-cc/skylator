"""
Skylator Remote Worker — standalone launcher.

The worker starts with NO model unless --config / --model-path is given.
Models can be loaded / swapped at runtime via POST /model/load from the frontend.

Usage:
    python server.py                                           # no model, load on demand
    python server.py --model-path /path/to/model.gguf         # load at startup
    python server.py --config server_config.yaml              # load from config
    python server.py --host-url http://192.168.1.100:5000     # register with host
    python server.py --host 0.0.0.0 --port 8765
    python server.py --no-mdns --log-level DEBUG
"""
from __future__ import annotations
import argparse
import dataclasses
import logging
import sys
from pathlib import Path

_HERE = Path(__file__).parent.resolve()
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


def _setup_logging(level_name: str = "INFO") -> None:
    level   = getattr(logging, level_name.upper(), logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s — %(message)s", "%H:%M:%S"))
    handler.setLevel(level)
    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)
    for noisy in ("urllib3", "httpx", "filelock", "huggingface_hub",
                  "zeroconf", "multipart", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main():
    parser = argparse.ArgumentParser(
        description="Skylator Remote Inference Worker",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--config", default=None,
                        help="server_config.yaml (optional — model can also be\n"
                             "loaded at runtime via POST /model/load)")
    parser.add_argument("--model-path", default=None,
                        help="Direct path to .gguf file — loads model at startup")
    parser.add_argument("--gpu-layers", type=int, default=None,
                        help="-1 = all on GPU (default),  0 = CPU only")
    parser.add_argument("--backend", default="llamacpp",
                        choices=["llamacpp", "mlx"],
                        help="Backend type (default: llamacpp)")
    parser.add_argument("--no-mdns", action="store_true",
                        help="Disable mDNS service announcement")
    parser.add_argument("--host-url", default="",
                        help="Host Flask URL for reverse registration.\n"
                             "Example: http://192.168.1.100:5000\n"
                             "Remote polls host for inference chunks (pull-mode).\n"
                             "Only outbound connections needed — no port forwarding.")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    _setup_logging(args.log_level)
    log = logging.getLogger(__name__)

    # ── Optional startup model ─────────────────────────────────────────────────
    model_cfg    = None
    backend_type = args.backend

    config_file = Path(args.config) if args.config else None
    if config_file is None:
        for candidate in [_HERE / "server_config.yaml", _HERE / "config.yaml"]:
            if candidate.exists():
                config_file = candidate
                break

    if config_file and config_file.exists():
        from config import load_config
        cfg          = load_config(config_file)
        model_cfg    = cfg.ensemble.model_b
        backend_type = args.backend if args.backend != "llamacpp" else cfg.ensemble.backend_type
        log.info("Config loaded: %s", config_file)

    if args.model_path:
        from config import ModelConfig
        p = Path(args.model_path)
        model_cfg = ModelConfig(
            repo_id        = "",
            local_dir_name = str(p.parent),
            gguf_filename  = p.name,
            n_gpu_layers   = args.gpu_layers if args.gpu_layers is not None else -1,
        )
        backend_type = args.backend
        log.info("Model at startup: %s", args.model_path)
    elif args.gpu_layers is not None and model_cfg is not None:
        model_cfg = dataclasses.replace(model_cfg, n_gpu_layers=args.gpu_layers)

    if model_cfg is None:
        log.info("No startup model — POST /model/load to load one from the frontend")

    import platform as _plat
    log.info("Platform: %s  Python: %s", _plat.system(), sys.version.split()[0])

    from remote_server import create_server_app
    app = create_server_app(
        model_cfg    = model_cfg,
        backend_type = backend_type,
        mdns_enabled = not args.no_mdns,
        mdns_host    = args.host if args.host != "0.0.0.0" else "",
        mdns_port    = args.port,
        host_url     = args.host_url.strip(),
    )

    bind = f"http://{args.host}:{args.port}"
    print(f"\n{'=' * 60}")
    print(f"  Skylator Remote Worker  v2.0")
    print(f"  {bind}")
    print(f"  Docs: {bind}/docs")
    print(f"  Model cache: remote_worker/models_cache/")
    print(f"  mDNS: {'disabled' if args.no_mdns else 'enabled'}")
    if args.host_url:
        print(f"  Host: {args.host_url.strip()}  (pull-mode)")
    print(f"  Startup model: {model_cfg.gguf_filename if model_cfg else 'none (load via API)'}")
    print(f"{'=' * 60}\n")

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level.lower())


if __name__ == "__main__":
    main()
