"""Translate From Mod — the whole flow, from a mod id to strings in the store.

    find -> download -> unpack -> harvest -> plan -> apply -> clean up

Each step is a function of the one before it and every step is separately callable, so
the UI can stop after `plan` and show what would happen. The default is one-shot: the
archive and everything unpacked from it are deleted once the strings are out, because a
donor is a source of text, not something to keep a copy of. `keep_archive=True` is there
for the case where the same donor will be merged into several mods.

Only plugins and their string files are unpacked. A donor is often a 400 MB upload whose
translated text is forty kilobytes of it, and writing the other 399 MB to disk to read
past it is a waste nobody notices until the disk is full.
"""
from __future__ import annotations

import logging
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from translator.nexus import DownloadRequest, build_manager
from translator.nexus.archive import cleanup as rm_tree
from translator.nexus.archive import extract
from translator.nexus.errors import NexusError

log = logging.getLogger(__name__)

# Plugins, plus the sibling tables a localized plugin keeps its text in. Anything else
# in the archive is textures and meshes as far as this is concerned.
WANTED_SUFFIXES = (".esp", ".esm", ".esl", ".strings", ".ilstrings", ".dlstrings",
                   # MCM text is as often inside a .bsa as loose beside it, and SWF
                   # carries the interface text; both are small in a translation mod.
                   ".bsa", ".swf", ".txt")

ProgressCb = Callable[[str, str], None]          # (step, message)


@dataclass
class ImportReport:
    """Everything that happened, in the order it happened."""
    mod_name:   str
    language:   str
    donor_mod_id: Optional[int] = None
    donor_name: str = ""
    archive:    str = ""
    archive_bytes: int = 0
    extracted:  dict = field(default_factory=dict)
    harvest:    dict = field(default_factory=dict)
    plan:       Optional[object] = None
    applied:    dict = field(default_factory=dict)
    cleaned:    bool = False
    elapsed:    float = 0.0
    error:      str = ""

    def as_dict(self, sample: int = 200) -> dict:
        return {
            "mod_name": self.mod_name, "language": self.language,
            "donor_mod_id": self.donor_mod_id, "donor_name": self.donor_name,
            "archive": self.archive, "archive_bytes": self.archive_bytes,
            "extracted": self.extracted, "harvest": self.harvest,
            "plan": self.plan.as_dict(sample) if self.plan is not None else None,
            "applied": self.applied, "cleaned": self.cleaned,
            "elapsed": round(self.elapsed, 2), "error": self.error,
        }


def _noop(step: str, message: str) -> None:
    log.info("[%s] %s", step, message)


