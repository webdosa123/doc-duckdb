# doc-duckdb

Sweep a folder of documents into three tables, then ask the folder questions in SQL.

```console
$ python -m docduckdb sweep ./corpus --out ./out
$ python -m docduckdb query "select format, count(*), sum(page_count) from document group by 1"
```

The sweep writes JSON Lines. DuckDB reads JSON Lines. There is no database file to
maintain and no extension to install, so the output of a sweep is three text files you can
copy, diff, or hand to someone who does not run this tool.

## Why

Asking a corpus a question usually means writing a script: open each file, pull out the
thing, count it. The next question means another script. The formats differ enough that
each script grows its own per-format branch, and none of them agree on what happens to the
files that would not open.

Three tables and a SQL prompt replace the scripts. The part worth more than the code is
what the tables promise about the files that failed, which is described under
[The four rules](#the-four-rules).

## What it reads

| Format | Library | Licence |
|---|---|---|
| `.pdf` | [pypdfium2](https://github.com/pypdfium2-team/pypdfium2) | BSD-3-Clause / Apache-2.0 |
| `.docx` | [python-docx](https://github.com/python-openxml/python-docx) | MIT |
| `.pptx` | [python-pptx](https://github.com/scanny/python-pptx) | MIT |
| `.xlsx` `.xlsm` | [openpyxl](https://foss.heptapod.net/openpyxl/openpyxl) | MIT |

Reading is done by [duckdb](https://duckdb.org) (MIT). Nothing else is required.

Files in formats this tool knows about but does not parse — `.doc`, `.xls`, `.ppt`,
`.hwp`, `.hwpx`, `.odt`, `.ods`, `.odp`, `.rtf` — still get a row, carrying
`error_kind = 'format_not_supported'`, so the shape of a corpus stays visible instead of
quietly shrinking to the part that happened to be parseable. Everything else in the
folder is counted in `sweep.files_seen` and otherwise ignored.

## Install

```console
$ pip install pypdfium2 python-docx python-pptx openpyxl duckdb
$ python -m docduckdb --help
```

Or `pip install -e .` for the `doc-duckdb` command. Python 3.10 or newer.

## Use

```console
# sweep a folder; one output directory per run, named by sweep id
$ python -m docduckdb sweep ./corpus --out ./out --label first_touch
swept 3,214 files in 41.8s — 3,180 ok, 34 failed, 214,884 blocks
out/20260916T101230Z-4f2a19/

# any SQL, against views over the sweep output
$ python -m docduckdb query "select error_kind, count(*) from document where not ok group by 1 order by 2 desc"

# the built-in summary, if you do not want to type SQL
$ python -m docduckdb query

# materialise into a real database file, for a tool that wants one
$ python -m docduckdb load --db corpus.duckdb

# every column, with its type, and the error-kind vocabulary
$ python -m docduckdb schema
```

`--out` accumulates. Each run writes `out/<sweep_id>/{document,block,sweep}.jsonl` and the
views read `out/*/`, so a second sweep does not overwrite the first and every row carries
the `sweep_id` that produced it. Two sweeps of the same folder is how you measure the same
corpus twice.

Because they accumulate, `document` and `block` are the latest sweep, and
`document_all` / `block_all` are every sweep. `doc_id` is a hash of the path, so it is the
same identity in every run of the same root: unscoped, `document join block using
(doc_id)` would return each block once per sweep, under the right column names and with a
quietly doubled count. `sweep` holds every run.

Useful flags: `--jobs N` for worker processes, `--no-hash` to skip the content hash (it
costs a full read of every file), `--ext pdf,docx` to narrow what is attempted,
`--max-bytes` to skip files above a size.

## The three tables

`document` — one row per file attempted, whether or not it parsed.
`block` — one row per paragraph, slide paragraph, or spreadsheet cell, with the text, where
it sits, and, for PDF, the byte range it occupies in the page's text and the box its
characters fall in.
`sweep` — one row per run: what was attempted, what it cost, which library versions did it.

Column-by-column reference, including what each backend cannot see, is in
[docs/schema.md](docs/schema.md). A sample of what the questions look like is in
[queries/](queries/).

```sql
-- PDF pages with no extractable text, the usual signature of a scan.
-- The page list comes from page_count, not from the blocks: a page with no text has no
-- block rows, so a join between the two tables drops exactly the pages being looked for.
with pages as (
    select doc_id, path, unnest(generate_series(0, page_count - 1)) as page_index
    from document where format = 'pdf' and ok and page_count > 0
),
text_on_page as (
    select doc_id, part_index, sum(char_count) as chars
    from block where part_kind = 'page' group by 1, 2
)
select p.path, count(*) as pages_without_text
from pages p
left join text_on_page t on t.doc_id = p.doc_id and t.part_index = p.page_index
where coalesce(t.chars, 0) = 0
group by 1
order by 2 desc;

-- what refused, and what share of the corpus that is
select error_kind,
       count(*) as files,
       round(100.0 * count(*) / sum(count(*)) over (), 2) as pct
from document
group by 1
order by files desc;
```

## The four rules

**1. A document that failed to parse is a row carrying an error kind, never an absence.**
Drop the refusals and every rate you compute is computed over the survivors, with nothing
in the output to say so.

**2. Every column a failed row cannot know is `null`, never `0` or `false`.**
A file refused at the door has an unknown page count, not zero pages. `block_count = 0` is
a real answer meaning the backend opened the file and found no text; `block_count = null`
means nobody knows. Collapsing the two makes every average wrong.

**3. The reader types its columns from every row, not from a sample.**
`read_json_auto` samples the head of a file. The head of a sweep is mostly successes, so
`error_kind` gets typed from nulls and the refusal rows disappear on the reader's side —
the rows rule 1 exists to keep. Pass `sample_size=-1`, or use the generated views, which
pass an explicit column map and infer nothing at all.

**4. No column whose name claims more than the backend can see.**
A column named `tagged` reads in SQL as "a screen reader can read this" and means, at
best, "an object of a certain name exists in the file". pdfium cannot walk a structure
tree, so there is no `tagged` column here, no `reading_order`, and nothing
accessibility-shaped at all. The same rule is why `.docx` rows have a `null` page count
rather than a plausible substitute, and why a PPTX box is labelled as the shape's box and
not the paragraph's.

Rules 1 to 3 are cheap to state and easy to skip; rule 4 is the one that costs something,
because the missing column is usually the one that would have looked best in a demo.

## What it will not tell you

By construction, not by omission. The libraries above read what the file states about
itself. They do not model a page, and they do not infer structure from how a page looks.

- No tag structure, no reading order, no accessibility verdict.
- No conformance claim of any kind (PDF/A and friends).
- No signature, encryption, or permissions detail. A file that demands a password is
  reported as `error_kind = 'password_required'`; a file encrypted with an owner password
  only opens normally and is indistinguishable from an unencrypted one, so no column
  pretends otherwise.
- No OCR. A scanned page yields zero blocks, which is a finding, not a failure.
- No table structure, no lists, no headings as a hierarchy. A heading is a paragraph with
  a style name where the format states one.
- No page count for `.docx`, because pagination happens when Word lays the file out and is
  not in the file.

## Measured on this build

<!-- MEASUREMENTS -->

## Status

Private. Not released, and not licensed for release. Building it and publishing it are
separate decisions, and only the second one is hard to undo.
