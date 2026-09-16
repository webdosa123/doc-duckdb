"""What every backend returns, and the one exception it is allowed to raise.

A backend's job is to state what the file states about itself. It is not to guess. Any
field it cannot see it simply leaves out of the returned dict, which leaves it null.
"""

from __future__ import annotations

import os
from typing import NamedTuple


class BackendError(Exception):
    """A failure with an error_kind from the closed vocabulary in schema.ERROR_KINDS.

    Backends raise this for failures they recognise. Anything else that escapes a
    backend is caught by the sweep and recorded as 'parse_failed', so no file can
    vanish from the output because of an exception nobody anticipated.
    """

    def __init__(self, kind: str, detail: str = ""):
        super().__init__("{}: {}".format(kind, detail) if detail else kind)
        self.kind = kind
        self.detail = detail


class Extract(NamedTuple):
    """fields: a subset of schema.DOCUMENT_COLUMNS. blocks: subsets of BLOCK_COLUMNS."""

    fields: dict
    blocks: list


def clean(value) -> "str | None":
    """Normalise a string the format states about itself.

    Empty and whitespace-only become None: a title of "" is not a title, and counting it
    as one inflates every "how many documents carry a title" query.
    """
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    text = text.replace("\x00", "").strip()
    return text or None


def detail(exc: Exception) -> str:
    """Exception type and message, short enough to sit in a column."""
    return "{}: {}".format(type(exc).__name__, exc)[:500]


def package_error(exc: Exception, path: str) -> BackendError:
    """Classify an OOXML open failure.

    python-docx and python-pptx raise the same PackageNotFoundError whether the file is
    absent or simply is not a zip. The file existed a moment ago, when the sweep stat'd
    it, so the usual answer is wrong_format -- but the two cases get different kinds
    rather than one kind covering both, because `wrong_format` on a file that was deleted
    mid-sweep would be a wrong answer stated confidently.
    """
    if not os.path.exists(path):
        return BackendError("read_failed", detail(exc))
    return BackendError("wrong_format", detail(exc))


def lib_version(dist: str) -> str:
    """The installed version of a parser library, for the backend_version column."""
    try:
        from importlib.metadata import version

        return version(dist)
    except Exception:
        return "unknown"