def download_donor(
    cfg,
    mod_id: int,
    dest_dir: Path,
    file_id: Optional[int] = None,
    on_progress: Optional[Callable] = None,
) -> Path:
    """Fetch one donor archive and return the file on disk.

    Reuses the same manager the batch downloader uses, so the donor comes down the same
    verified, resumable path as everything else -- including the browser link route that
    makes this unattended on a free account.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    mgr = build_manager(cfg, dest_dir=dest_dir,
                        on_event=(lambda item: on_progress(item)) if on_progress else None)
    mgr.submit([DownloadRequest(mod_id=mod_id, file_id=file_id,
                                label=f"donor {mod_id}")])
    mgr.run(block=True)

    snap = mgr.snapshot()
    item = (snap.get("items") or [{}])[0]
    if item.get("state") not in ("done", "skipped"):
        raise NexusError(f"could not download donor mod {mod_id}: "
                         f"{item.get('error') or item.get('state')}")
    path = (item.get("result") or {}).get("path")
    if not path:
        raise NexusError(f"donor mod {mod_id} downloaded but reported no file")
    return Path(path)


def strings_from_archive(
    archive: Path,
    work_dir: Optional[Path] = None,
    tool_path: Optional[str] = None,
    bsarch_exe: Optional[str] = None,
    ffdec_jar: Optional[str] = None,
    include_mcm: bool = True,
    include_swf: bool = True,
) -> tuple[list, dict, Path]:
    """Unpack an archive and parse every plugin in it.

    Returns (plugins, extract_summary, work_dir). The caller owns `work_dir` and is
    expected to remove it; that is deliberate, because the plugins hold paths into it.
    """
    from translator.nexus import harvest as harvest_mod

    work = Path(work_dir) if work_dir else Path(
        tempfile.mkdtemp(prefix="skylator_donor_"))
    ex = extract(archive, work, tool_path=tool_path, only_suffixes=WANTED_SUFFIXES)
    plugins = harvest_mod.harvest(work, bsarch_exe=bsarch_exe, ffdec_jar=ffdec_jar,
                                  include_mcm=include_mcm, include_swf=include_swf)
    return plugins, ex.as_dict(), work


def run(
    cfg,
    repo,
    mod_name: str,
    donor_mod_id: Optional[int] = None,
    archive_path: Optional[Path] = None,
    donor_file_id: Optional[int] = None,
    language: str = "Russian",
    apply_changes: bool = False,
    overwrite: bool = False,
    status: str = "needs_review",
    only_keys: Optional[list] = None,
    esp_map: Optional[dict] = None,
    keep_archive: bool = False,
    include_mcm: bool = True,
    include_swf: bool = True,
    global_dict=None,
    staging_dir: Optional[Path] = None,
    on_progress: Optional[ProgressCb] = None,
) -> ImportReport:
    """Run the flow end to end.

    With `apply_changes=False` this stops after planning and writes nothing, which is
    what the UI calls first: the plan carries every conflict, so the decision to
    overwrite finished work is made by a person looking at it, not by a default.
    """
    from translator.nexus import merge as merge_mod

    say = on_progress or _noop
    started = time.monotonic()
    report = ImportReport(mod_name=mod_name, language=language,
                          donor_mod_id=donor_mod_id)
    work: Optional[Path] = None
    downloaded_here = False

    try:
        # -- 1. get the archive ------------------------------------------------
        if archive_path:
            archive = Path(archive_path)
            if not archive.exists():
                raise NexusError(f"archive not found: {archive}")
        elif donor_mod_id:
            staging = Path(staging_dir) if staging_dir else Path(
                cfg.nexus.download_dir) / "_donors"
            say("download", f"fetching donor mod {donor_mod_id}")
            archive = download_donor(cfg, donor_mod_id, staging, file_id=donor_file_id)
            downloaded_here = True
        else:
            raise ValueError("need either donor_mod_id or archive_path")

        report.archive = str(archive)
        report.archive_bytes = archive.stat().st_size
        report.donor_name = archive.stem

        # -- 2. unpack and parse ----------------------------------------------
        say("extract", f"unpacking {archive.name}")
        plugins, extracted, work = strings_from_archive(
            archive,
            tool_path  = getattr(cfg.nexus, "archive_tool_path", "") or None,
            # BSArch and FFDec are what turn MCM and interface text from "not
            # harvested" into "harvested"; without them the flow simply yields less.
            bsarch_exe = str(getattr(cfg.paths, "bsarch_exe", "") or "") or None,
            ffdec_jar  = str(getattr(cfg.paths, "ffdec_jar", "") or "") or None,
            include_mcm = include_mcm, include_swf = include_swf)
        report.extracted = extracted

        from translator.nexus import harvest as harvest_mod
        report.harvest = harvest_mod.summarise(plugins)
        say("harvest", f"{report.harvest['string_count']} strings in "
                       f"{report.harvest['plugin_count']} plugin(s)")
        if not report.harvest["string_count"]:
            raise NexusError(
                f"{archive.name} carries no readable strings. Checked plugins, MCM "
                f"tables (loose and inside .bsa) and SWF interface text"
                + ("" if getattr(cfg.paths, "ffdec_jar", None) else
                   "; FFDec is not configured, so SWF was skipped")
                + ("" if getattr(cfg.paths, "bsarch_exe", None) else
                   "; BSArch is not configured, so .bsa contents were skipped") + ".")

        # -- 3. plan -----------------------------------------------------------
        say("plan", f"matching against {mod_name}")
        report.plan = merge_mod.plan(repo, mod_name, plugins, language=language,
                                     esp_map=esp_map)
        say("plan", f"{report.plan.counts}")

        # -- 4. apply ----------------------------------------------------------
        if apply_changes:
            say("apply", f"writing as {status}"
                         + (" (overwriting existing translations)" if overwrite else ""))
            report.applied = merge_mod.apply(
                repo, report.plan, overwrite=overwrite, status=status,
                only_keys=[tuple(k) for k in only_keys] if only_keys else None,
                global_dict=global_dict)

    except Exception as exc:                        # noqa: BLE001 - reported, not raised
        report.error = f"{type(exc).__name__}: {exc}"
        log.warning("translate-from-mod failed for %s: %s", mod_name, exc)
        raise
    finally:
        # The unpacked tree is always disposable -- the strings are in the report or in
        # the store by now. The archive is only removed when this call fetched it and
        # the caller did not ask to keep it.
        if work is not None:
            report.cleaned = rm_tree(work)
        if downloaded_here and not keep_archive and report.archive:
            try:
                Path(report.archive).unlink(missing_ok=True)
            except OSError as exc:
                log.warning("could not remove donor archive: %s", exc)
        report.elapsed = time.monotonic() - started

    return report
