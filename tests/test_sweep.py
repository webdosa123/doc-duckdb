"""Tests for the four rules, plus enough of the backends to trust the counts.

Run with: python -m unittest discover -s tests -t .
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fixtures  # noqa: E402

from docduckdb import schema, sweep  # noqa: E402


def read_jsonl(path: Path) -> list:
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


class SweptCorpus(unittest.TestCase):
    """One sweep of the fixture corpus, shared by every test below."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        base = Path(cls._tmp.name)
        cls.corpus = fixtures.build(base / "corpus")
        cls.row = sweep.run(cls.corpus, base / "out", label="test")
        cls.directory = Path(cls.row["directory"])
        cls.documents = read_jsonl(cls.directory / "document.jsonl")
        cls.blocks = read_jsonl(cls.directory / "block.jsonl")
        cls.by_path = {d["path"]: d for d in cls.documents}

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def blocks_of(self, path):
        doc_id = self.by_path[path]["doc_id"]
        return [b for b in self.blocks if b["doc_id"] == doc_id]


class TestRuleOne(SweptCorpus):
    """A document that failed to parse is a row carrying an error kind."""

    def test_every_document_file_has_a_row(self):
        expected = {
            "two_pages.pdf", "nested/one_page.pdf", "sample.docx", "deck.pptx",
            "book.xlsx", "broken.pdf", "broken.docx", "broken.xlsx", "broken.pptx",
            "empty.pdf", "legacy.doc",
        }
        self.assertEqual(expected, set(self.by_path))

    def test_non_document_files_are_not_rows(self):
        self.assertNotIn("notes.txt", self.by_path)
        self.assertGreater(self.row["files_seen"], self.row["files_attempted"])

    def test_failures_carry_a_kind_from_the_vocabulary(self):
        failed = [d for d in self.documents if not d["ok"]]
        self.assertEqual(6, len(failed))
        for document in failed:
            self.assertIn(document["error_kind"], schema.ERROR_KINDS)

    def test_the_kinds_are_the_right_ones(self):
        for name in ("broken.pdf", "broken.docx", "broken.xlsx", "broken.pptx"):
            self.assertEqual("wrong_format", self.by_path[name]["error_kind"], name)
        self.assertEqual("empty_file", self.by_path["empty.pdf"]["error_kind"])
        self.assertEqual(
            "format_not_supported", self.by_path["legacy.doc"]["error_kind"]
        )

    def test_error_kind_is_null_exactly_when_ok(self):
        for document in self.documents:
            if document["ok"]:
                self.assertIsNone(document["error_kind"], document["path"])
            else:
                self.assertIsNotNone(document["error_kind"], document["path"])

    def test_counts_add_up(self):
        self.assertEqual(len(self.documents), self.row["files_attempted"])
        self.assertEqual(
            self.row["files_attempted"], self.row["files_ok"] + self.row["files_failed"]
        )
        self.assertEqual(len(self.blocks), self.row["blocks_written"])


class TestRuleTwo(SweptCorpus):
    """Every column a failed row cannot know is null, never 0 or false."""

    UNKNOWABLE = (
        "page_count", "slide_count", "sheet_count", "block_count", "char_count",
        "format_version", "title", "author", "subject", "keywords",
        "creator", "producer", "created_declared", "modified_declared",
    )

    def test_failed_rows_know_nothing_about_content(self):
        for document in self.documents:
            if document["ok"]:
                continue
            for column in self.UNKNOWABLE:
                self.assertIsNone(
                    document[column],
                    "{} reported {} on a failed row".format(document["path"], column),
                )

    def test_zero_blocks_and_unknown_blocks_are_different(self):
        empty_pdf = self.corpus / "no_text.pdf"
        fixtures.make_pdf(empty_pdf, [[]])
        row, blocks = sweep._attempt(str(empty_pdf), "no_text.pdf", sweep.Options())
        self.assertTrue(row["ok"])
        self.assertEqual(0, row["block_count"])   # opened it, found no text
        self.assertEqual([], blocks)
        self.assertIsNone(self.by_path["broken.pdf"]["block_count"])  # nobody knows
        empty_pdf.unlink()

    def test_docx_has_no_page_count_rather_than_zero(self):
        document = self.by_path["sample.docx"]
        self.assertTrue(document["ok"])
        self.assertIsNone(document["page_count"])
        self.assertIsNone(document["slide_count"])
        self.assertIsNone(document["sheet_count"])

    def test_a_format_count_is_null_outside_its_own_format(self):
        self.assertIsNone(self.by_path["two_pages.pdf"]["sheet_count"])
        self.assertIsNone(self.by_path["book.xlsx"]["page_count"])
        self.assertIsNone(self.by_path["deck.pptx"]["page_count"])


