import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import db, ontology_documents, ontology_versions, semantic_tools


class OntologyVersionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.conn = db.init_db(str(Path(self.temp_dir.name) / "versions.db"))
        created = ontology_documents.create_document(self.conn, self.document("Account"))
        self.document_id = created["id"]

    def tearDown(self):
        self.conn.close()
        self.temp_dir.cleanup()

    def test_candidate_snapshot_is_immutable_and_accept_publishes_active_version(self):
        candidate = ontology_versions.create_candidate(self.conn, self.document_id)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "UPDATE ontology_versions SET snapshot_json='{}' WHERE id=?",
                (candidate["id"],),
            )
        report = ontology_versions.evaluate_candidate(self.conn, candidate["id"])
        self.assertTrue(report["passed"])
        self.assertTrue(report["requires_human_review"])

        accepted = ontology_versions.decide_candidate(self.conn, candidate["id"], "accept", "Reviewed")
        self.assertEqual(accepted["status"], "accepted")
        active = ontology_versions.get_active_version(self.conn, self.document_id)
        self.assertEqual(active["id"], candidate["id"])

        ontology_documents.update_document(self.conn, self.document_id, self.document("AccountDraft"))
        published = semantic_tools.browse_semantics(self.conn, "Account", kind="term")
        self.assertEqual(published["results"][0]["source"]["type"], "published_ontology_version")
        self.assertEqual(semantic_tools.browse_semantics(self.conn, "AccountDraft", kind="term")["results"], [])

    def test_invalid_candidate_cannot_be_accepted_and_rejection_requires_reason(self):
        invalid_document = {
            "name": "Broken",
            "entityTypes": [{"id": "account", "name": "Account", "properties": []}],
            "relationships": [{"id": "invalid", "name": "owns", "from": "missing", "to": "account", "cardinality": "one-to-one"}],
        }
        draft = ontology_documents.update_document(self.conn, self.document_id, invalid_document)
        candidate = ontology_versions.create_candidate(self.conn, draft["id"])
        report = ontology_versions.evaluate_candidate(self.conn, candidate["id"])
        self.assertFalse(report["passed"])
        with self.assertRaises(ValueError):
            ontology_versions.decide_candidate(self.conn, candidate["id"], "accept")
        with self.assertRaises(ValueError):
            ontology_versions.decide_candidate(self.conn, candidate["id"], "reject")
        rejected = ontology_versions.decide_candidate(self.conn, candidate["id"], "reject", "Dangling relation")
        self.assertEqual(rejected["status"], "rejected")
        self.assertIsNone(ontology_versions.get_active_version(self.conn, self.document_id))

    def test_candidate_with_stale_parent_cannot_publish(self):
        first = ontology_versions.create_candidate(self.conn, self.document_id)
        ontology_versions.evaluate_candidate(self.conn, first["id"])
        ontology_versions.decide_candidate(self.conn, first["id"], "accept")

        left = ontology_versions.create_candidate(self.conn, self.document_id, first["id"])
        right = ontology_versions.create_candidate(self.conn, self.document_id, first["id"])
        ontology_versions.evaluate_candidate(self.conn, left["id"])
        ontology_versions.evaluate_candidate(self.conn, right["id"])
        ontology_versions.decide_candidate(self.conn, left["id"], "accept")
        with self.assertRaisesRegex(ValueError, "Active parent changed"):
            ontology_versions.decide_candidate(self.conn, right["id"], "accept")

    def test_generated_candidate_requires_current_source_at_evaluation_and_acceptance(self):
        document_id = self.conn.execute(
            "INSERT INTO documents(title, content) VALUES (?, ?)", ("Glossary", "Account definition")
        ).lastrowid
        draft = self.document("Account")
        draft["sourceEvidence"] = [{
            "element_id": "account", "source_id": f"document:{document_id}", "locator": "Account definition",
        }]
        ontology_documents.update_document(self.conn, self.document_id, draft)
        stale = ontology_versions.create_candidate(self.conn, self.document_id)
        self.conn.execute("UPDATE documents SET content='Removed' WHERE id=?", (document_id,))
        report = ontology_versions.evaluate_candidate(self.conn, stale["id"])
        self.assertFalse(report["passed"])
        with self.assertRaisesRegex(ValueError, "structural validation"):
            ontology_versions.decide_candidate(self.conn, stale["id"], "accept")

        self.conn.execute("UPDATE documents SET content='Account definition' WHERE id=?", (document_id,))
        candidate = ontology_versions.create_candidate(self.conn, self.document_id)
        self.assertTrue(ontology_versions.evaluate_candidate(self.conn, candidate["id"])["passed"])
        self.conn.execute("UPDATE documents SET content='Removed again' WHERE id=?", (document_id,))
        with self.assertRaisesRegex(ValueError, "source"):
            ontology_versions.decide_candidate(self.conn, candidate["id"], "accept")
        self.assertIsNone(ontology_versions.get_active_version(self.conn, self.document_id))

    @staticmethod
    def document(term_name):
        return {
            "name": "Versioned ontology",
            "entityTypes": [{"id": "account", "name": term_name, "properties": []}],
            "relationships": [],
        }


if __name__ == "__main__":
    unittest.main()