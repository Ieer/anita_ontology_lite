import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import api, db
from ui.templates import COMMERCE_TEMPLATE


class OntologyVersionsApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "versions-api.db"
        self.get_conn_patcher = patch.object(api, "get_conn", lambda: db.init_db(str(self.database_path)))
        self.get_conn_patcher.start()
        self.client = TestClient(api.app)
        created = self.client.post("/api/ontology-documents", json={"document": COMMERCE_TEMPLATE})
        self.document_id = created.json()["id"]

    def tearDown(self):
        self.get_conn_patcher.stop()
        self.temp_dir.cleanup()

    def test_create_evaluate_accept_and_activate_candidate(self):
        created = self.client.post(f"/api/ontology-documents/{self.document_id}/versions", json={})
        self.assertEqual(created.status_code, 201, created.text)
        version_id = created.json()["id"]

        skipped = self.client.post(f"/api/ontology-versions/{version_id}/decision", json={"decision": "accept"})
        self.assertEqual(skipped.status_code, 400)

        evaluated = self.client.post(f"/api/ontology-versions/{version_id}/evaluate")
        self.assertEqual(evaluated.status_code, 200, evaluated.text)
        self.assertTrue(evaluated.json()["passed"])
        self.assertTrue(evaluated.json()["requires_human_review"])

        accepted = self.client.post(
            f"/api/ontology-versions/{version_id}/decision",
            json={"decision": "accept", "reason": "Reviewed schema"},
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(accepted.json()["status"], "accepted")
        listing = self.client.get(f"/api/ontology-documents/{self.document_id}/versions")
        self.assertEqual(listing.json()[0]["id"], version_id)

        draft_update = {**COMMERCE_TEMPLATE, "name": "Unpublished draft"}
        self.client.put(f"/api/ontology-documents/{self.document_id}", json={"document": draft_update})
        active = self.client.post(
            f"/api/ontology-documents/{self.document_id}/versions/{version_id}/activate"
        )
        self.assertEqual(active.status_code, 200, active.text)
        self.assertEqual(active.json()["snapshot"]["name"], COMMERCE_TEMPLATE["name"])

    def test_invalid_candidate_is_reported_and_cannot_be_accepted(self):
        invalid = {
            "name": "Invalid",
            "entityTypes": [{"id": "account", "name": "Account", "properties": []}],
            "relationships": [{"id": "bad", "name": "owns", "from": "missing", "to": "account", "cardinality": "one-to-one"}],
        }
        self.client.put(f"/api/ontology-documents/{self.document_id}", json={"document": invalid})
        created = self.client.post(f"/api/ontology-documents/{self.document_id}/versions", json={})
        version_id = created.json()["id"]
        evaluated = self.client.post(f"/api/ontology-versions/{version_id}/evaluate")
        self.assertFalse(evaluated.json()["passed"])
        rejected = self.client.post(
            f"/api/ontology-versions/{version_id}/decision",
            json={"decision": "accept"},
        )
        self.assertEqual(rejected.status_code, 400)

    def test_changed_property_is_reported_and_only_accepted_snapshot_exports(self):
        initial = self.client.post(f"/api/ontology-documents/{self.document_id}/versions", json={}).json()
        self.client.post(f"/api/ontology-versions/{initial['id']}/evaluate")
        self.client.post(f"/api/ontology-versions/{initial['id']}/decision", json={"decision": "accept"})
        modified = {**COMMERCE_TEMPLATE, "entityTypes": [dict(item) for item in COMMERCE_TEMPLATE["entityTypes"]]}
        modified["entityTypes"][0]["properties"] = [dict(item) for item in modified["entityTypes"][0]["properties"]]
        modified["entityTypes"][0]["properties"][1]["type"] = "integer"
        self.client.put(f"/api/ontology-documents/{self.document_id}", json={"document": modified})
        candidate = self.client.post(f"/api/ontology-documents/{self.document_id}/versions", json={}).json()
        report = self.client.post(f"/api/ontology-versions/{candidate['id']}/evaluate").json()
        self.assertIn("entityTypes:customer.properties", report["changed_semantics"])
        pending_export = self.client.get(f"/api/ontology-documents/{self.document_id}/versions/{candidate['id']}/rdf")
        self.assertEqual(pending_export.status_code, 404)
        self.client.post(f"/api/ontology-versions/{candidate['id']}/decision", json={"decision": "accept"})
        exported = self.client.get(f"/api/ontology-documents/{self.document_id}/versions/{candidate['id']}/rdf")
        self.assertEqual(exported.status_code, 200, exported.text)
        self.assertIn(candidate["id"], exported.headers["content-disposition"])
        self.client.put(f"/api/ontology-documents/{self.document_id}", json={"document": {**modified, "name": "Later draft"}})
        self.assertEqual(self.client.get(f"/api/ontology-documents/{self.document_id}/versions/{candidate['id']}/rdf").content, exported.content)


if __name__ == "__main__":
    unittest.main()