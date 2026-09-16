"""Walk a directory, read what can be read, write three JSON Lines files.

The whole of rule 1 is in `_attempt`: every path that reaches it produces exactly one
document row, whatever happens inside. There is no path through this module on which a
file is examined and then silently left out.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, backends, schema
from .backends.base import BackendError

DEFAULT_SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__",
    ".venv", "venv", ".tox", ".mypy_cache", ".pytest_cache",
}

HASH_CHUNK = 1 << 20


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------

def _utc(stamp: float) -> str:
    return datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_sweep_id() -> str:
    """Sortable, and unique even for two runs started in the same second.

    The microseconds are not decoration: `latest_sweep` orders runs by this clock, and a
    second-resolution stamp lets two runs tie and the wrong one win.
    """
    return "{}-{}".format(
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ"),
        os.urandom(3).hex(),
    )


def _doc_id(rel_path: str) -> str:
    return hashlib.sha256(rel_path.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def _content_sha256(path: str) -> "str | None":
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(HASH_CHUNK), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _scrub(value):
    """Make a value safe to put through json.dumps and then UTF-8.

    PDF text decoding can produce lone surrogates, which are valid Python strings and
    invalid UTF-8. Dropping the document over one of them would break rule 1 for a
    reason that has nothing to do with the document.
    """
    if isinstance(value, str):
        if "\x00" in value:
            value = value.replace("\x00", "")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            value = value.encode("utf-8", "replace").decode("utf-8")
    return value


class Writer:
    """Streams rows to <out>/<sweep_id>/<table>.jsonl, checking each one."""

    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.directory = directory
        self._handles = {}

    def write(self, table: str, row: dict) -> None:
        schema.check_row(table, row)
        handle = self._handles.get(table)
        if handle is None:
            handle = open(
                self.directory / "{}.jsonl".format(table), "w",
                encoding="utf-8", newline="\n",
            )
            self._handles[table] = handle
        clean = {key: _scrub(value) for key, value in row.items()}
        handle.write(json.dumps(clean, ensure_ascii=False, separators=(",", ":")))
        handle.write("\n")

    def size(self, table: str) -> int:
        handle = self._handles.get(table)
        if handle is not None:
            handle.flush()  # otherwise the size read back is the part already drained
        path = self.directory / "{}.jsonl".format(table)
        return path.stat().st_size if path.exists() else 0

    def close(self) -> None:
        for handle in self._handles.values():
            handle.close()
        self._handles.clear()


# --------------------------------------------------------------------------------------
# one file
# --------------------------------------------------------------------------------------

class Options:
    """Settings a worker process needs. Plain and picklable on purpose."""

    def __init__(self, hash_files=True, boxes=True, max_bytes=0):
        self.hash_files = hash_files
        self.boxes = boxes
        self.max_bytes = max_bytes


def _attempt(abs_path: str, rel_path: str, options: Options):
    """Read one file. Returns (document_fields, blocks) and never raises.

    document_fields is a partial document row: the sweep adds sweep_id afterwards.
    blocks is [] for a failure, never None, because there is nothing to write; the row's
    block_count stays null, which is what says the number is unknown rather than zero.
    """
    row = schema.empty_row("document")
    row["doc_id"] = _doc_id(rel_path)
    row["path"] = rel_path
    ext = os.path.splitext(rel_path)[1].lower()
    row["ext"] = ext

    started = time.perf_counter()

    try:
        stat = os.stat(abs_path)
        row["size_bytes"] = stat.st_size
        row["modified_utc"] = _utc(stat.st_mtime)
    except OSError as exc:
        row["ok"] = False
        row["error_kind"] = "read_failed"
        row["error_detail"] = "{}: {}".format(type(exc).__name__, exc)[:500]
        row["elapsed_ms"] = (time.perf_counter() - started) * 1000
        return row, []

    fmt = schema.EXT_FORMAT.get(ext)
    row["format"] = fmt

    def fail(kind: str, detail: "str | None" = None):
        row["ok"] = False
        row["error_kind"] = kind
        row["error_detail"] = detail
        row["elapsed_ms"] = (time.perf_counter() - started) * 1000
        return row, []

    if fmt is None:
        return fail("format_not_supported", "no backend for {}".format(ext or "(no extension)"))
    if row["size_bytes"] == 0:
        return fail("empty_file", None)
    if options.max_bytes and row["size_bytes"] > options.max_bytes:
        return fail("too_large", "{} bytes".format(row["size_bytes"]))

    backend = backends.get(fmt)
    if backend is None:
        return fail("format_not_supported", "library for {} is not installed".format(fmt))

    row["backend"] = backend.NAME
    row["backend_version"] = backend.version()

    if options.hash_files:
        row["content_sha256"] = _content_sha256(abs_path)

    try:
        extract = backend.extract(abs_path, boxes=options.boxes)
    except BackendError as exc:
        return fail(exc.kind, exc.detail or None)
    except Exception as exc:  # a backend is not allowed to take a file out of the table
        return fail("parse_failed", "{}: {}".format(type(exc).__name__, exc)[:500])

    row.update(extract.fields)
    row["ok"] = True
    row["elapsed_ms"] = (time.perf_counter() - started) * 1000
    return row, extract.blocks


_WORKER_OPTIONS = None


def _worker_init(options: Options) -> None:
    global _WORKER_OPTIONS
    _WORKER_OPTIONS = options


def _worker(task):
    abs_path, rel_path = task
    return _attempt(abs_path, rel_path, _WORKER_OPTIONS)


# --------------------------------------------------------------------------------------
# the walk
# --------------------------------------------------------------------------------------

def walk(root: Path, extensions: "set[str] | None" = None):
    """Yield (abs_path, rel_path, is_document) under root.

    is_document decides whether the file gets a row. Extensions this build parses and
    document extensions it does not both count; a .jpg does not.
    """
    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames if d not in DEFAULT_SKIP_DIRS and not d.startswith(".")
        ]
        dirnames.sort()
        for name in sorted(filenames):
            abs_path = os.path.join(dirpath, name)
            rel_path = os.path.relpath(abs_path, root).replace(os.sep, "/")
            ext = os.path.splitext(name)[1].lower()
            if ext in schema.EXT_FORMAT:
                wanted = extensions is None or ext in extensions
                yield abs_path, rel_path, wanted
            elif ext in schema.EXT_UNSUPPORTED:
                yield abs_path, rel_path, True
            else:
                yield abs_path, rel_path, False


def run(
    root: Path,
    out: Path,
    label: "str | None" = None,
    extensions: "set[str] | None" = None,
    jobs: int = 1,
    hash_files: bool = True,
    boxes: bool = True,
    max_bytes: int = 0,
    progress=None,
) -> dict:
    """Sweep root into out/<sweep_id>/ and return the sweep row."""
    root = Path(root).resolve()
    sweep_id = new_sweep_id()
    directory = Path(out) / sweep_id
    writer = Writer(directory)
    options = Options(hash_files=hash_files, boxes=boxes, max_bytes=max_bytes)

    files_seen = 0
    tasks = []
    walk_started = time.perf_counter()
    for abs_path, rel_path, is_document in walk(root, extensions):
        files_seen += 1
        if is_document:
            tasks.append((abs_path, rel_path))
    walk_elapsed = time.perf_counter() - walk_started

    started_wall = datetime.now(timezone.utc)
    started = time.perf_counter()

    counts = {"ok": 0, "failed": 0, "blocks": 0, "source_bytes": 0}

    def emit(row, blocks):
        row["sweep_id"] = sweep_id
        counts["ok" if row["ok"] else "failed"] += 1
        if row["size_bytes"]:
            counts["source_bytes"] += row["size_bytes"]
        writer.write("document", row)
        for index, partial in enumerate(blocks):
            block = schema.empty_row("block")
            block.update(partial)
            block["sweep_id"] = sweep_id
            block["doc_id"] = row["doc_id"]
            block["block_index"] = index
            writer.write("block", block)
        counts["blocks"] += len(blocks)
        if progress is not None:
            progress(counts["ok"] + counts["failed"], len(tasks))

    if jobs > 1 and tasks:
        with ProcessPoolExecutor(
            max_workers=jobs, initializer=_worker_init, initargs=(options,)
        ) as pool:
            for row, blocks in pool.map(_worker, tasks, chunksize=8):
                emit(row, blocks)
    else:
        for abs_path, rel_path in tasks:
            emit(*_attempt(abs_path, rel_path, options))

    elapsed = time.perf_counter() - started
    finished_wall = datetime.now(timezone.utc)

    sweep_row = schema.empty_row("sweep")
    sweep_row.update(
        {
            "sweep_id": sweep_id,
            "label": label,
            "root": str(root),
            "started_utc": started_wall.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "finished_utc": finished_wall.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "walk_s": walk_elapsed,
            "elapsed_s": elapsed,
            "jobs": jobs,
            "hashed": hash_files,
            "extensions": ",".join(sorted(extensions)) if extensions else ",".join(
                sorted(schema.EXT_FORMAT)
            ),
            "files_seen": files_seen,
            "files_attempted": counts["ok"] + counts["failed"],
            "files_ok": counts["ok"],
            "files_failed": counts["failed"],
            "blocks_written": counts["blocks"],
            "source_bytes": counts["source_bytes"],
            "document_bytes": writer.size("document"),
            "block_bytes": writer.size("block"),
            "tool_version": __version__,
            "python_version": platform.python_version(),
            "platform": "{} {}".format(platform.system(), platform.machine()),
            # The dict itself, not a dumped string: the column is declared JSON, and a
            # JSON string holding JSON reads back as VARCHAR, so `backends->>'pdf'`
            # quietly returns null instead of the version it names.
            "backends": backends.versions(),
        }
    )
    writer.write("sweep", sweep_row)
    writer.close()

    # The two row files are sized before sweep.jsonl is written, so the numbers above
    # are the real ones and not a guess at what the file would weigh once closed.
    sweep_row["directory"] = str(directory)
    return sweep_row
