import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import api, db


class TrajectoryApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "trajectory-api.db"
        self.get_conn_patcher = patch.object(api, "get_conn", lambda: db.init_db(str(self.database_path)))
        self.get_conn_patcher.start()
        self.client = TestClient(api.app)

    def tearDown(self):
        self.get_conn_patcher.stop()
        self.temp_dir.cleanup()

    def test_task_api_lifecycle_and_event_deletion(self):
        created = self.client.post("/api/agent-tasks", json={})
        self.assertEqual(created.status_code, 201, created.text)
        task_id = created.json()["id"]
        self.assertEqual(created.json()["status"], "active")

        listed = self.client.get("/api/agent-tasks")
        self.assertEqual(listed.json()[0]["id"], task_id)

        completed = self.client.post(f"/api/agent-tasks/{task_id}/complete")
        self.assertEqual(completed.status_code, 200, completed.text)
        self.assertEqual(completed.json()["status"], "completed")

        deleted = self.client.delete(f"/api/agent-tasks/{task_id}")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(self.client.get(f"/api/agent-tasks/{task_id}").status_code, 404)

    def test_task_rejects_unknown_ontology_version(self):
        response = self.client.post("/api/agent-tasks", json={"ontology_version_id": "missing"})
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()