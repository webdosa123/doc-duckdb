"""Backend registry.

A backend is missing, not broken, when its library is not installed: the sweep still
writes a row for every file it was asked to read, carrying
`error_kind = 'format_not_supported'`. A corpus swept on a machine without python-pptx
must not look like a corpus with no slides in it.
"""

from __future__ import annotations

import importlib

_MODULES = {
    "pdf": "docduckdb.backends.pdf",
    "docx": "docduckdb.backends.docx",
    "pptx": "docduckdb.backends.pptx",
    "xlsx": "docduckdb.backends.xlsx",
}

_cache: dict = {}


def get(fmt: str):
    """The backend module for a format, or None if its library is not installed."""
    if fmt not in _cache:
        try:
            _cache[fmt] = importlib.import_module(_MODULES[fmt])
        except Exception:
            _cache[fmt] = None
    return _cache[fmt]


def versions() -> dict:
    """{format: 'library version'} for the backends that are importable here."""
    out = {}
    for fmt in _MODULES:
        module = get(fmt)
        if module is not None:
            out[fmt] = "{} {}".format(module.NAME, module.version())
    return out
