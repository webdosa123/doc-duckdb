# Schema

Three tables. Every column is nullable, and null means the backend could not see it —
never zero, never false, never a plausible substitute.

The column list in [docduckdb/schema.py](../docduckdb/schema.py) is the source of truth;
the views in `docduckdb/sql/views.sql` are generated from it. Regenerate them with
`python -m docduckdb schema --write` after any change here.

A short way to read the "seen by" column below: if a format is not listed, that column is
null for every row of that format, and the reason is in the notes.

---

## The views

`python -m docduckdb query` and `load` build these over `out/*/`:

| View | Contents |
|---|---|
| `sweep` | every run in the output directory |
| `document`, `block` | the latest run only |
| `document_all`, `block_all` | every run |
| `latest_sweep` | the one `sweep_id` the plain views are scoped to |

`document` and `block` are scoped to one sweep on purpose. `doc_id` is a hash of the
path, so it is the same row identity in every sweep of the same root: after two runs,
`document join block using (doc_id)` would return each block twice, under the right
column names and with a quietly doubled count. Scoping the plain names to one run makes
the obvious join the correct one, and cross-run work asks for `_all` by name and joins on
`sweep_id` as well as `doc_id`.

`--out` accumulates, so two sweeps of one folder sit side by side and can be compared:
that is how a first-touch run and a repeat run are paired.

---

## `document`

One row per file the sweep attempted, whether or not it parsed. That is rule 1: a
document that failed is a row carrying an error kind, never an absence.

### Identity and filesystem

Knowable without opening the file, so present on failed rows too.

| Column | Type | Seen by | Notes |
|---|---|---|---|
| `sweep_id` | VARCHAR | all | which run produced this row |
| `doc_id` | VARCHAR | all | sha256 of `path`, first 16 hex. Stable across runs of the same root, so two sweeps can be compared row by row |
| `path` | VARCHAR | all | relative to the swept root, forward slashes |
| `ext` | VARCHAR | all | lowercased, with the dot |
| `format` | VARCHAR | all | `pdf`, `docx`, `pptx`, `xlsx`, or null when no backend was dispatched |
| `size_bytes` | BIGINT | all | null if `stat` failed |
| `modified_utc` | VARCHAR | all | filesystem mtime, `YYYY-MM-DDTHH:MM:SSZ` |
| `content_sha256` | VARCHAR | all | null with `--no-hash`, or if the file could not be read. Also null for a file rejected before it was opened, such as an empty one |

### The attempt

| Column | Type | Seen by | Notes |
|---|---|---|---|
| `backend` | VARCHAR | all | library that read it, null when none was dispatched |
| `backend_version` | VARCHAR | all | for pdf, the pypdfium2 version and the pdfium build inside it |
| `ok` | BOOLEAN | all | did the backend finish |
| `error_kind` | VARCHAR | all | null if and only if `ok`. One of the kinds below |
| `error_detail` | VARCHAR | all | exception type and message, truncated to 500 characters. **May contain a file path** |
| `elapsed_ms` | DOUBLE | all | wall time for this file, hashing included |

Error kinds are a closed vocabulary, so a query written against the list stays correct:

| Kind | Means |
|---|---|
| `read_failed` | the filesystem refused `stat` or `open` |
| `empty_file` | zero bytes |
| `too_large` | above `--max-bytes`; not attempted |
| `format_not_supported` | a document format this build does not parse, or one whose library is not installed on this machine |
| `wrong_format` | the extension says one thing and the bytes say another |
| `password_required` | the backend will not open it without a password |
| `parse_failed` | the backend raised while reading the content |

### What the format states about itself

All null on a failed row: a file that did not open has an unknown page count, not zero
pages.

| Column | Type | Seen by | Notes |
|---|---|---|---|
| `format_version` | VARCHAR | pdf | e.g. `1.7`, from the file header |
| `page_count` | BIGINT | pdf | **Not docx.** Word pagination is decided when the document is laid out and is not stored in the file, so python-docx cannot see it. There is no substitute count standing in for it |
| `slide_count` | BIGINT | pptx | |
| `sheet_count` | BIGINT | xlsx | worksheets, in workbook order |
| `block_count` | BIGINT | all ok rows | `0` is a real answer: the backend opened the file and found no text. `null` means nobody knows. Collapsing the two makes every average wrong |
| `char_count` | BIGINT | all ok rows | sum of `block.char_count` for this document |
| `title` | VARCHAR | all | PDF `/Title`, OOXML `dc:title`. Whitespace-only is null: a title of `""` is not a title |
| `author` | VARCHAR | all | PDF `/Author`, OOXML `dc:creator` |
| `subject` | VARCHAR | all | |
| `keywords` | VARCHAR | all | one string as stored, not split |
| `creator` | VARCHAR | pdf | the application that produced the original. OOXML records this in `docProps/app.xml`, which these libraries do not expose, so it is null rather than filled with something else |
| `producer` | VARCHAR | pdf | the library that wrote the PDF |
| `created_declared` | VARCHAR | all | as the document states it, unparsed: a PDF date string (`D:20240131...`) for PDF, ISO 8601 for OOXML. It is a claim by the file, not a fact about it, which is why it is not a timestamp column |
| `modified_declared` | VARCHAR | all | same |

---

## `block`

One row per line, paragraph, or cell. Ordered by `block_index` within a document.