class TestRuleThree(SweptCorpus):
    """The reader types its columns from every row."""

    def _duckdb(self):
        try:
            import duckdb
        except ImportError:
            self.skipTest("duckdb not installed")
        connection = duckdb.connect(":memory:")
        glob = str(self.directory.parent).replace("\\", "/") + "/*"
        connection.execute(schema.views_sql(glob))
        return connection

    def test_refusal_rows_survive_the_reader(self):
        connection = self._duckdb()
        rows = connection.sql(
            "select error_kind, count(*) from document where not ok group by 1"
        ).fetchall()
        self.assertEqual(6, sum(count for _, count in rows))
        kinds = {kind for kind, _ in rows}
        self.assertEqual(
            {"wrong_format", "empty_file", "format_not_supported"}, kinds
        )

    def test_declared_types_survive_an_all_null_column(self):
        # subject is null in every fixture row. Inference would collapse it; the
        # declared column map keeps it a VARCHAR that a query can still reference.
        connection = self._duckdb()
        described = connection.sql("describe document").fetchall()
        types = {name: dtype for name, dtype, *_ in described}
        self.assertEqual("VARCHAR", types["subject"])
        self.assertEqual("BIGINT", types["page_count"])
        self.assertEqual("BOOLEAN", types["ok"])

    def test_sample_size_minus_one_is_what_hand_queries_need(self):
        connection = self._duckdb()
        path = str(self.directory / "document.jsonl").replace("\\", "/")
        full = connection.sql(
            "select count(*) from read_json_auto('{}', sample_size=-1)".format(path)
        ).fetchone()[0]
        self.assertEqual(len(self.documents), full)


