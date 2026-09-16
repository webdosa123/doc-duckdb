"""Column definitions for the three tables.

This module is the single source of truth. The backends fill these keys, the writer
checks that every key is present on every row, and the DuckDB views are generated from
the type map here rather than inferred from the data.

Rule 2 lives in this file's shape: every column below is nullable, and a backend that
cannot see a column sets it to None explicitly. There is no default that fills an
unknown with 0 or false.
"""

from __future__ import annotations

# --------------------------------------------------------------------------------------
# error kinds - a closed vocabulary. A sweep that wants to report something not in this
# list adds it here first, so that a query written against the list stays correct.
# --------------------------------------------------------------------------------------

ERROR_KINDS = (
    "read_failed",            # stat or open refused by the filesystem
    "empty_file",             # zero bytes
    "too_large",              # above --max-bytes, not attempted
    "format_not_supported",   # a document format this build does not parse
    "wrong_format",           # extension says one thing, the bytes say another
    "password_required",      # the backend will not open it without a password
    "parse_failed",           # the backend raised while reading the content
)

FORMATS = ("pdf", "docx", "pptx", "xlsx")

# Extensions dispatched to a backend.
EXT_FORMAT = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".pptx": "pptx",
    ".xlsx": "xlsx",
    ".xlsm": "xlsx",
}

# Extensions recognised as documents but not parsed by this build. These still produce a
# document row (rule 1): a corpus that is half .doc should not look like a corpus of
# whatever happened to be parseable.
EXT_UNSUPPORTED = (
    ".doc", ".xls", ".ppt", ".hwp", ".hwpx",
    ".odt", ".ods", ".odp", ".rtf", ".pages", ".key", ".numbers",
)

# --------------------------------------------------------------------------------------
# tables
# --------------------------------------------------------------------------------------

SWEEP_COLUMNS: "dict[str, str]" = {
    "sweep_id": "VARCHAR",
    "label": "VARCHAR",            # free text, e.g. 'first_touch' / 'repeat'
    "root": "VARCHAR",
    "started_utc": "VARCHAR",
    "finished_utc": "VARCHAR",
    "walk_s": "DOUBLE",           # finding the files
    "elapsed_s": "DOUBLE",        # reading them; the two together are the run
    "jobs": "BIGINT",
    "hashed": "BOOLEAN",           # was content_sha256 computed
    "extensions": "VARCHAR",       # comma-joined set actually attempted
    "files_seen": "BIGINT",        # entries walked, documents or not
    "files_attempted": "BIGINT",   # rows written to document
    "files_ok": "BIGINT",
    "files_failed": "BIGINT",
    "blocks_written": "BIGINT",
    "source_bytes": "BIGINT",      # sum of size_bytes over attempted files
    "document_bytes": "BIGINT",    # size of this run's document.jsonl
    "block_bytes": "BIGINT",       # size of this run's block.jsonl
    "tool_version": "VARCHAR",
    "python_version": "VARCHAR",
    "platform": "VARCHAR",
    "backends": "JSON",            # {backend: version} for the libraries present
}

DOCUMENT_COLUMNS: "dict[str, str]" = {
    # identity and filesystem - knowable without a parser
    "sweep_id": "VARCHAR",
    "doc_id": "VARCHAR",           # sha256 of the relative path, first 16 hex
    "path": "VARCHAR",             # relative to the swept root, forward slashes
    "ext": "VARCHAR",
    "format": "VARCHAR",           # one of FORMATS, or null when nothing was dispatched
    "size_bytes": "BIGINT",
    "modified_utc": "VARCHAR",
    "content_sha256": "VARCHAR",   # null when unreadable or --no-hash

    # the attempt
    "backend": "VARCHAR",
    "backend_version": "VARCHAR",
    "ok": "BOOLEAN",
    "error_kind": "VARCHAR",       # null if and only if ok
    "error_detail": "VARCHAR",     # exception type and message; may contain a file path
    "elapsed_ms": "DOUBLE",

    # what the format states about itself - every one null on a failed row
    "format_version": "VARCHAR",   # '1.7' for pdf; null elsewhere
    "page_count": "BIGINT",        # pdf only. docx pagination is not in the file
    "slide_count": "BIGINT",       # pptx only
    "sheet_count": "BIGINT",       # xlsx only
    "block_count": "BIGINT",       # 0 is a real answer; null means unknown
    "char_count": "BIGINT",
    "title": "VARCHAR",
    "author": "VARCHAR",
    "subject": "VARCHAR",
    "keywords": "VARCHAR",
    "creator": "VARCHAR",
    "producer": "VARCHAR",
    "created_declared": "VARCHAR",   # as the document states it, unparsed
    "modified_declared": "VARCHAR",
}

