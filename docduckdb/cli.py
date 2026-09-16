"""Command line: sweep, query, load, schema."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, schema, sweep as sweep_module

SQL_PATH = Path(__file__).parent / "sql" / "views.sql"

SUMMARY_SQL = """
select
    coalesce(format, '(none)')                      as format,
    count(*)                                        as files,
    count(*) filter (where ok)                      as ok,
    count(*) filter (where not ok)                  as failed,
    sum(page_count)                                 as pages,
    sum(slide_count)                                as slides,
    sum(sheet_count)                                as sheets,
    sum(block_count)                                as blocks,
    round(sum(size_bytes) / 1048576.0, 1)           as source_mb
from document
group by 1
order by files desc
"""


def _views(connection, out: Path) -> None:
    glob = str(Path(out)).replace("\\", "/").rstrip("/") + "/*"
    connection.execute(schema.views_sql(glob))


def _connect(out: Path, database: str = ":memory:"):
    try:
        import duckdb
    except ImportError:
        sys.exit("duckdb is not installed: pip install duckdb")
    connection = duckdb.connect(database)
    if not any(Path(out).glob("*/document.jsonl")):
        sys.exit("no sweep output under {} - run `sweep` first".format(out))
    _views(connection, out)
    return connection


# --------------------------------------------------------------------------------------
# subcommands
# --------------------------------------------------------------------------------------

def cmd_sweep(args) -> int:
    root = Path(args.root)
    if not root.is_dir():
        sys.exit("not a directory: {}".format(root))

    extensions = None
    if args.ext:
        extensions = set()
        for item in args.ext.split(","):
            item = item.strip().lower()
            if not item:
                continue
            extensions.add(item if item.startswith(".") else "." + item)
        unknown = extensions - set(schema.EXT_FORMAT)
        if unknown:
            sys.exit("no backend for: {}".format(", ".join(sorted(unknown))))

    def progress(done, total):
        if args.quiet or not sys.stderr.isatty():
            return
        if done % 25 == 0 or done == total:
            sys.stderr.write("\r{}/{}".format(done, total))
            sys.stderr.flush()

    row = sweep_module.run(
        root=root,
        out=Path(args.out),
        label=args.label,
        extensions=extensions,
        jobs=max(1, args.jobs),
        hash_files=not args.no_hash,
        boxes=not args.no_boxes,
        max_bytes=args.max_bytes,
        progress=progress,
    )
    if not args.quiet and sys.stderr.isatty():
        sys.stderr.write("\r")

    store = row["document_bytes"] + row["block_bytes"]
    print(
        "swept {:,} files in {:.1f}s read + {:.1f}s walk"
        " - {:,} ok, {:,} failed, {:,} blocks".format(
            row["files_attempted"], row["elapsed_s"], row["walk_s"], row["files_ok"],
            row["files_failed"], row["blocks_written"],
        )
    )
    if row["source_bytes"]:
        print(
            "store {:,} bytes over source {:,} bytes = {:.4f}x".format(
                store, row["source_bytes"], store / row["source_bytes"]
            )
        )
    print(row["directory"])
    return 0


def cmd_query(args) -> int:
    connection = _connect(Path(args.out))
    if args.file:
        sql = Path(args.file).read_text(encoding="utf-8")
    elif args.sql:
        sql = args.sql
    else:
        sql = SUMMARY_SQL
    result = connection.sql(sql)
    if result is None:
        return 0
    if args.json:
        columns = [d[0] for d in result.description]
        for record in result.fetchall():
            print(json.dumps(dict(zip(columns, record)), ensure_ascii=False, default=str))
    else:
        result.show(max_rows=args.limit)
    return 0


def cmd_load(args) -> int:
    connection = _connect(Path(args.out), args.db)
    sources = {"sweep": "sweep", "document": "document", "block": "block"}
    if args.all:
        sources["document"] = "document_all"
        sources["block"] = "block_all"
    for table, source in sources.items():
        connection.execute("drop table if exists {}_t".format(table))
        connection.execute(
            "create table {}_t as select * from {}".format(table, source)
        )
    for view in ("document", "block", "latest_sweep", "document_all", "block_all",
                 "sweep"):
        connection.execute("drop view if exists {}".format(view))
    for table in sources:
        connection.execute("alter table {0}_t rename to {0}".format(table))
        count = connection.sql("select count(*) from {}".format(table)).fetchone()[0]
        print("{:<9} {:>12,} rows".format(table, count))
    connection.close()
    print(args.db)
    return 0


def cmd_schema(args) -> int:
    if args.write:
        SQL_PATH.parent.mkdir(parents=True, exist_ok=True)
        SQL_PATH.write_text(schema.views_sql(), encoding="utf-8")
        print(SQL_PATH)
        return 0
    if args.sql:
        print(schema.views_sql(str(Path(args.out)).replace("\\", "/") + "/*"))
        return 0
    for table, columns in schema.TABLES.items():
        print("{} ({} columns)".format(table, len(columns)))
        for name, dtype in columns.items():
            print("    {:<20} {}".format(name, dtype))
        print()
    print("error kinds: {}".format(", ".join(schema.ERROR_KINDS)))
    return 0


# --------------------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="doc-duckdb",
        description="Sweep a folder of documents into three tables, then ask it in SQL.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("sweep", help="walk a directory and write JSON Lines")
    p.add_argument("root")
    p.add_argument("--out", default="out", help="output directory (default: out)")
    p.add_argument("--label", default=None, help="free text recorded on the sweep row")
    p.add_argument("--ext", default=None, help="restrict to e.g. pdf,docx")
    p.add_argument("--jobs", type=int, default=1, help="worker processes (default: 1)")
    p.add_argument("--no-hash", action="store_true", help="skip content_sha256")
    p.add_argument("--no-boxes", action="store_true", help="skip block boxes")
    p.add_argument("--max-bytes", type=int, default=0, help="skip files above this size")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_sweep)

    p = sub.add_parser("query", help="run SQL over the sweep output")
    p.add_argument("sql", nargs="?", default=None)
    p.add_argument("--out", default="out")
    p.add_argument("-f", "--file", default=None, help="read SQL from a file")
    p.add_argument("--json", action="store_true", help="one JSON object per row")
    p.add_argument("--limit", type=int, default=40, help="rows to display (default: 40)")
    p.set_defaults(func=cmd_query)

    p = sub.add_parser("load", help="materialise the views into a database file")
    p.add_argument("--out", default="out")
    p.add_argument("--db", default="corpus.duckdb")
    p.add_argument("--all", action="store_true", help="every sweep, not just the latest")
    p.set_defaults(func=cmd_load)

    p = sub.add_parser("schema", help="print the columns, or regenerate views.sql")
    p.add_argument("--out", default="out")
    p.add_argument("--sql", action="store_true", help="print the view definitions")
    p.add_argument("--write", action="store_true", help="regenerate sql/views.sql")
    p.set_defaults(func=cmd_schema)

    return parser


def main(argv=None) -> int:
    # Document text is not ASCII and the console encoding is not the tool's business.
    # Without this a single character outside the code page ends the command, which is
    # the reader-side version of losing a row to something that is not about the row.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    return args.func(args)
