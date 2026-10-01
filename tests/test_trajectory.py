import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import db, ontology_documents, ontology_versions, trajectory


class TrajectoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.conn = db.init_db(str(Path(self.temp_dir.name) / "trajectory.db"))

    def tearDown(self):
        self.conn.close()
        self.temp_dir.cleanup()

    def test_task_is_opt_in_and_records_only_minimized_tool_metadata(self):
        task = trajectory.create_task(self.conn)
        with patch.dict(os.environ, {"UTOPIA_TRAJECTORY_HMAC_KEY": "test-only-secret"}):
            event = trajectory.record_event(
                self.conn,
                task["id"],
                "resolve_semantics",
                "success",
                query="customer revenue",
                context="monthly report",
                result_count=1,
                semantic_object_ids=["ontology:doc:term:revenue"],
                evidence_ref_ids=["evidence-1"],
            )
        self.assertEqual(len(event["query_fingerprint"]), 64)
        self.assertNotIn("customer revenue", str(event))
        self.assertNotIn("monthly report", str(event))
        self.assertEqual(event["semantic_object_ids"], ["ontology:doc:term:revenue"])
        self.assertEqual(trajectory.get_task(self.conn, task["id"])["event_count"], 1)

    def test_completed_task_cannot_accept_events_and_delete_cascades(self):
        task = trajectory.create_task(self.conn)
        trajectory.record_event(self.conn, task["id"], "browse_semantics", "not_found")
        completed = trajectory.complete_task(self.conn, task["id"])
        self.assertEqual(completed["status"], "completed")
        with self.assertRaisesRegex(ValueError, "already completed"):
            trajectory.record_event(self.conn, task["id"], "browse_semantics", "success")
        self.assertTrue(trajectory.delete_task(self.conn, task["id"]))
        self.assertIsNone(trajectory.get_task(self.conn, task["id"]))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM agent_task_events").fetchone()[0], 0)

    def test_task_can_pin_an_accepted_ontology_version(self):
        document = ontology_documents.create_document(
            self.conn,
            {"name": "Test", "entityTypes": [{"id": "account", "name": "Account", "properties": []}], "relationships": []},
        )
        candidate = ontology_versions.create_candidate(self.conn, document["id"])
        ontology_versions.evaluate_candidate(self.conn, candidate["id"])
        ontology_versions.decide_candidate(self.conn, candidate["id"], "accept")
        task = trajectory.create_task(self.conn, candidate["id"])
        self.assertEqual(task["ontology_version_id"], candidate["id"])
        with self.assertRaisesRegex(ValueError, "accepted ontology version"):
            trajectory.create_task(self.conn, "missing-version")

    def test_existing_event_table_migrates_sequence_column(self):
        legacy_path = Path(self.temp_dir.name) / "legacy-trajectory.db"
        legacy = sqlite3.connect(legacy_path)
        legacy.executescript(
            "CREATE TABLE agent_tasks ("
            "id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'active', "
            "ontology_version_id TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')), completed_at TEXT);"
            "CREATE TABLE agent_task_events ("
            "id TEXT PRIMARY KEY, task_id TEXT NOT NULL, tool_name TEXT NOT NULL, outcome TEXT NOT NULL, "
            "query_fingerprint TEXT NOT NULL DEFAULT '', result_count INTEGER NOT NULL DEFAULT 0, "
            "semantic_object_ids_json TEXT NOT NULL DEFAULT '[]', evidence_ref_ids_json TEXT NOT NULL DEFAULT '[]', "
            "created_at TEXT NOT NULL DEFAULT (datetime('now')));"
        )
        legacy.close()

        migrated = db.init_db(str(legacy_path))
        columns = {row[1] for row in migrated.execute("PRAGMA table_info(agent_task_events)")}
        migrated.close()
        self.assertIn("sequence", columns)


if __name__ == "__main__":
    unittest.main()