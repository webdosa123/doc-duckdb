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
swept 11,743 files in 65.0s read + 1.6s walk - 10,475 ok, 1,268 failed, 821,026 blocks
store 406,015,795 bytes over source 2,016,381,418 bytes = 0.2014x
out/20260916T023840.716166Z-af95ae

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

Numbers from this build, on this machine, with the command lines shown. They are not
transferable to another parser or another corpus, and nothing measured elsewhere has been
copied in.

Machine: Windows 11, 20 logical cores, Python 3.10.10, pypdfium2 5.7.0 (pdfium
148.0.7776.0), python-docx 1.1.2, python-pptx 0.6.23, openpyxl 3.1.5, duckdb 1.0.0.

### Cost

Corpus: 4,005 documents, 881 MiB, on a local disk. 2,943 PDF (865 MiB, 24,888 pages),
1,046 docx, 16 pptx. All 4,005 parsed; 777,781 blocks.

```console
$ python -m docduckdb sweep <corpus> --out out --label first_touch
```

| Run | Workers | Hash + boxes | Walk | Read | Total | Per file | Store / source |
|---|---|---|---|---|---|---|---|
| first touch | 1 | yes | 2.6 s | 267.7 s | 270.3 s | 66.9 ms | 0.4236 |
| repeat | 1 | yes | 1.2 s | 189.3 s | 190.4 s | 47.3 ms | 0.4236 |
| repeat, `--jobs 8` | 8 | yes | 1.1 s | 76.9 s | 78.0 s | 19.2 ms | 0.4236 |
| repeat, `--no-hash --no-boxes` | 1 | no | 1.2 s | 133.0 s | 134.2 s | 33.2 ms | 0.3737 |

What these numbers do and do not say:

- **The pair is the measurement, not the first row.** This machine cannot flush the
  operating system's page cache, so "first touch" means the first read of these files in
  this session, not a guaranteed cold cache. The gap between the first two rows is 1.41x,
  and part of that is the cache rather than the work. Quoting either row on its own would
  be a different and less defensible claim; that is why both are here.
- **Runs 2 to 4 compare with each other, not with run 1.** All three read a corpus the
  operating system had already seen.
- **Eight workers gave 2.5x, not 8x**, on 20 logical cores. The work is a mix of file
  reading and CPython, and every row is still written by one parent process.
- **The hash and the boxes together cost 30%** of the warm single-process run and 12%
  of the store. `content_sha256` reads every byte of every file; the PDF boxes
  ask pdfium for a rectangle per character. Both are on by default and both have a flag.
- **The store ratio is a fact about the corpus, not about the tool.** 0.42x here, on
  text-dense PDFs. On the second corpus below, where half the PDF pages carry no text at
  all and a third of the bytes are in formats this build does not parse, the same build
  produced 0.20x.

### What a sweep finds

Second corpus: 11,743 documents, 1,923 MiB. 5,152 PDF, 4,885 docx, 565 pptx, and 1,141
files in formats this build does not parse. 821,026 blocks, store 0.2014x of source.

```console
$ python -m docduckdb query -f queries/02_failures.sql
```

| `error_kind` | Files | Share |
|---|---|---|
| (parsed) | 10,475 | 89.20% |
| `format_not_supported` | 1,141 | 9.72% |
| `wrong_format` | 90 | 0.77% |
| `password_required` | 21 | 0.18% |
| `parse_failed` | 15 | 0.13% |
| `empty_file` | 1 | 0.01% |

Every one of those 1,268 files is a row. Nine and a half percent of this corpus by count,
and a third by bytes, is hwp, hwpx, doc and key, which this build does not read. Had those
been dropped instead of recorded, the sweep would report 10,602 documents of which 98.8%
parsed, and nothing in the output would say that 1,141 files had been left out.

Three numbers from the same sweep that only exist because of rules 1 and 2:

- **3,937 documents opened and produced no text**, against **1,268 whose block count is
  unknown**. `block_count = 0` and `block_count = null` are different answers, and here
  they differ by a factor of three. A schema that wrote `0` for both would report 5,205
  empty documents, of which 1,268 were never opened.
- **13,222 of 27,100 PDF pages carry no extractable text.** Finding them needs the page
  list generated from `page_count`, because a page with no text has no rows in `block`:
  join the two tables and the pages you are looking for are exactly the ones the join
  drops. See [queries/04_pages_without_text.sql](queries/04_pages_without_text.sql).
- **Average PDF pages: 5.31 over the files that opened, 5.26 over the corpus.** A 1%
  difference, from a 0.89% refusal rate, on a corpus where the refusals are small files.
  It is small here and it is not always small, and the only reason it can be checked at
  all is that the refused rows are still in the table.

## Status

Private. Not released, and not licensed for release. Building it and publishing it are
separate decisions, and only the second one is hard to undo.
