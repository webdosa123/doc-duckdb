"""Word, through python-docx.

The body is walked in stored order, so a paragraph inside a table arrives between the
paragraphs around the table rather than at the end.

There is no page_count for a .docx and there is no substitute standing in for one.
Pagination happens when Word lays the file out; it is not written down in the file, so
python-docx cannot see it and the column stays null.

Not read in this cycle: headers, footers, footnotes, endnotes, comments, text boxes.
Those are separate parts of the package and adding them is adding parts, not heuristics.
"""

from __future__ import annotations

import zipfile

import docx as python_docx
from docx.opc.exceptions import PackageNotFoundError
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

from .base import (
    BackendError, Extract, clean, detail, lib_version, package_error,
)

NAME = "python-docx"

_W_P = qn("w:p")
_W_TBL = qn("w:tbl")
_W_TR = qn("w:tr")
_W_TC = qn("w:tc")


def version() -> str:
    return lib_version("python-docx")


def _style_map(document) -> tuple:
    """(style id -> name, default paragraph style name), built once per document.

    `paragraph.style.name` looks the style up in the styles part on every call, which is
    a scan. On a document that defines a few thousand styles that is milliseconds per
    paragraph and minutes per file, so the map is built once and read per paragraph.
    """
    names = {}
    default = None
    try:
        for style in document.styles:
            style_id = getattr(style, "style_id", None)
            if style_id:
                names[style_id] = clean(style.name)
    except Exception:
        return names, default
    try:
        from docx.enum.style import WD_STYLE_TYPE

        fallback = document.styles.default(WD_STYLE_TYPE.PARAGRAPH)
        default = clean(fallback.name) if fallback is not None else None
    except Exception:
        pass
    return names, default


def _style_name(paragraph, names: dict, default: "str | None") -> "str | None":
    """The style the paragraph names, or the document default when it names none."""
    try:
        style_id = paragraph._p.style
    except Exception:
        return None
    if style_id is None:
        return default
    return names.get(style_id, clean(style_id))


def _walk(element, document, kind: str):
    """Yield (paragraph, block_kind) over a body or cell element, in stored order.

    Tables are walked as XML rather than through python-docx's row/cell view, which
    returns a merged cell once per grid position it spans. Counting that cell's text
    once per position would inflate both block_count and char_count on exactly the
    documents that use merges most.
    """
    for child in element.iterchildren():
        if child.tag == _W_P:
            yield Paragraph(child, document), kind
        elif child.tag == _W_TBL:
            for row in child.iterchildren(_W_TR):
                for cell in row.iterchildren(_W_TC):
                    for item in _walk(cell, document, "table_cell"):
                        yield item


def core_properties(source) -> dict:
    """The core properties an OOXML package states about itself.

    `creator` and `producer` stay null: the producing application is recorded in
    docProps/app.xml, which these libraries do not expose, and inventing a value from
    the core properties would put a different fact under the column's name.
    """
    fields = {}
    try:
        props = source.core_properties
    except Exception:
        return fields
    fields["title"] = clean(getattr(props, "title", None))
    fields["author"] = clean(getattr(props, "author", None))
    fields["subject"] = clean(getattr(props, "subject", None))
    fields["keywords"] = clean(getattr(props, "keywords", None))
    for attr, column in (("created", "created_declared"), ("modified", "modified_declared")):
        value = getattr(props, attr, None)
        fields[column] = value.isoformat() if value is not None else None
    return fields


def extract(path: str, boxes: bool = True) -> Extract:
    try:
        document = python_docx.Document(path)
    except PackageNotFoundError as exc:
        raise package_error(exc, path)
    except (zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise BackendError("wrong_format", detail(exc))
    except OSError as exc:
        raise BackendError("read_failed", detail(exc))
    except Exception as exc:
        raise BackendError("parse_failed", detail(exc))

    fields = core_properties(document)
    names, default_style = _style_map(document)
    blocks = []
    chars = 0
    for paragraph, kind in _walk(document.element.body, document, "paragraph"):
        text = paragraph.text
        if not text or not text.strip():
            continue
        blocks.append(
            {
                "block_kind": kind,
                "text": text,
                "char_count": len(text),
                "style_name": _style_name(paragraph, names, default_style),
            }
        )
        chars += len(text)

    fields["block_count"] = len(blocks)
    fields["char_count"] = chars
    return Extract(fields, blocks)
