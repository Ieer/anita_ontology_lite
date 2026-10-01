import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from rdflib import BNode, Graph, Literal, Namespace, RDF, RDFS
from rdflib.namespace import OWL

from app import api, db, ontology_rdf
from ui.templates import COMMERCE_TEMPLATE


class OntologyDocumentApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "api.db"
        self.get_conn_patcher = patch.object(api, "get_conn", lambda: db.init_db(str(database_path)))
        self.get_conn_patcher.start()
        self.client = TestClient(api.app)

    def tearDown(self):
        self.get_conn_patcher.stop()
        self.temp_dir.cleanup()

    def test_document_crud_validation_and_rdf_round_trip(self):
        created = self.client.post(
            "/api/ontology-documents",
            json={"document": COMMERCE_TEMPLATE},
        )
        self.assertEqual(created.status_code, 201, created.text)
        document_id = created.json()["id"]
        self.assertEqual(created.json()["version"], 1)

        listing = self.client.get("/api/ontology-documents")
        self.assertEqual([row["id"] for row in listing.json()], [document_id])
        loaded = self.client.get(f"/api/ontology-documents/{document_id}")
        self.assertEqual(loaded.json()["document"], COMMERCE_TEMPLATE)

        updated_document = {**COMMERCE_TEMPLATE, "description": "Updated through API"}
        updated = self.client.put(
            f"/api/ontology-documents/{document_id}",
            json={"document": updated_document},
        )
        self.assertEqual(updated.json()["version"], 2)
        self.assertEqual(updated.json()["document"]["description"], "Updated through API")

        validation = self.client.post(
            "/api/ontology-documents/validate",
            json={"document": {"name": "Broken", "entityTypes": [], "relationships": [{"from": "missing"}]}},
        )
        self.assertFalse(validation.json()["valid"])
        self.assertTrue(validation.json()["errors"])

        exported = self.client.get(f"/api/ontology-documents/{document_id}/rdf")
        self.assertEqual(exported.status_code, 200)
        self.assertIn("application/rdf+xml", exported.headers["content-type"])
        imported = self.client.post(
            "/api/ontology-documents/import-rdf",
            json={"rdf_xml": exported.text},
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        self.assertEqual(imported.json()["document"], updated_document)
        self.assertEqual(imported.json()["warnings"], [])

        self.assertEqual(self.client.delete(f"/api/ontology-documents/{document_id}").status_code, 200)
        self.assertEqual(self.client.get(f"/api/ontology-documents/{document_id}").status_code, 404)

    def test_invalid_rdf_returns_client_error(self):
        response = self.client.post(
            "/api/ontology-documents/import-rdf",
            json={"rdf_xml": "<not-rdf"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid RDF/XML", response.json()["detail"])

    def test_imported_owl_is_exported_exactly_and_unknown_triples_survive_edits(self):
        custom = Namespace("https://example.org/custom#")
        source_graph = Graph().parse(
            data=ontology_rdf.serialize_document(COMMERCE_TEMPLATE),
            format="xml",
        )
        product_uri = next(
            subject
            for subject in source_graph.subjects(RDF.type, OWL.Class)
            if str(source_graph.value(subject, ontology_rdf.ANNOTATION.id)) == "product"
        )
        restriction = BNode("sourceRestriction")
        source_graph.add((product_uri, custom.license, Literal("retained")))
        source_graph.add((product_uri, RDFS.subClassOf, restriction))
        source_graph.add((restriction, RDF.type, OWL.Restriction))
        source_graph.add((restriction, OWL.someValuesFrom, OWL.Thing))
        original_xml = source_graph.serialize(format="pretty-xml")

        imported = self.client.post(
            "/api/ontology-documents/import-rdf",
            json={"rdf_xml": original_xml},
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        import_payload = imported.json()
        saved = self.client.post(
            "/api/ontology-documents",
            json={
                "document": import_payload["document"],
                "rdf_source_xml": import_payload["rdf_source_xml"],
                "rdf_source_document": import_payload["rdf_source_document"],
            },
        )
        document_id = saved.json()["id"]

        exported_unchanged = self.client.get(f"/api/ontology-documents/{document_id}/rdf")
        self.assertEqual(exported_unchanged.text, original_xml)

        edited_document = import_payload["document"]
        edited_document["name"] = "Edited Commerce"
        product = next(item for item in edited_document["entityTypes"] if item["id"] == "product")
        product["name"] = "Edited Product"
        updated = self.client.put(
            f"/api/ontology-documents/{document_id}",
            json={"document": edited_document},
        )
        self.assertEqual(updated.status_code, 200, updated.text)

        exported_edited = self.client.get(f"/api/ontology-documents/{document_id}/rdf")
        merged_graph = Graph().parse(data=exported_edited.text, format="xml")
        self.assertIn((product_uri, custom.license, Literal("retained")), merged_graph)
        self.assertIn((product_uri, RDFS.label, Literal("Edited Product")), merged_graph)
        merged_restriction = merged_graph.value(product_uri, RDFS.subClassOf)
        self.assertIsNotNone(merged_restriction)
        self.assertIn((merged_restriction, RDF.type, OWL.Restriction), merged_graph)
        self.assertIn((merged_restriction, OWL.someValuesFrom, OWL.Thing), merged_graph)


if __name__ == "__main__":
    unittest.main()