BLOCK_COLUMNS: "dict[str, str]" = {
    "sweep_id": "VARCHAR",
    "doc_id": "VARCHAR",
    "block_index": "BIGINT",       # 0-based, document order

    # where it sits. A docx has no part: python-docx reads stored XML, and the page a
    # paragraph lands on is decided when Word lays the document out.
    "part_kind": "VARCHAR",        # 'page' | 'slide' | 'sheet' | null
    "part_index": "BIGINT",        # 0-based within the document
    "part_label": "VARCHAR",       # sheet name for xlsx; null elsewhere

    "block_kind": "VARCHAR",       # 'line' | 'paragraph' | 'table_cell' | 'cell'
    "text": "VARCHAR",
    "char_count": "BIGINT",

    # offsets into the text of the part, for backends that produce one text per part
    "char_start": "BIGINT",
    "char_end": "BIGINT",
    "byte_start": "BIGINT",        # UTF-8 bytes into the part text
    "byte_end": "BIGINT",

    # points, origin bottom-left of the part. box_of says what the box bounds, because
    # a PPTX paragraph's box is the box of the shape it sits in, which is a weaker claim
    # than the PDF box next to it in the same column.
    "box_x0": "DOUBLE",
    "box_y0": "DOUBLE",
    "box_x1": "DOUBLE",
    "box_y1": "DOUBLE",
    "box_of": "VARCHAR",           # 'text_chars' | 'shape' | null

    "style_name": "VARCHAR",       # docx/pptx, where the format states one
    "shape_name": "VARCHAR",       # pptx
    "cell_ref": "VARCHAR",         # xlsx, e.g. 'B7'
}

TABLES: "dict[str, dict[str, str]]" = {
    "sweep": SWEEP_COLUMNS,
    "document": DOCUMENT_COLUMNS,
    "block": BLOCK_COLUMNS,
}


def empty_row(table: str) -> dict:
    """A row with every key present and every value null.

    Backends start here and set what they can see. What they do not set stays null,
    which is the correct answer for a column the backend cannot see.
    """
    return {key: None for key in TABLES[table]}


def check_row(table: str, row: dict) -> None:
    """Raise if a row is missing a key or carries one the schema does not have.

    A missing key and a null key are different things to a reader and only one of them
    is in the contract.
    """
    expected = set(TABLES[table])
    got = set(row)
    if got != expected:
        missing = sorted(expected - got)
        extra = sorted(got - expected)
        raise ValueError(f"{table} row mismatch: missing={missing} unexpected={extra}")


def columns_clause(table: str) -> str:
    """The `columns = {...}` argument for DuckDB's read_json.

    Stronger than rule 3's sample_size=-1: no inference at all, so a column that is null
    in every row of a sweep still arrives with its declared type instead of collapsing.
    """
    inner = ", ".join(
        "'{}': '{}'".format(name, dtype) for name, dtype in TABLES[table].items()
    )
    return "{" + inner + "}"


def views_sql(out_glob: str = "out/*") -> str:
    """Generate the view definitions read by `query` and `load`.

    `document` and `block` are the latest sweep. `document_all` and `block_all` are every
    sweep in the output directory.

    The reason is a trap worth closing rather than documenting. doc_id is a hash of the
    path, so it is the same row identity in every sweep of the same root. Two sweeps of
    one folder and `document join block using (doc_id)` then returns each block twice,
    with the right column names and a quietly doubled count. Scoping the plain names to
    one sweep makes the obvious join correct, and cross-run work asks for it by name.
    """
    lines = [
        "-- Generated by `python -m docduckdb schema --write`. Do not hand-edit.",
        "--",
        "-- Columns are declared, not inferred. read_json_auto types each column from a",
        "-- sample, and the head of a sweep is whatever sorted first: on a block table",
        "-- that began with docx rows, every coordinate column came back JSON, because",
        "-- docx blocks have no coordinates. Arithmetic on them then fails, and a",
        "-- comparison quietly returns the wrong count.",
        "",
    ]
    lines += [
        "create or replace view sweep as",
        "select * from read_json(",
        "    '{}/sweep.jsonl',".format(out_glob),
        "    format = 'newline_delimited',",
        "    columns = {}".format(columns_clause("sweep")),
        ");",
        "",
    ]
    for table in ("document", "block"):
        lines += [
            "create or replace view {}_all as".format(table),
            "select * from read_json(",
            "    '{}/{}.jsonl',".format(out_glob, table),
            "    format = 'newline_delimited',",
            "    columns = {}".format(columns_clause(table)),
            ");",
            "",
        ]
    lines += [
        "-- The most recent sweep. Ties break on sweep_id, which carries the same clock.",
        "create or replace view latest_sweep as",
        "select sweep_id from sweep order by started_utc desc, sweep_id desc limit 1;",
        "",
    ]
    for table in ("document", "block"):
        lines += [
            "create or replace view {} as".format(table),
            "select * from {}_all".format(table),
            "where sweep_id = (select sweep_id from latest_sweep);",
            "",
        ]
    return "\n".join(lines)
