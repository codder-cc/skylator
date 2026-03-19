# Nolvus Translator v2 — Project Overview

## Goal

Automatically translate all Skyrim mods in the **Nolvus Awakening** modpack from
English into Russian using local AI models.  The original trigger was Russian font
squares (missing glyphs) — fixing fonts turned out to require proper `.esp` string
patching and MCM interface file generation, which grew into a full localization
pipeline.

## What is being translated

| Target | Format | Location |
|--------|--------|----------|
| ESP/ESM/ESL plugins | Binary TES4 records | `mods/<ModName>/*.esp` |
| MCM interface files | UTF-16 LE `.txt` (`$KEY\tVALUE`) | `interface/translations/*_russian.txt` (loose or inside BSA) |

## Environment

| Item | Value |
|------|-------|
| Modpack | Nolvus Awakening |
| Mods dir | `H:/Nolvus/Instances/Nolvus Awakening/MODS/mods` |
| Backup dir | `H:/Nolvus/Instances/Nolvus Awakening/MODS/mods_backup` |
| BSArch | `H:/Nolvus/Instances/Nolvus Awakening/TOOLS/BSArch/BSArch.exe` |
| Model cache | `D:/DevSpace/AI/` |
| GPU | NVIDIA RTX 5080 (16 GB VRAM, Blackwell sm_120) |
| RAM | 32 GB |
| Project root | `H:/Nolvus/Translator/` |

## Quick start

```bat
cd H:\Nolvus\Translator
setup_venv.bat              # creates venv, installs torch cu128 + all deps

# activate venv
venv\Scripts\activate

# translate a single mod (ESP + MCM)
nolvus-translate translate-mod "H:\Nolvus\Instances\Nolvus Awakening\MODS\mods\A Cat's Life"

# translate everything (resumable)
nolvus-translate translate-all --resume
```

## Config

Copy `config.yaml.example` → `config.yaml` and fill in:

```yaml
nexus:
  api_key: "your_key_here"   # Nexus Mods personal API key
paths:
  mods_dir: "..."            # adjust if instance path differs
```

`config.yaml` is in `.gitignore` — it is never committed.