| Column | Type | Seen by | Notes |
|---|---|---|---|
| `sweep_id` | VARCHAR | all | |
| `doc_id` | VARCHAR | all | joins to `document` |
| `block_index` | BIGINT | all | 0-based, dense, stored order |
| `part_kind` | VARCHAR | pdf, pptx, xlsx | `page`, `slide`, `sheet`. **Null for docx**, which has no part a paragraph belongs to |
| `part_index` | BIGINT | pdf, pptx, xlsx | 0-based within the document |
| `part_label` | VARCHAR | xlsx | the sheet name. Pages and slides have no stated name |
| `block_kind` | VARCHAR | all | `line`, `paragraph`, `table_cell`, `cell`. See below |
| `text` | VARCHAR | all | as extracted, not normalised beyond dropping NUL |
| `char_count` | BIGINT | all | `len(text)` |
| `char_start` | BIGINT | pdf | offset into the page text |
| `char_end` | BIGINT | pdf | exclusive |
| `byte_start` | BIGINT | pdf | UTF-8 byte offset into the page text |
| `byte_end` | BIGINT | pdf | exclusive |
| `box_x0` `box_y0` `box_x1` `box_y1` | DOUBLE | pdf, pptx | points, origin bottom left of the page or slide |
| `box_of` | VARCHAR | pdf, pptx | what the box bounds: `text_chars` or `shape` |
| `style_name` | VARCHAR | docx | the paragraph style the file names, e.g. `Heading 1`. It is a name the document uses, not a verdict that the paragraph is a heading |
| `shape_name` | VARCHAR | pptx | the shape the text sits in |
| `cell_ref` | VARCHAR | xlsx | e.g. `B7` |

### Why a PDF block is a `line` and not a `paragraph`

pdfium delimits lines in the page text it returns. It does not state where a paragraph
begins. Grouping lines into paragraphs means guessing from spacing and indentation, and a
column called `paragraph` holding a guess is the defect rule 4 exists to stop: a stranger
querying `block_kind = 'paragraph'` would have no way to see that the value was inferred.

So the PDF backend emits what it saw. `docx` and `pptx` emit `paragraph` because
`<w:p>` and `<a:p>` are stated in the file.

This is a deliberate departure from the original specification, which described the block
table as one row per paragraph. Rule 4 outranks it.

### Why `box_of` exists

A PDF box is the union of the character boxes of that line: it bounds the text itself. A
PPTX box is the geometry of the shape the paragraph sits in, because python-pptx reads
stored shape geometry and does not lay out the text inside it. Both arrive in the same
four columns, so something in the row has to say which claim is being made.

`box_of` is null, and the four box columns with it, when the position is not stated: a
PPTX shape that inherits its position from a placeholder, or a PDF page where the
character index and the returned text disagree on length, which would make every offset
wrong by an unknown amount.

Docx and xlsx blocks have no box at all. A Word paragraph and a spreadsheet cell get a
position when the application lays the file out for printing, and that is not in the file.

---

## `sweep`

One row per run: what was attempted, what it cost, and which versions did it.

| Column | Type | Notes |
|---|---|---|
| `sweep_id` | VARCHAR | also the output directory name |
| `label` | VARCHAR | free text from `--label`. Used to pair a first run against a repeat |
| `root` | VARCHAR | absolute path that was swept |
| `started_utc` `finished_utc` | VARCHAR | |
| `walk_s` | DOUBLE | finding the files: one pass over the directory tree |
| `elapsed_s` | DOUBLE | reading them. `walk_s + elapsed_s` is the run |
| `jobs` | BIGINT | worker processes |
| `hashed` | BOOLEAN | whether `content_sha256` was computed, which costs a full read of every file |
| `extensions` | VARCHAR | the set actually attempted |
| `files_seen` | BIGINT | every file the walk passed, documents or not |
| `files_attempted` | BIGINT | rows written to `document` |
| `files_ok` `files_failed` | BIGINT | sum to `files_attempted` |
| `blocks_written` | BIGINT | rows written to `block` |
| `source_bytes` | BIGINT | sum of `size_bytes` over attempted files |
| `document_bytes` `block_bytes` | BIGINT | this run's two files on disk. Their sum over `source_bytes` is the store ratio |
| `tool_version` `python_version` `platform` | VARCHAR | |
| `backends` | JSON | `{format: "library version"}` for the libraries present on that machine |

`sweep.jsonl` is written last and is not counted in `document_bytes` or `block_bytes`,
which are measured from the closed files rather than estimated.

---

## What is not here, and will not be

Not an oversight list. These are things the backends cannot see, so a column carrying
them would be a name standing on nothing.

- **`tagged`, `reading_order`, any accessibility column.** pdfium exposes
  `is_tagged()`, so the column is one line of code away. It would mean "an object of a
  certain name exists in the file" and would read in SQL as "a screen reader can read
  this". pdfium cannot walk the structure tree, cannot check the tags are right, and
  cannot state a reading order. The boolean is available and the column is still banned.
- **`encrypted`.** A file that demands a password refuses to open, so that case is known
  and lives in `error_kind = 'password_required'`. A file encrypted with an owner
  password only opens normally and is indistinguishable from an unencrypted one. A column
  that is right for refusals and wrong for everything else is worse than no column.
- **Conformance of any kind** (PDF/A and its relatives), signatures, permissions.
- **Anything derived from rendering.** No OCR, no layout analysis, no table detection, no
  heading hierarchy, no language detection.
- **Headers, footers, footnotes, comments, and text boxes in docx**; **speaker notes,
  masters, layouts, chart and SmartArt text in pptx**. These are absent because they are
  not read yet, not because they cannot be. Adding them means reading more parts of the
  package, not inferring anything, so they are a candidate for a later cycle. Until then
  the omission is here in writing rather than hidden in a count.
- **Formula results that Excel never cached.** The workbook is opened with
  `data_only=True`, which returns the value Excel stored the last time it calculated. A
  formula cell with no cached value produces no block. This build does not evaluate
  formulas and does not report a computed number as a stored one.
