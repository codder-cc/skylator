"""
ESP record context extractor.
Reads the plugin file and builds a map of FormID → (record_type, edid, group_label)
so that each string being translated can be annotated with what it belongs to.
"""

from __future__ import annotations
import logging
import struct
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Record types we care about for context
_CONTEXT_TYPES = {
    b"NPC_", b"QUST", b"WEAP", b"ARMO", b"BOOK", b"ALCH",
    b"INGR", b"MISC", b"CONT", b"ACTI", b"FLOR", b"LIGH",
    b"MESG", b"PERK", b"MGEF", b"SPEL", b"FACT",
}


class RecordContext:
    """Per-record context for a single ESP string."""
    __slots__ = ("form_id", "rec_type", "edid", "group_label")

    def __init__(self, form_id: int, rec_type: str, edid: str, group_label: str):
        self.form_id    = form_id
        self.rec_type   = rec_type
        self.edid       = edid
        self.group_label = group_label

    def as_hint(self) -> str:
        parts = [f"[{self.rec_type}]"]
        if self.edid:
            parts.append(f"EDID:{self.edid}")
        if self.group_label:
            parts.append(f"Group:{self.group_label}")
        return " ".join(parts)


class EspContextExtractor:
    """
    Lightweight ESP scanner that builds a FormID→RecordContext map.
    Only reads EDID fields (editor IDs) and group labels — no full parse.
    """

    def __init__(self, esp_path: Path):
        self._path = esp_path
        self._ctx_map: dict[int, RecordContext] = {}
        self._parsed = False

    def get(self, form_id: int) -> Optional[RecordContext]:
        if not self._parsed:
            self._parse()
        return self._ctx_map.get(form_id)

    def all_records(self) -> dict[int, RecordContext]:
        if not self._parsed:
            self._parse()
        return dict(self._ctx_map)

    def _parse(self):
        self._parsed = True
        try:
            data = self._path.read_bytes()
        except OSError as exc:
            log.warning(f"EspContextExtractor: cannot read {self._path}: {exc}")
            return

        offset = 0
        total  = len(data)
        current_group_label = ""

        while offset + 24 <= total:
            rec_type = data[offset : offset + 4]

            if rec_type == b"GRUP":
                # GRUP: type(4) + size(4) + label(4) + group_type(4) + timestamp(2) + vc(2) + unknown(4)
                size        = struct.unpack_from("<I", data, offset + 4)[0]
                label_bytes = data[offset + 8 : offset + 12]
                group_type  = struct.unpack_from("<I", data, offset + 12)[0]

                if group_type == 0:
                    # Top-level group: label is record type
                    current_group_label = label_bytes.rstrip(b"\x00").decode("ascii", errors="replace")

                offset += 24
                continue

            # Regular record: type(4) + datasize(4) + flags(4) + formid(4) + timestamp(2) + vc(4+2) = 24
            if offset + 24 > total:
                break

            data_size = struct.unpack_from("<I", data, offset + 4)[0]
            flags     = struct.unpack_from("<I", data, offset + 8)[0]
            form_id   = struct.unpack_from("<I", data, offset + 12)[0]

            record_end = offset + 24 + data_size
            if record_end > total:
                break

            rec_type_str = rec_type.rstrip(b"\x00").decode("ascii", errors="replace")

            # Parse EDID subfield if this is a record type we want context for
            if rec_type in _CONTEXT_TYPES:
                edid = self._extract_edid(data, offset + 24, record_end, flags)
                self._ctx_map[form_id] = RecordContext(
                    form_id=form_id,
                    rec_type=rec_type_str,
                    edid=edid,
                    group_label=current_group_label,
                )

            offset = record_end

    @staticmethod
    def _extract_edid(data: bytes, start: int, end: int, flags: int) -> str:
        """Extract EDID subrecord value from a record's data section."""
        # Compressed records: we skip for now (EDID is rarely compressed)
        is_compressed = bool(flags & 0x00040000)
        if is_compressed:
            return ""

        offset = start
        while offset + 6 <= end:
            sub_type = data[offset : offset + 4]
            sub_size = struct.unpack_from("<H", data, offset + 4)[0]
            sub_data_start = offset + 6
            sub_data_end   = sub_data_start + sub_size

            if sub_data_end > end:
                break

            if sub_type == b"EDID":
                raw = data[sub_data_start : sub_data_end]
                return raw.rstrip(b"\x00").decode("utf-8", errors="replace")

            offset = sub_data_end

        return ""
