import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import db, evidence, ontology_documents, trajectory
import mcp_server


class McpSemanticToolTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "mcp.db"
        self.db_patcher = patch.object(db, "DB_PATH", str(self.database_path))
        self.db_patcher.start()
        conn = db.init_db()
        document = ontology_documents.create_document(
            conn,
            {
                "name": "Sales",
                "entityTypes": [{"id": "account", "name": "Account", "properties": []}],
                "relationships": [],
            },
        )
        self.semantic_id = f"ontology:{document['id']}:term:account"
        conn.close()

    def tearDown(self):
        self.db_patcher.stop()
        self.temp_dir.cleanup()

    def test_mcp_browse_and_resolve_include_bounded_semantics_and_evidence(self):
        conn = db.init_db()
        try:
            task = trajectory.create_task(conn)
            result = evidence.add_reference(
                conn,
                self.semantic_id,
                "document",
                str(conn.execute("INSERT INTO documents(title, content) VALUES ('Account policy', 'Accounts are reviewed monthly')").lastrowid),
                locator="paragraph:1",
                label="Review cadence",
            )
        finally:
            conn.close()

        browsed = mcp_server.browse_semantics("Account", kind="term", limit=6, task_id=task["id"])
        self.assertEqual(browsed["results"][0]["id"], self.semantic_id)

        resolved = mcp_server.resolve_semantics(["Account"], task_id=task["id"])
        item = resolved["results"][0]
        self.assertEqual(item["status"], "resolved")
        self.assertEqual(item["evidence"][0]["id"], result["id"])
        self.assertEqual(item["evidence"][0]["state"], "current")
        self.assertNotIn("content", item["evidence"][0])

        conn = db.init_db()
        try:
            stored_task = trajectory.get_task(conn, task["id"])
        finally:
            conn.close()
        self.assertEqual(len(stored_task["events"]), 2)
        self.assertEqual(stored_task["events"][1]["evidence_ref_ids"], [result["id"]])
        self.assertNotIn("Account", str(stored_task["events"]))


if __name__ == "__main__":
    unittest.main()