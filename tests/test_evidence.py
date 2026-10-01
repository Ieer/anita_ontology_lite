import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import db, evidence, graph, local_markdown


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.conn = db.init_db(str(self.root / "evidence.db"))

    def tearDown(self):
        self.conn.close()
        self.temp_dir.cleanup()

    def test_fact_reference_tracks_current_stale_and_missing_without_copying_source(self):
        subject = graph.add_entity(self.conn, "Subject", "Thing")
        object_id = graph.add_entity(self.conn, "Object", "Thing")
        fact_id = graph.add_fact(self.conn, subject, "related_to", object_id, "2020-01-01", source="catalog")
        reference = evidence.add_reference(
            self.conn,
            "runtime_type:Thing",
            "fact",
            str(fact_id),
            locator="fact source record",
        )
        self.assertEqual(reference["state"], "current")
        self.assertNotIn("content", reference)

        self.conn.execute("UPDATE facts SET source='updated catalog' WHERE id=?", (fact_id,))
        self.conn.commit()
        self.assertEqual(evidence.get_reference(self.conn, reference["id"])["state"], "stale")

        self.conn.execute("DELETE FROM facts WHERE id=?", (fact_id,))
        self.conn.commit()
        self.assertEqual(evidence.get_reference(self.conn, reference["id"])["state"], "missing")

    def test_markdown_reference_tracks_content_hash_and_is_append_only(self):
        markdown_root = self.root / "markdown"
        with patch("app.local_data.markdown_root", return_value=markdown_root):
            source = local_markdown.save_file(self.conn, "Evidence note", "Initial evidence")
            reference = evidence.add_reference(
                self.conn,
                "ontology:commerce:term:invoice",
                "markdown",
                source["file_id"],
                locator="heading:definition",
                label="Invoice definition",
            )
            self.assertEqual(reference["state"], "current")
            self.assertNotIn("body", reference)

            local_markdown.save_file(self.conn, "Evidence note", "Updated evidence", file_id=source["file_id"])
            listed = evidence.list_references(self.conn, "ontology:commerce:term:invoice")

        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["state"], "stale")
        self.assertEqual(listed[0]["locator"], "heading:definition")

    def test_reference_rejects_unsupported_or_missing_sources(self):
        with self.assertRaises(ValueError):
            evidence.add_reference(self.conn, "term:invoice", "mounted_path", "anything")
        with self.assertRaises(ValueError):
            evidence.add_reference(self.conn, "term:invoice", "fact", "999")
        with self.assertRaises(ValueError):
            evidence.add_reference(self.conn, "term:invoice", "document", "not-an-id")