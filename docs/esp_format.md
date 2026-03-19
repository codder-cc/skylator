# ESP Binary Format — Research Notes

All field definitions cross-referenced against:
- `xTranslator/Data/SkyrimSE/_recorddefs.txt`
- `xTranslator/TESVT_espDefinition.pas` (special EPFT conditions)
- `xTranslator/TESVT_VMAD.pas` (VMAD structure + property types)
- UESP wiki: Record types, VMAD, NOTE

## File structure

```
TES4 record (file header, 24 bytes + data)
  flags bit 0x80 = localized (strings stored in .STRINGS files, not embedded)

GRUP (group block)
  bytes 0-3:   "GRUP"
  bytes 4-7:   total size including this 24-byte header
  bytes 8-11:  label (record type for top-level groups, group_type=0)
  bytes 12-15: group_type
  bytes 16-23: timestamp + version

Record (inside GRUP)
  bytes 0-3:   record type (e.g. "NPC_")
  bytes 4-7:   data size (excludes 24-byte header)
  bytes 8-11:  flags (bit 0x00040000 = zlib compressed)
  bytes 12-15: FormID
  bytes 16-23: timestamp + version control info
  bytes 24+:   subrecords (possibly zlib compressed)
```

## Subrecord structure

```
bytes 0-3:  field type (e.g. "FULL")
bytes 4-5:  data size (uint16)
bytes 6+:   data

XXXX prefix (large fields > 65535 bytes):
  "XXXX" + uint16(4) + uint32(real_size)
  followed by: actual_field_type + uint16(0) + data[real_size]
```

## Translatable fields (from xTranslator _recorddefs.txt)

| Field | Record | Notes |
|-------|--------|-------|
| FULL | any | Full name |
| DESC | any | Description |
| DNAM | MGEF | Magic effect description |
| NAM1 | INFO | Dialogue response text |
| SHRT | NPC_ | NPC short name |
| CNAM | QUST | Quest description |
| CNAM | BOOK | Book author |
| TNAM | WOOP | Word of Power translation |
| NNAM | QUST | Quest next stage text |
| ITXT | MESG | Message box button text |
| RDMP | REGN | Region map name |
| RNAM | ACTI | Activator prompt verb |
| RNAM | FLOR | Flora prompt verb |
| RNAM | INFO | Dialogue response label |
| BPTN | BPTD | Body part node name |
| MNAM | FACT | Faction male rank name |
| FNAM | FACT | Faction female rank name |
| DESC | LSCR | Load screen text |

## Special conditional fields

### GMST:DATA
Extract as string **only when** `EDID` starts with `'s'`.
(GMSTs starting with 's' are string game settings; others are float/int.)

### PERK:EPFD (xTranslator proc2)
Extract **only when** preceding `EPFT` subrecord byte `== 7`.
(EPFT 7 = string type. Was wrongly coded as 5 — fixed after reading TESVT_espDefinition.pas.)

### PERK:EPF2 (xTranslator proc4)
Extract **only when** preceding `EPFT` subrecord byte `== 4`.
(EPFT 4 = script property. Was unconditional — fixed.)

### NOTE:TNAM
Extract **only when** `DATA` subrecord first byte `== 1` (text note type).
(NOTE DATA types: 0=sound, 1=text, 2=image, 3=voice. From UESP wiki.)

## VMAD (Papyrus script properties)

Source: `xTranslator/TESVT_VMAD.pas` + UESP VMAD page.

```
VMAD header:
  uint16  version      (2–5; Skyrim SE uses 5)
  uint16  objFormat    (1 or 2)
  uint16  scriptCount

per script:
  len-string  scriptName   (uint16 length + bytes, no null terminator)
  uint8       status       (ONLY if version >= 4)
  uint16      propertyCount

per property:
  len-string  propName
  uint8       propType
  uint8       status       (ONLY if version >= 4)
  <data depending on propType>

propType values:
  0  = null
  1  = object   (8 bytes)
  2  = string   (len-string)  ← EXTRACT
  3  = int32    (4 bytes)
  4  = float32  (4 bytes)
  5  = bool     (1 byte)
  11 = object[] (uint32 count + count*8)
  12 = string[] (uint32 count + count*len-strings)  ← EXTRACT
  13 = int32[]  (uint32 count + count*4)
  14 = float32[] (uint32 count + count*4)
  15 = bool[]   (uint32 count + count*1)
  (types 6–10 do not exist)
```

> **Important:** Version check for `status` byte applies to **both** the
> per-script status byte and the per-property status byte.  VMAD fragments
> (after scripts section) contain only script names — no translatable strings.

## trans_map key structure

Regular fields:
```python
key = (form_id_str, rec_type_str, field_type_str, field_index)
# e.g. ("0001A2B3", "NPC_", "FULL", 2)
value = "translated string"
```

VMAD fields (multiple strings per VMAD subrecord):
```python
key = (form_id_str, rec_type_str, "VMAD", field_index)
value = {vmad_str_idx: "translated string", ...}
```

## GRUP size recalculation

When string sizes change (translated text length ≠ original):
- Subrecord `size` field (2 bytes, uint16) is updated automatically by `build_subrecords()`
- Record `data_size` field (4 bytes) is updated by `Record.to_bytes(new_payload)`
- GRUP `total_size` (4 bytes) is updated by `rewrite_esp()` which recurses and rebuilds

## Localized plugins (flag 0x80)

When TES4 flags bit 7 is set, string fields contain a 4-byte string ID instead
of null-terminated text. The actual strings live in `.STRINGS`, `.DLSTRINGS`,
`.ILSTRINGS` files alongside the plugin.

Current implementation: localized strings are **detected and skipped** (logged
as `[LOC:NNNN]`).  Full `.STRINGS` file patching is a future task.
