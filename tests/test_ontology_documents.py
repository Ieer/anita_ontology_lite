import tempfile
import sqlite3
import unittest
from pathlib import Path

from rdflib import BNode, Graph, Literal, Namespace, RDF, RDFS, URIRef
from rdflib.namespace import OWL, XSD

from app import db, ontology_documents, ontology_rdf, ontology_sharing


VALID_DOCUMENT = {
    "name": "Commerce",
    "description": "Commerce schema",
    "entityTypes": [
        {
            "id": "product",
            "name": "Product",
            "description": "A sellable item",
            "icon": "box",
            "color": "#168577",
            "properties": [
                {"name": "sku", "type": "string", "isIdentifier": True},
                {"name": "price", "type": "decimal"},
            ],
        },
        {
            "id": "order",
            "name": "Order",
            "description": "A purchase",
            "icon": "receipt",
            "color": "#b77a24",
            "properties": [{"name": "orderId", "type": "string", "isIdentifier": True}],
        },
    ],
    "relationships": [
        {
            "id": "order_product",
            "name": "contains",
            "from": "order",
            "to": "product",
            "cardinality": "many-to-many",
            "attributes": [{"name": "quantity", "type": "integer"}],
        }
    ],
}


class OntologyDocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.conn = db.init_db(str(Path(self.temp_dir.name) / "ontology.db"))

    def tearDown(self):
        self.conn.close()
        self.temp_dir.cleanup()

    def test_validate_rejects_duplicate_types_and_dangling_relationships(self):
        document = {
            **VALID_DOCUMENT,
            "entityTypes": [*VALID_DOCUMENT["entityTypes"], VALID_DOCUMENT["entityTypes"][0]],
        }
        document["relationships"] = [
            {**VALID_DOCUMENT["relationships"][0], "to": "missing"}
        ]

        errors = ontology_documents.validate_document(document)

        self.assertTrue(any("Duplicate entity type ID" in error["message"] for error in errors))
        self.assertTrue(any("must reference an entity type" in error["message"] for error in errors))

    def test_document_crud_tracks_version_and_keeps_full_json(self):
        created = ontology_documents.create_document(self.conn, VALID_DOCUMENT)
        self.assertEqual(created["version"], 1)
        self.assertEqual(created["document"], VALID_DOCUMENT)

        updated_input = {**VALID_DOCUMENT, "description": "Updated schema"}
        updated = ontology_documents.update_document(self.conn, created["id"], updated_input)
        self.assertEqual(updated["version"], 2)
        self.assertEqual(updated["document"]["description"], "Updated schema")
        self.assertEqual(len(ontology_documents.list_documents(self.conn)), 1)

        self.assertTrue(ontology_documents.delete_document(self.conn, created["id"]))
        self.assertIsNone(ontology_documents.get_document(self.conn, created["id"]))

    def test_existing_document_table_migrates_rdf_source_columns_without_data_loss(self):
        legacy_path = Path(self.temp_dir.name) / "legacy.db"
        legacy = sqlite3.connect(legacy_path)
        legacy.execute(
            "CREATE TABLE ontology_documents ("
            "id TEXT PRIMARY KEY, name TEXT NOT NULL, document_json TEXT NOT NULL, "
            "version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        legacy.execute(
            "INSERT INTO ontology_documents(id, name, document_json, version, created_at, updated_at) "
            "VALUES ('legacy', 'Legacy', ?, 3, '2025-01-01', '2025-01-02')",
            ("{}",),
        )
        legacy.commit()
        legacy.close()

        migrated = db.init_db(str(legacy_path))
        try:
            columns = {row[1] for row in migrated.execute("PRAGMA table_info(ontology_documents)")}
            stored = ontology_documents.get_document(migrated, "legacy")
        finally:
            migrated.close()

        self.assertIn("rdf_source_xml", columns)
        self.assertIn("rdf_source_document_json", columns)
        self.assertEqual(stored["name"], "Legacy")
        self.assertEqual(stored["version"], 3)

    def test_rdf_xml_round_trip_preserves_supported_document_fields(self):
        rdf_xml = ontology_rdf.serialize_document(VALID_DOCUMENT)
        self.assertIn("<owl:Class", rdf_xml)
        self.assertIn("<owl:DatatypeProperty", rdf_xml)
        self.assertIn("<owl:ObjectProperty", rdf_xml)
        imported, warnings = ontology_rdf.parse_document(rdf_xml)

        self.assertEqual(warnings, [])
        self.assertEqual(imported, VALID_DOCUMENT)

    def test_rdf_xml_rejects_oversized_payload(self):
        with self.assertRaisesRegex(ValueError, "exceeds"):
            ontology_rdf.parse_document(" " * (ontology_rdf.MAX_RDF_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "exceeds"):
            ontology_rdf.serialize_document(
                VALID_DOCUMENT,
                " " * (ontology_rdf.MAX_RDF_BYTES + 1),
                VALID_DOCUMENT,
            )

    def test_external_xsd_string_stays_string_and_unsupported_axioms_warn(self):
        external = Graph()
        external.bind("owl", OWL)
        external.bind("rdfs", RDFS)
        ontology_uri = URIRef("https://example.org/schema")
        class_uri = URIRef("https://example.org/schema#Product")
        property_uri = URIRef("https://example.org/schema#name")
        external.add((ontology_uri, RDF.type, OWL.Ontology))
        external.add((ontology_uri, RDFS.label, Literal("External")))
        external.add((class_uri, RDF.type, OWL.Class))
        external.add((class_uri, RDFS.label, Literal("Product")))
        external.add((property_uri, RDF.type, OWL.DatatypeProperty))
        external.add((property_uri, RDFS.label, Literal("name")))
        external.add((property_uri, RDFS.domain, class_uri))
        external.add((property_uri, RDFS.range, XSD.string))
        external.add((class_uri, RDFS.subClassOf, OWL.Thing))

        document, warnings = ontology_rdf.parse_document(external.serialize(format="pretty-xml"))

        self.assertEqual(document["entityTypes"][0]["properties"][0]["type"], "string")
        self.assertTrue(any("subClassOf" in warning for warning in warnings))

    def test_owl_round_trip_preserves_original_xml_and_unknown_graph_triples(self):
        custom = Namespace("https://example.org/custom#")
        rdf_xml = ontology_rdf.serialize_document(VALID_DOCUMENT)
        source_graph = Graph().parse(data=rdf_xml, format="xml")
        product_uri = next(
            subject
            for subject in source_graph.subjects(RDF.type, OWL.Class)
            if str(source_graph.value(subject, ontology_rdf.ANNOTATION.id)) == "product"
        )
        restriction = BNode("priceRestriction")
        complex_range = BNode("complexPriceRange")
        source_graph.add((product_uri, custom.license, Literal("retained")))
        source_graph.add((product_uri, RDFS.label, Literal("Product (fr)", lang="fr")))
        source_graph.add((product_uri, RDFS.subClassOf, restriction))
        source_graph.add((restriction, RDF.type, OWL.Restriction))
        source_graph.add((restriction, OWL.onProperty, URIRef("https://example.org/vocab#price")))
        source_graph.add((restriction, OWL.someValuesFrom, OWL.Thing))
        price_uri = next(
            subject
            for subject in source_graph.subjects(RDF.type, OWL.DatatypeProperty)
            if str(source_graph.value(subject, ontology_rdf.ANNOTATION.id)) == "price"
        )
        source_graph.add((price_uri, RDFS.range, complex_range))
        source_graph.add((complex_range, RDF.type, RDFS.Datatype))
        source_graph.add((complex_range, OWL.onDatatype, XSD.decimal))
        original_xml = source_graph.serialize(format="pretty-xml")

        imported, _ = ontology_rdf.parse_document(original_xml)
        self.assertEqual(ontology_rdf.serialize_document(imported, original_xml, imported), original_xml)

        edited = {**imported, "name": "Renamed commerce", "entityTypes": [dict(item) for item in imported["entityTypes"]]}
        product_index = next(index for index, item in enumerate(edited["entityTypes"]) if item["id"] == "product")
        edited["entityTypes"][product_index] = {
            **edited["entityTypes"][product_index],
            "name": "Renamed Product",
        }
        merged_xml = ontology_rdf.serialize_document(edited, original_xml, imported)
        merged_graph = Graph().parse(data=merged_xml, format="xml")

        self.assertIn((product_uri, custom.license, Literal("retained")), merged_graph)
        self.assertIn((product_uri, RDFS.label, Literal("Product (fr)", lang="fr")), merged_graph)
        merged_restriction = merged_graph.value(product_uri, RDFS.subClassOf)
        self.assertIsNotNone(merged_restriction)
        self.assertIn((merged_restriction, RDF.type, OWL.Restriction), merged_graph)
        self.assertIn((merged_restriction, OWL.someValuesFrom, OWL.Thing), merged_graph)
        self.assertIn((product_uri, RDFS.label, Literal("Renamed Product")), merged_graph)
        ranges = set(merged_graph.objects(price_uri, RDFS.range))
        self.assertIn(XSD.decimal, ranges)
        complex_range_nodes = [node for node in ranges if isinstance(node, BNode)]
        self.assertTrue(complex_range_nodes)
        self.assertIn((complex_range_nodes[0], RDF.type, RDFS.Datatype), merged_graph)
        self.assertIn((complex_range_nodes[0], OWL.onDatatype, XSD.decimal), merged_graph)
        self.assertEqual(len(list(merged_graph.subjects(RDF.type, OWL.Ontology))), 1)

    def test_share_payload_round_trip_and_rejects_tampering(self):
        token = ontology_sharing.encode_document(VALID_DOCUMENT)
        self.assertEqual(ontology_sharing.decode_document(token), VALID_DOCUMENT)
        with self.assertRaisesRegex(ValueError, "Invalid"):
            ontology_sharing.decode_document(token[:-2] + "??")

    def test_share_payload_rejects_documents_over_size_limit(self):
        document = {**VALID_DOCUMENT, "description": "x" * ontology_sharing.MAX_DOCUMENT_BYTES}
        with self.assertRaisesRegex(ValueError, "too large"):
            ontology_sharing.encode_document(document)


if __name__ == "__main__":
    unittest.main()