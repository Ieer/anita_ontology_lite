import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import api, db, graph


class EvidenceApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "evidence-api.db"
        self.get_conn_patcher = patch.object(api, "get_conn", lambda: db.init_db(str(self.database_path)))
        self.get_conn_patcher.start()
        self.client = TestClient(api.app)
        conn = db.init_db(str(self.database_path))
        subject = graph.add_entity(conn, "Subject", "Thing")
        object_id = graph.add_entity(conn, "Object", "Thing")
        self.fact_id = graph.add_fact(conn, subject, "related_to", object_id, "2020-01-01", source="catalog")
        conn.close()

    def tearDown(self):
        self.get_conn_patcher.stop()
        self.temp_dir.cleanup()

    def test_add_and_list_evidence_references_with_current_state(self):
        response = self.client.post(
            "/api/evidence",
            json={
                "semantic_object_id": "runtime_type:Thing",
                "source_type": "fact",
                "source_id": str(self.fact_id),
                "locator": "source record",
                "label": "Relationship example",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        reference = response.json()
        self.assertEqual(reference["state"], "current")
        self.assertNotIn("content", reference)

        listing = self.client.get("/api/evidence/runtime_type:Thing")
        self.assertEqual(listing.status_code, 200, listing.text)
        self.assertEqual(listing.json()[0]["id"], reference["id"])

        conn = db.init_db(str(self.database_path))
        conn.execute("UPDATE facts SET source='revised source' WHERE id=?", (self.fact_id,))
        conn.commit()
        conn.close()
        stale = self.client.get("/api/evidence/runtime_type:Thing")
        self.assertEqual(stale.json()[0]["state"], "stale")

    def test_invalid_evidence_source_returns_bad_request(self):
        response = self.client.post(
            "/api/evidence",
            json={
                "semantic_object_id": "runtime_type:Thing",
                "source_type": "external_path",
                "source_id": "/etc/passwd",
            },
        )
        self.assertEqual(response.status_code, 400)