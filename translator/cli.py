"""
CLI entry point: nolvus-translate
"""

from __future__ import annotations
import logging
import sys
from pathlib import Path

import click

from translator.config import load_config


def _setup_logging(level: str, log_file: Path | None, to_console: bool):
    import logging.handlers

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    if to_console:
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(fmt)
        root.addHandler(ch)

    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)


@click.group()
@click.option("--config", "config_path", type=click.Path(), default=None,
              help="Path to config.yaml (default: auto-detect)")
@click.pass_context
def cli(ctx, config_path):
    """Nolvus Translator — automatic Skyrim mod localization pipeline."""
    cfg_file = Path(config_path) if config_path else None
    cfg = load_config(cfg_file) if cfg_file else load_config()
    _setup_logging(
        cfg.logging.level,
        cfg.paths.log_file,
        cfg.logging.log_to_console,
    )
    ctx.ensure_object(dict)
    ctx.obj["cfg"] = cfg


# ── ESP translation ────────────────────────────────────────────────────────────

@cli.command("translate-esp")
@click.argument("esp_file", type=click.Path(exists=True))
@click.option("--output", "-o", type=click.Path(), default=None,
              help="Output ESP path (default: overwrite input)")
@click.option("--mod-folder", type=click.Path(), default=None,
              help="Mod folder for Nexus context lookup")
@click.option("--dry-run", is_flag=True, help="Extract strings only, no write")
@click.pass_context
def translate_esp(ctx, esp_file, output, mod_folder, dry_run):
    """Translate strings embedded in an ESP/ESM/ESL plugin."""
    cfg = ctx.obj["cfg"]
    from scripts.esp_engine import cmd_translate

    esp_path    = Path(esp_file)
    out_path    = Path(output) if output else esp_path
    mod_path    = Path(mod_folder) if mod_folder else esp_path.parent

    cmd_translate(esp_path, out_path, mod_path, dry_run=dry_run)


# ── MCM translation ────────────────────────────────────────────────────────────

@cli.command("translate-mcm")
@click.argument("mod_folder", type=click.Path(exists=True))
@click.option("--dry-run", is_flag=True, help="Show what would be translated")
@click.pass_context
def translate_mcm(ctx, mod_folder, dry_run):
    """Translate MCM interface .txt files (loose and BSA-embedded)."""
    from scripts.translate_mcm import cmd_translate_mcm
    cmd_translate_mcm(Path(mod_folder), dry_run=dry_run)


# ── Batch mod translation ──────────────────────────────────────────────────────

@cli.command("translate-mod")
@click.argument("mod_folder", type=click.Path(exists=True))
@click.option("--dry-run", is_flag=True)
@click.pass_context
def translate_mod(ctx, mod_folder, dry_run):
    """Translate both ESP and MCM files for an entire mod folder."""
    mod_path = Path(mod_folder)

    # MCM
    try:
        from scripts.translate_mcm import cmd_translate_mcm
        cmd_translate_mcm(mod_path, dry_run=dry_run)
    except Exception as exc:
        click.echo(f"MCM translation error: {exc}", err=True)

    # ESP files
    try:
        from scripts.esp_engine import cmd_translate
        for esp_file in mod_path.rglob("*.esp"):
            click.echo(f"  ESP: {esp_file.name}")
            cmd_translate(esp_file, esp_file, mod_path, dry_run=dry_run)
        for esm_file in mod_path.rglob("*.esm"):
            click.echo(f"  ESM: {esm_file.name}")
            cmd_translate(esm_file, esm_file, mod_path, dry_run=dry_run)
    except Exception as exc:
        click.echo(f"ESP translation error: {exc}", err=True)


# ── Batch all mods in mods_dir ─────────────────────────────────────────────────

@cli.command("translate-all")
@click.option("--dry-run", is_flag=True)
@click.option("--resume", is_flag=True, help="Skip mods that already have a translation cache entry")
@click.pass_context
def translate_all(ctx, dry_run, resume):
    """Translate all mods in the configured mods_dir."""
    cfg      = ctx.obj["cfg"]
    mods_dir = cfg.paths.mods_dir

    if not mods_dir.is_dir():
        click.echo(f"mods_dir not found: {mods_dir}", err=True)
        sys.exit(1)

    mod_folders = sorted(d for d in mods_dir.iterdir() if d.is_dir())
    click.echo(f"Found {len(mod_folders)} mod folders in {mods_dir}")

    done_file = cfg.paths.translation_cache.parent / "translated_mods.txt"
    done: set[str] = set()
    if resume and done_file.exists():
        done = set(done_file.read_text(encoding="utf-8").splitlines())
        click.echo(f"Resuming: {len(done)} already done")

    for mod_folder in mod_folders:
        if resume and mod_folder.name in done:
            click.echo(f"  [skip] {mod_folder.name}")
            continue

        click.echo(f"\n{'='*60}")
        click.echo(f"  MOD: {mod_folder.name}")
        click.echo(f"{'='*60}")

        ctx.invoke(translate_mod, mod_folder=str(mod_folder), dry_run=dry_run)

        if not dry_run:
            with open(done_file, "a", encoding="utf-8") as f:
                f.write(mod_folder.name + "\n")


if __name__ == "__main__":
    cli()
