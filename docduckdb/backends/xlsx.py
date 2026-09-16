"""Excel, through openpyxl.

One block per non-empty cell, carrying the cell reference, so a query can go straight
back to the place in the sheet.

The workbook is opened with `data_only=True`, which returns the value Excel cached the
last time it calculated the sheet. A formula cell that has never been calculated has no
cached value and produces no block: what is in the file is a formula, and this build does
not evaluate formulas. Nothing here reports a computed number as if it were stored.

A cell has no position on a page. Column widths and row heights are stated, but where a
sheet breaks into pages is decided when Excel prints it, so every box column is null.
"""

from __future__ import annotations

import zipfile

import openpyxl

from .base import BackendError, Extract, clean, detail, lib_version

NAME = "openpyxl"


def version() -> str:
    return lib_version("openpyxl")


def _core_properties(workbook) -> dict:
    fields = {}
    try:
        props = workbook.properties
    except Exception:
        return fields
    fields["title"] = clean(getattr(props, "title", None))
    fields["author"] = clean(getattr(props, "creator", None))
    fields["subject"] = clean(getattr(props, "subject", None))
    fields["keywords"] = clean(getattr(props, "keywords", None))
    for attr, column in (("created", "created_declared"), ("modified", "modified_declared")):
        value = getattr(props, attr, None)
        fields[column] = value.isoformat() if value is not None else None
    return fields


def extract(path: str, boxes: bool = True) -> Extract:
    try:
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except (zipfile.BadZipFile, KeyError) as exc:
        raise BackendError("wrong_format", detail(exc))
    except OSError as exc:
        raise BackendError("read_failed", detail(exc))
    except Exception as exc:
        raise BackendError("parse_failed", detail(exc))

    try:
        fields = _core_properties(workbook)
        blocks = []
        chars = 0
        sheets = 0
        for sheet_index, sheet in enumerate(workbook.worksheets):
            sheets += 1
            for row in sheet.iter_rows():
                for cell in row:
                    value = cell.value
                    if value is None:
                        continue
                    text = value if isinstance(value, str) else str(value)
                    if not text.strip():
                        continue
                    blocks.append(
                        {
                            "part_kind": "sheet",
                            "part_index": sheet_index,
                            "part_label": sheet.title,
                            "block_kind": "cell",
                            "text": text,
                            "char_count": len(text),
                            "cell_ref": cell.coordinate,
                        }
                    )
                    chars += len(text)

        fields["sheet_count"] = sheets
        fields["block_count"] = len(blocks)
        fields["char_count"] = chars
        return Extract(fields, blocks)
    except BackendError:
        raise
    except Exception as exc:
        raise BackendError("parse_failed", detail(exc))
    finally:
        try:
            workbook.close()
        except Exception:
            pass