class TestViewScoping(SweptCorpus):
    """Two sweeps of one folder must not double a join.

    doc_id is a hash of the path, so it is the same identity in every sweep of the same
    root. `document join block using (doc_id)` over two sweeps would return each block
    twice, with the right column names and a quietly doubled count. The plain views are
    scoped to the latest sweep so the obvious join is the correct one.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.second = sweep.run(cls.corpus, cls.directory.parent, label="second")

    def _duckdb(self):
        try:
            import duckdb
        except ImportError:
            self.skipTest("duckdb not installed")
        connection = duckdb.connect(":memory:")
        glob = str(self.directory.parent).replace("\\", "/") + "/*"
        connection.execute(schema.views_sql(glob))
        return connection

    def test_both_sweeps_are_on_disk(self):
        self.assertEqual(2, len(list(self.directory.parent.glob("*/document.jsonl"))))

    def test_the_plain_views_are_one_sweep(self):
        connection = self._duckdb()
        latest = connection.sql("select sweep_id from latest_sweep").fetchone()[0]
        self.assertEqual(self.second["sweep_id"], latest)
        self.assertEqual(
            len(self.documents),
            connection.sql("select count(*) from document").fetchone()[0],
        )

    def test_the_obvious_join_does_not_fan_out(self):
        connection = self._duckdb()
        joined = connection.sql(
            "select count(*) from document join block using (doc_id)"
        ).fetchone()[0]
        self.assertEqual(len(self.blocks), joined)

    def test_all_views_carry_every_sweep(self):
        connection = self._duckdb()
        self.assertEqual(
            2 * len(self.documents),
            connection.sql("select count(*) from document_all").fetchone()[0],
        )


class TestDeclaredTypes(unittest.TestCase):
    """The reader side of rule 3, asserted where this repo controls it.

    The generated views declare every column instead of letting the reader infer it.
    That is stronger than sample_size=-1 and, unlike it, does not depend on how DuckDB
    buffers a file it is sampling.

    There is no test here asserting that read_json_auto's default *mis*types a long
    file. It does -- see the note in the README, reproduced on a 379 MiB block table
    where twelve columns came back JSON and `where box_y0 > 700` quietly returned a
    different count. But the trigger is DuckDB's internal buffering, not the row count,
    and a test that asserts another project's sampling stays wrong is a flaky test
    pointed at someone else's implementation detail.
    """

    def test_every_column_is_declared_with_its_type(self):
        for table, columns in schema.TABLES.items():
            clause = schema.columns_clause(table)
            for name, dtype in columns.items():
                self.assertIn("'{}': '{}'".format(name, dtype), clause)

    def test_the_numeric_block_columns_are_not_left_to_inference(self):
        clause = schema.columns_clause("block")
        for name in ("box_x0", "box_y0", "box_x1", "box_y1"):
            self.assertIn("'{}': 'DOUBLE'".format(name), clause)
        for name in ("char_start", "char_end", "byte_start", "byte_end", "part_index"):
            self.assertIn("'{}': 'BIGINT'".format(name), clause)

    def test_the_views_never_call_the_inferring_reader(self):
        statements = " ".join(
            line for line in schema.views_sql().splitlines()
            if not line.lstrip().startswith("--")
        )
        self.assertIn("read_json(", statements)
        self.assertNotIn("read_json_auto", statements)


class TestRuleFour(SweptCorpus):
    """No column whose name claims more than the backend can see."""

    BANNED = ("tagged", "reading_order", "accessible", "conformance", "pdfa",
              "encrypted", "signed", "language_detected")

    def test_the_schema_carries_no_column_it_cannot_stand_behind(self):
        for table, columns in schema.TABLES.items():
            for name in columns:
                self.assertNotIn(
                    name, self.BANNED, "{}.{} is a banned column".format(table, name)
                )

    def test_a_pdf_block_is_a_line_not_a_claimed_paragraph(self):
        kinds = {b["block_kind"] for b in self.blocks_of("two_pages.pdf")}
        self.assertEqual({"line"}, kinds)

    def test_box_of_says_what_the_box_bounds(self):
        pdf_blocks = self.blocks_of("two_pages.pdf")
        self.assertTrue(all(b["box_of"] == "text_chars" for b in pdf_blocks))
        pptx_blocks = self.blocks_of("deck.pptx")
        self.assertTrue(all(b["box_of"] == "shape" for b in pptx_blocks))

    def test_a_backend_without_boxes_reports_none(self):
        for path in ("sample.docx", "book.xlsx"):
            for block in self.blocks_of(path):
                self.assertIsNone(block["box_of"], path)
                self.assertIsNone(block["box_x0"], path)


class TestSchemaContract(SweptCorpus):
    def test_every_row_carries_every_key(self):
        for table, rows in (("document", self.documents), ("block", self.blocks)):
            for row in rows:
                schema.check_row(table, row)

    def test_check_row_rejects_a_missing_key(self):
        row = schema.empty_row("document")
        del row["ok"]
        with self.assertRaises(ValueError):
            schema.check_row("document", row)

    def test_check_row_rejects_an_unknown_key(self):
        row = schema.empty_row("document")
        row["tagged"] = True
        with self.assertRaises(ValueError):
            schema.check_row("document", row)

    def test_block_index_is_dense_and_ordered(self):
        for path in self.by_path:
            indexes = [b["block_index"] for b in self.blocks_of(path)]
            self.assertEqual(list(range(len(indexes))), indexes, path)


class TestPdfBackend(SweptCorpus):
    def test_pages_and_lines(self):
        document = self.by_path["two_pages.pdf"]
        self.assertEqual(2, document["page_count"])
        self.assertEqual("1.7", document["format_version"])
        blocks = self.blocks_of("two_pages.pdf")
        self.assertEqual(["Page one line one", "Page one line two", "Page two line one"],
                         [b["text"] for b in blocks])
        self.assertEqual([0, 0, 1], [b["part_index"] for b in blocks])
        self.assertEqual({"page"}, {b["part_kind"] for b in blocks})

    def test_metadata_is_what_the_file_states(self):
        document = self.by_path["two_pages.pdf"]
        self.assertEqual("Fixture", document["title"])
        self.assertEqual("doc-duckdb fixture", document["producer"])
        self.assertIsNone(document["author"])   # the fixture states none

    def test_byte_range_cuts_the_text_back_out(self):
        # The offsets are into the page text, so a consumer can take the same slice
        # without re-running the extraction.
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(self.corpus / "two_pages.pdf"))
        page_text = pdf[0].get_textpage().get_text_range()
        raw = page_text.encode("utf-8")
        for block in self.blocks_of("two_pages.pdf"):
            if block["part_index"] != 0:
                continue
            self.assertEqual(
                block["text"], raw[block["byte_start"]:block["byte_end"]].decode("utf-8")
            )
            self.assertEqual(
                block["text"], page_text[block["char_start"]:block["char_end"]]
            )
        pdf.close()

    def test_boxes_are_on_the_page_and_ordered_down_it(self):
        blocks = [b for b in self.blocks_of("two_pages.pdf") if b["part_index"] == 0]
        for block in blocks:
            self.assertLess(block["box_x0"], block["box_x1"])
            self.assertLess(block["box_y0"], block["box_y1"])
            self.assertGreaterEqual(block["box_x0"], 0)
            self.assertLessEqual(block["box_y1"], 792)
        self.assertGreater(blocks[0]["box_y0"], blocks[1]["box_y0"])  # line two is lower


class TestOoxmlBackends(SweptCorpus):
    def test_docx_reads_the_body_in_stored_order(self):
        texts = [b["text"] for b in self.blocks_of("sample.docx")]
        self.assertEqual(
            ["Heading one", "First body paragraph.", "cell a", "cell b", "cell c",
             "cell d", "After the table."],
            texts,
        )

    def test_docx_marks_table_cells_and_keeps_style_names(self):
        blocks = self.blocks_of("sample.docx")
        self.assertEqual("table_cell", blocks[2]["block_kind"])
        self.assertEqual("paragraph", blocks[0]["block_kind"])
        self.assertIsNotNone(blocks[0]["style_name"])

    def test_docx_core_properties(self):
        document = self.by_path["sample.docx"]
        self.assertEqual("Docx fixture", document["title"])
        self.assertEqual("Fixture author", document["author"])
        self.assertIsNone(document["producer"])  # app.xml is not read by python-docx

    def test_pptx_slides_and_boxes(self):
        document = self.by_path["deck.pptx"]
        self.assertEqual(2, document["slide_count"])
        blocks = self.blocks_of("deck.pptx")
        self.assertEqual(
            ["Slide one title", "Slide one body", "Slide two only"],
            [b["text"] for b in blocks],
        )
        self.assertEqual([0, 0, 1], [b["part_index"] for b in blocks])
        self.assertEqual(72.0, blocks[0]["box_x0"])  # one inch from the left

    def test_xlsx_cells_carry_their_reference_and_sheet(self):
        document = self.by_path["book.xlsx"]
        self.assertEqual(2, document["sheet_count"])
        blocks = self.blocks_of("book.xlsx")
        by_ref = {(b["part_label"], b["cell_ref"]): b["text"] for b in blocks}
        self.assertEqual("label", by_ref[("Numbers", "A1")])
        self.assertEqual("12", by_ref[("Numbers", "B2")])
        self.assertEqual("a note", by_ref[("Notes", "A1")])
        self.assertNotIn(("Numbers", "B3"), by_ref)  # an empty cell is not a block


class TestParallel(unittest.TestCase):
    """Worker processes must produce the same rows as one process.

    A sweep that quietly differed by --jobs would make every measurement conditional on
    how it was run.
    """

    def test_jobs_two_matches_jobs_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            corpus = fixtures.build(base / "corpus")
            single = sweep.run(corpus, base / "one", jobs=1, label="one")
            double = sweep.run(corpus, base / "two", jobs=2, label="two")

            self.assertEqual(single["files_ok"], double["files_ok"])
            self.assertEqual(single["files_failed"], double["files_failed"])
            self.assertEqual(single["blocks_written"], double["blocks_written"])

            def rows(row):
                out = []
                for document in read_jsonl(Path(row["directory"]) / "document.jsonl"):
                    document.pop("sweep_id")
                    document.pop("elapsed_ms")
                    out.append(document)
                return sorted(out, key=lambda d: d["path"])

            self.assertEqual(rows(single), rows(double))


class TestSweepRow(SweptCorpus):
    def test_it_records_what_was_attempted(self):
        self.assertEqual("test", self.row["label"])
        self.assertEqual(1, self.row["jobs"])
        self.assertTrue(self.row["hashed"])
        self.assertGreater(self.row["elapsed_s"], 0)
        self.assertGreater(self.row["source_bytes"], 0)
        self.assertGreater(self.row["document_bytes"], 0)
        self.assertIn("pdf", self.row["backends"])

    def test_backends_is_a_json_object_a_query_can_reach_into(self):
        try:
            import duckdb
        except ImportError:
            self.skipTest("duckdb not installed")
        connection = duckdb.connect(":memory:")
        glob = str(self.directory.parent).replace("\\", "/") + "/*"
        connection.execute(schema.views_sql(glob))
        kind, backend = connection.sql(
            "select json_type(backends), backends->>'pdf' from sweep limit 1"
        ).fetchone()
        self.assertEqual("OBJECT", kind)
        self.assertIn("pypdfium2", backend)

    def test_the_store_size_is_the_file_on_disk(self):
        self.assertEqual(
            (self.directory / "document.jsonl").stat().st_size,
            self.row["document_bytes"],
        )
        self.assertEqual(
            (self.directory / "block.jsonl").stat().st_size, self.row["block_bytes"]
        )

    def test_content_hash_identifies_a_duplicate(self):
        hashes = [d["content_sha256"] for d in self.documents if d["ok"]]
        self.assertTrue(all(h and len(h) == 64 for h in hashes))

    def test_a_failed_read_still_gets_a_hash_attempt_not_a_false_value(self):
        row = self.by_path["broken.pdf"]
        self.assertIsNotNone(row["content_sha256"])  # readable, just not a PDF
        self.assertFalse(row["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
