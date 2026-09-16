"""PDF, through pypdfium2.

A block here is a line, not a paragraph. pdfium delimits lines in the page text it
returns; it does not state where a paragraph begins. Grouping lines into paragraphs
would be a guess made from spacing, and a column called `paragraph` holding a guess is
the defect rule 4 exists to stop. So: `block_kind = 'line'`, and what the reader gets is
what the backend saw.

What pdfium cannot see, and what therefore has no column anywhere in this repo: the tag
structure, the reading order, any conformance claim, and whether a file that opened was
encrypted with an owner password.

Extraction is all or nothing. A document whose page 50 raises becomes one failed row
rather than a row reporting 49 pages of blocks as if that were the document. A partial
result recorded as a complete one is the survivorship problem moved inside a single file,
where it is even harder to see.
"""

from __future__ import annotations

import pypdfium2 as pdfium
from pypdfium2.version import PDFIUM_INFO, PYPDFIUM_INFO

from .base import BackendError, Extract, clean, detail

NAME = "pypdfium2"

_META_TO_COLUMN = {
    "Title": "title",
    "Author": "author",
    "Subject": "subject",
    "Keywords": "keywords",
    "Creator": "creator",
    "Producer": "producer",
    "CreationDate": "created_declared",
    "ModDate": "modified_declared",
}


def version() -> str:
    return "{} (pdfium {})".format(PYPDFIUM_INFO.version, PDFIUM_INFO.version)


def _open_error(exc: Exception) -> BackendError:
    message = str(exc).lower()
    if "password" in message:
        return BackendError("password_required", detail(exc))
    if "format" in message:
        return BackendError("wrong_format", detail(exc))
    if "file access" in message or "file error" in message:
        return BackendError("read_failed", detail(exc))
    return BackendError("parse_failed", detail(exc))


def _line_spans(text: str):
    """Yield (line_text, start, end) over the page text, skipping blank lines.

    Offsets are into the page text exactly as pdfium returned it, so a consumer can cut
    the same substring back out without re-running the extraction.
    """
    start = 0
    length = len(text)
    while start < length:
        end = text.find("\n", start)
        if end == -1:
            end = length
            nxt = length
        else:
            nxt = end + 1
        line_end = end
        if line_end > start and text[line_end - 1] == "\r":
            line_end -= 1
        line = text[start:line_end]
        if line.strip():
            yield line, start, line_end
        start = nxt


def _box_for(textpage, lo: int, hi: int):
    """Union of the character boxes over [lo, hi), in points, origin bottom-left.

    Returns None when no character in the range reported a usable box, which is a real
    outcome for text drawn in ways pdfium cannot place.
    """
    x0 = y0 = None
    x1 = y1 = None
    for index in range(lo, hi):
        try:
            left, bottom, right, top = textpage.get_charbox(index)
        except Exception:
            continue
        if left == right or bottom == top:
            continue
        if x0 is None or left < x0:
            x0 = left
        if y0 is None or bottom < y0:
            y0 = bottom
        if x1 is None or right > x1:
            x1 = right
        if y1 is None or top > y1:
            y1 = top
    if x0 is None:
        return None
    return (x0, y0, x1, y1)


def extract(path: str, boxes: bool = True) -> Extract:
    try:
        pdf = pdfium.PdfDocument(path)
    except pdfium.PdfiumError as exc:
        raise _open_error(exc)
    except OSError as exc:
        raise BackendError("read_failed", detail(exc))

    try:
        fields = {"page_count": len(pdf)}

        try:
            raw_version = pdf.get_version()
        except Exception:
            raw_version = None   # a header this build cannot read is not a failed file
        if raw_version:
            fields["format_version"] = "{}.{}".format(raw_version // 10, raw_version % 10)

        try:
            meta = pdf.get_metadata_dict()
        except Exception:
            meta = {}
        for key, column in _META_TO_COLUMN.items():
            fields[column] = clean(meta.get(key))

        blocks = []
        chars = 0
        for page_index in range(len(pdf)):
            page = pdf[page_index]
            textpage = page.get_textpage()
            try:
                text = textpage.get_text_range()
                # The character index pdfium uses for boxes lines up with this string
                # only when the two agree on length. When they do not, the boxes would
                # be off by an unknown amount, so they are left null rather than made up.
                placeable = boxes and len(text) == textpage.count_chars()
                # Byte offsets are advanced from the previous line rather than measured
                # from the start of the page, which would make a long page quadratic.
                seen_chars = 0
                seen_bytes = 0
                for line, start, end in _line_spans(text):
                    byte_start = seen_bytes + len(
                        text[seen_chars:start].encode("utf-8", "surrogatepass")
                    )
                    byte_end = byte_start + len(line.encode("utf-8", "surrogatepass"))
                    seen_chars, seen_bytes = end, byte_end
                    block = {
                        "part_kind": "page",
                        "part_index": page_index,
                        "block_kind": "line",
                        "text": line,
                        "char_count": len(line),
                        "char_start": start,
                        "char_end": end,
                        "byte_start": byte_start,
                        "byte_end": byte_end,
                    }
                    if placeable:
                        box = _box_for(textpage, start, end)
                        if box is not None:
                            block["box_x0"], block["box_y0"] = box[0], box[1]
                            block["box_x1"], block["box_y1"] = box[2], box[3]
                            block["box_of"] = "text_chars"
                    chars += len(line)
                    blocks.append(block)
            finally:
                textpage.close()
                page.close()

        fields["block_count"] = len(blocks)
        fields["char_count"] = chars
        return Extract(fields, blocks)
    except BackendError:
        raise
    except Exception as exc:
        raise BackendError("parse_failed", detail(exc))
    finally:
        pdf.close()
