# MCM Translation File Format

## Location

Skyrim MCM (Mod Configuration Menu) interface strings live in:

```
<mod>/interface/translations/<ModName>_<language>.txt
```

or inside a BSA archive at the same relative path.

The game loads `_russian.txt` for Russian locale.  If it does not exist,
MCM falls back to `_english.txt` — meaning English text shows in menus.

## File encoding

- **UTF-16 LE** with BOM (`FF FE`)
- Line endings: `\r\n` (CRLF)
- Some older mods use UTF-8 or UTF-16 BE — the reader handles all three

## Line format

```
$KEY\tVALUE
```

- `$KEY` — internal identifier (never translated, starts with `$`)
- `\t` — literal tab character
- `VALUE` — display string (translate this)

Lines with no tab are treated as key-only (no value to translate).
Empty lines are skipped.

## Example

```
$sMCM_Title	SunHelm Survival
$sNeedsTab	Needs
$sHungerLabel	Hunger
$sHungerDesc	Your character is hungry. Eat food to reduce hunger.
```

Becomes after translation:

```
$sMCM_Title	SunHelm Survival
$sNeedsTab	Нужды
$sHungerLabel	Голод
$sHungerDesc	Ваш персонаж голоден. Съешьте еду, чтобы уменьшить голод.
```

Note: `$sMCM_Title` is a proper noun and kept in English here — the translator
may choose to keep mod names unchanged.

## BSA workflow

When the english file is packed inside a BSA:

```
1. BSArch.exe unpack <file.bsa> <temp_dir> -q -mt
2. Find *_english.txt in temp_dir
3. Translate → write *_russian.txt alongside in temp_dir
4. BSArch.exe pack <temp_dir> <file.bsa> -sse -mt
5. Clean up temp_dir
```

The original BSA is backed up to `mods_backup/` before repacking.

## MO2 virtual filesystem note

Loose files in the mod folder **override** BSA contents.  So an alternative
to repacking is dropping `*_russian.txt` as a loose file.  However, the
project deliberately uses the BSA workflow to keep the mod folder clean and
avoid conflicts with other mods that might also have loose translation files.

## Values NOT translated

- Values matching `^\d[\d\s.,\-+%]*$` (pure numbers/punctuation)
- Empty values
- Keys only (no tab in line)
