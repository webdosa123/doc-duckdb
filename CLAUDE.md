# CLAUDE.md — doc-duckdb

Working notes for any session that opens this folder. Read this before touching the
schema.

## What this repo is

A directory sweeper that turns documents into three tables of JSON Lines, and SQL that
reads them with DuckDB. A corpus becomes something you ask in SQL instead of writing a
fresh script for each question.

Every parser here is third-party open source under a licence a permissive release can
carry: `pypdfium2` (BSD-3 / Apache-2.0) for PDF, `python-docx` / `python-pptx` /
`openpyxl` (MIT) for OOXML, `duckdb` (MIT) to read the result.

## What this repo is not

- **No first-party engine.** No parser written here or elsewhere in this workspace goes
  into this tree, in source, as a binary, or as an optional backend. The sweep runs on
  third-party libraries only.
- **No measurement carried in from elsewhere.** Numbers in this repo are measured by this
  build, on a named corpus, with the command line shown. A number produced by a different
  parser is a number about a different parser, and it does not transfer. If you cannot
  measure it here, do not write it down here.
- **No hwp / hwpx.** Out of scope by owner ruling, not by licence.
- **No DuckDB extension.** A per-platform by per-version build matrix is not worth
  carrying. JSON Lines plus `read_json` is the whole interface.
- **No local paths, machine names, or account names in the tree.** Examples use `./corpus`
  and `./out`. Sweep output records the root you swept, but sweep output is data and does
  not get committed.

## The four rules

These are the reason the schema is worth anything. A change that breaks one of them is a
change to reject, not a trade-off to weigh.

**1. A document that failed to parse is a row carrying an error kind, never an absence.**
If a refused file is simply missing from `document`, then every rate you compute — pages
per document, blocks per page, share of files with a title — silently computes over the
survivors, and nothing in the output says so. Failures get a row with `ok = false` and an
`error_kind` from the closed vocabulary in `schema.py`.

**2. Every column a failed row cannot know is `null`, never `0` or `false`.**
A file refused at the door has an unknown page count, not zero pages. `block_count` on a
failed row is `null`; `block_count = 0` is a real answer that means the backend opened the
file and found no text. Those two states have to stay distinguishable, because
`avg(block_count)` over a corpus is wrong the moment they are not.

**3. The reader types its columns from every row, not from a sample.**
`read_json_auto` infers each column's type from a sample, and the head of a sweep does not
represent the file — it is whatever sorted first. Measured here: a block table whose first
rows were docx blocks typed all twelve coordinate columns as `JSON`, because docx blocks
have no coordinates. `avg(char_end - char_start)` then raised a binder error, and
`count(*) where box_y0 > 700` silently returned 38,247 against a true 38,497.

Note what did *not* happen, because the guessed version of this rule is easy to repeat and
wrong: `error_kind` on the document table typed correctly, and no refusal row was lost.
At 11,743 rows that file was inside the sample window. The hazard is real and it lands on
the block table first, because a block table passes the window after a few hundred
documents. Do not restate the rule as "the refusals disappear" without measuring it.

Use `sample_size=-1`, or better, pass the explicit `columns=` map this repo generates. The
generated views in `docduckdb/sql/` use that map, which is strictly stronger than the
sample rule: no inference at all. Hand-written queries in `queries/` show `sample_size=-1`
because that is what someone will type without the repo's help.

**4. No column whose name claims more than the backend can see.**
This is the one that binds hardest, because a stranger querying the table cannot see what
the column was standing on. A column named `tagged` reads in SQL as "a screen reader can
read this" while meaning, at best, "a `/StructTreeRoot` object exists". There is no
`tagged` column here, no `reading_order`, and no accessibility-shaped column at all.

The temptation is concrete, not hypothetical: `pypdfium2` exposes
`PdfDocument.is_tagged()`, so the column is one line of code away. pdfium cannot walk the
structure tree, cannot tell you the tags are correct, and cannot tell you the reading
order. The boolean is available and still the column is banned. Do not add it back.

The same rule decides several smaller calls already made:

- **No `encrypted` column.** A file that needs a password refuses to open, so we know it
  is encrypted — but a file encrypted with an owner password only opens normally and looks
  identical to an unencrypted one. A column that is right for refusals and wrong for
  everything else is worse than no column. What we actually know lives in
  `error_kind = 'password_required'`.
- **No `page_count` for `.docx`.** Word pagination is a rendering result; `python-docx`
  reads the stored XML and cannot see it. The column is `null` for every docx row. It is
  not `0`, and there is no substitute count quietly standing in for it.
- **`box_of`.** A box on a PDF block bounds the characters of that block. A box on a PPTX
  paragraph would bound the whole shape the paragraph sits in, which is a different claim.
  Both are emitted, and `box_of` says which one you are looking at: `text_chars` or
  `shape`.

## Layout

```
docduckdb/
  schema.py        column lists for the three tables, the error-kind vocabulary,
                   the DuckDB type map that the views are generated from
  sweep.py         directory walk, dispatch, timing, JSON Lines writer
  cli.py           sweep / query / load / schema subcommands
  backends/
    base.py        the shape every backend returns and the one exception it may raise
    pdf.py         pypdfium2
    docx.py        python-docx
    pptx.py        python-pptx
    xlsx.py        openpyxl
  sql/views.sql    generated; do not hand-edit, run `python -m docduckdb schema --write`
queries/           example SQL a person would type
tests/             fixture documents are generated, not committed
docs/schema.md     column-by-column reference, including what each backend cannot see
```

The package sits at the top of the repo rather than under `src/` so that
`python -m docduckdb` works in a clone with nothing installed but the four parser
libraries and duckdb.

## Conventions

- Python 3.10+, standard library plus the four parser libraries plus duckdb. No framework.
- Every backend returns the same `DocResult` shape and never raises past `sweep.py`; a
  backend exception becomes `error_kind = 'parse_failed'` with the exception type in
  `error_detail`.
- Every row is written with every key present. A missing key and a null key are different
  things to a reader, and only one of them is in the contract.
- Adding a column means: add it to `schema.py`, set it in every backend (explicitly to
  `None` where that backend cannot see it), regenerate the views, and add a line to
  `docs/schema.md` saying which backends can see it. If you cannot write that line, the
  column is not ready.
- Commits use the machine's git config identity and nothing else. No tool attribution
  lines, no generated-by trailers.
- Two views over the same rows: `document` and `block` are the latest sweep,
  `document_all` and `block_all` are every sweep. `doc_id` is a hash of the path, so it
  repeats across runs of the same root; unscoped, the obvious join doubles its result
  once a second sweep exists. Keep the plain names scoped.
- Performance changes belong in the code, not in the schema. Two already found:
  `paragraph.style.name` rescans the styles part on every call, so docx builds the style
  map once per document, and PDF byte offsets advance from the previous line instead of
  being measured from the start of the page.

## Status

Private, and licensed under Apache-2.0 (see `LICENSE`). Publishing is a separate decision
from building, and only publishing is hard to undo - a licence being in place does not
mean the repository has been made public.
