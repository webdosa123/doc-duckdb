"""PowerPoint, through python-pptx.

Every block on a slide gets a box, and every one of those boxes is the box of the shape
the text sits in, not of the text itself. python-pptx reads the shape geometry stated in
the slide XML; it does not lay out the text inside the shape, so there is no paragraph
box to report. `box_of = 'shape'` says so in the row, next to PDF rows in the same
column whose boxes bound the characters themselves.

Boxes are converted from EMU to points with the origin moved to the bottom left of the
slide, so a box from this backend and a box from the PDF backend mean the same thing.

Not read in this cycle: speaker notes, slide masters and layouts, chart and SmartArt
text.
"""

from __future__ import annotations

import zipfile

from pptx import Presentation
from pptx.exc import PackageNotFoundError

from .base import BackendError, Extract, detail, lib_version, package_error
from .docx import core_properties

NAME = "python-pptx"

EMU_PER_POINT = 12700


def version() -> str:
    return lib_version("python-pptx")


def _box(shape, slide_height) -> dict:
    """Shape geometry in points, origin bottom-left, or nothing if it is not stated.

    left/top/width/height are None when the shape inherits its position from a
    placeholder, which python-pptx does not resolve. That is a null box, not a zero one.
    """
    try:
        left, top = shape.left, shape.top
        width, height = shape.width, shape.height
    except Exception:
        return {}
    if None in (left, top, width, height) or slide_height is None:
        return {}
    return {
        "box_x0": left / EMU_PER_POINT,
        "box_y0": (slide_height - (top + height)) / EMU_PER_POINT,
        "box_x1": (left + width) / EMU_PER_POINT,
        "box_y1": (slide_height - top) / EMU_PER_POINT,
        "box_of": "shape",
    }


def _shapes(container):
    """Flatten grouped shapes, keeping stored order."""
    for shape in container:
        if hasattr(shape, "shapes"):  # a group
            for inner in _shapes(shape.shapes):
                yield inner
        else:
            yield shape


def _paragraphs(shape, slide_height, slide_index):
    box = _box(shape, slide_height)
    name = getattr(shape, "name", None)

    frames = []
    if getattr(shape, "has_text_frame", False):
        frames.append((shape.text_frame, "paragraph"))
    if getattr(shape, "has_table", False):
        for row in shape.table.rows:
            for cell in row.cells:
                frames.append((cell.text_frame, "table_cell"))

    for frame, kind in frames:
        for paragraph in frame.paragraphs:
            text = paragraph.text
            if not text or not text.strip():
                continue
            block = {
                "part_kind": "slide",
                "part_index": slide_index,
                "block_kind": kind,
                "text": text,
                "char_count": len(text),
                "shape_name": name,
            }
            block.update(box)
            yield block


def extract(path: str, boxes: bool = True) -> Extract:
    try:
        presentation = Presentation(path)
    except PackageNotFoundError as exc:
        raise package_error(exc, path)
    except (zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise BackendError("wrong_format", detail(exc))
    except OSError as exc:
        raise BackendError("read_failed", detail(exc))
    except Exception as exc:
        raise BackendError("parse_failed", detail(exc))

    fields = core_properties(presentation)
    slide_height = presentation.slide_height if boxes else None

    blocks = []
    chars = 0
    slides = 0
    for slide_index, slide in enumerate(presentation.slides):
        slides += 1
        for shape in _shapes(slide.shapes):
            for block in _paragraphs(shape, slide_height, slide_index):
                blocks.append(block)
                chars += block["char_count"]

    fields["slide_count"] = slides
    fields["block_count"] = len(blocks)
    fields["char_count"] = chars
    return Extract(fields, blocks)
