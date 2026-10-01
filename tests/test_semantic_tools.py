import tempfile
import unittest
from pathlib import Path

from app import db, graph, ontology, ontology_documents, semantic_tools


class SemanticToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.conn = db.init_db(str(Path(self.temp_dir.name) / "semantic.db"))

    def tearDown(self):
        self.conn.close()
        self.temp_dir.cleanup()

    def test_browse_separates_document_terms_runtime_types_and_mappings(self):
        document = ontology_documents.create_document(
            self.conn,
            {
                "name": "Commerce",
                "entityTypes": [{
                    "id": "invoice",
                    "name": "Invoice",
                    "description": "A customer invoice",
                    "properties": [{"name": "total", "type": "decimal"}],
                }],
                "relationships": [],
            },
        )
        ontology.add_type(self.conn, "Invoice")
        self.conn.execute("INSERT INTO mounted_dbs(name, path) VALUES ('finance', '/not-opened/source.sqlite')")
        self.conn.execute(
            "INSERT INTO table_mappings(mount_name, table_name, entity_type, name_col, key_col) "
            "VALUES ('finance', 'invoices', 'Invoice', 'number', 'invoice_id')"
        )
        self.conn.execute(
            "INSERT INTO column_mappings(mount_name, table_name, column_name, attr_name) "
            "VALUES ('finance', 'invoices', 'amount', 'total')"
        )
        self.conn.commit()

        terms = semantic_tools.browse_semantics(self.conn, "Invoice", kind="term")
        self.assertEqual(
            {item["representation"] for item in terms["results"]},
            {"ontology_document", "runtime_type"},
        )
        self.assertEqual(terms["results"][0]["source"]["id"], document["id"])

        resolved = semantic_tools.resolve_semantics(self.conn, ["Invoice"])
        self.assertEqual(resolved["results"][0]["status"], "ambiguous")
        self.assertIsNone(resolved["results"][0]["resolved"])

        design_only = semantic_tools.resolve_semantics(self.conn, ["customer invoice"])
        result = design_only["results"][0]
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["resolved"]["representation"], "ontology_document")
        self.assertEqual(result["linked"]["mappings"][0]["table_name"], "invoices")

    def test_browse_bounds_results_and_inputs(self):
        ontology_documents.create_document(
            self.conn,
            {
                "name": "Metrics",
                "entityTypes": [
                    {"id": f"metric_{index}", "name": f"Metric {index}", "properties": []}
                    for index in range(8)
                ],
                "relationships": [],
            },
        )
        result = semantic_tools.browse_semantics(self.conn, "Metric", kind="term", limit=6)
        self.assertEqual(len(result["results"]), 6)
        self.assertTrue(result["has_more"])
        with self.assertRaises(ValueError):
            semantic_tools.browse_semantics(self.conn, "x" * 201)
        with self.assertRaises(ValueError):
            semantic_tools.browse_semantics(self.conn, "Metric", limit=7)

    def test_resolve_returns_not_found_and_filters_facts_by_valid_time(self):
        ontology.add_type(self.conn, "Company")
        company = graph.add_entity(self.conn, "Example Co", "Company")
        project = graph.add_entity(self.conn, "Project", "Thing")
        graph.add_fact(self.conn, company, "owns", project, "2020-01-01", "2023-01-01", "test")

        resolved = semantic_tools.resolve_semantics(self.conn, ["Company"], as_of="2021-01-01")
        facts = resolved["results"][0]["instances"][0]["facts"]
        self.assertEqual(facts[0]["predicate"], "owns")
        self.assertEqual(facts[0]["source"], "test")

        expired = semantic_tools.resolve_semantics(self.conn, ["Company"], as_of="2024-01-01")
        self.assertEqual(expired["results"][0]["instances"][0]["facts"], [])
        missing = semantic_tools.resolve_semantics(self.conn, ["missing concept"])
        self.assertEqual(missing["results"][0]["status"], "not_found")

    def test_resolve_believed_at_shows_only_facts_known_at_that_time(self):
        ontology.add_type(self.conn, "Company")
        subject = graph.add_entity(self.conn, "Example Co", "Company")
        early = graph.add_entity(self.conn, "early", "Thing")
        late = graph.add_entity(self.conn, "late", "Thing")
        cursor = self.conn.execute(
            "INSERT INTO facts(subject_id, predicate, object_id, valid_from, asserted_at, status) "
            "VALUES (?, 'status', ?, '2020-01-01', '2020-01-01', 'superseded')",
            (subject, early),
        )
        old_fact_id = cursor.lastrowid
        cursor = self.conn.execute(
            "INSERT INTO facts(subject_id, predicate, object_id, valid_from, asserted_at) "
            "VALUES (?, 'status', ?, '2020-01-01', '2022-01-01')",
            (subject, late),
        )
        new_fact_id = cursor.lastrowid
        self.conn.execute("UPDATE facts SET superseded_by=? WHERE id=?", (new_fact_id, old_fact_id))
        self.conn.commit()

        before = semantic_tools.resolve_semantics(self.conn, ["Company"], believed_at="2021-01-01")
        after = semantic_tools.resolve_semantics(self.conn, ["Company"], believed_at="2023-01-01")
        self.assertEqual(before["results"][0]["instances"][0]["facts"][0]["object_name"], "early")
        self.assertEqual(after["results"][0]["instances"][0]["facts"][0]["object_name"], "late")

    def test_resolve_rejects_oversized_requests(self):
        with self.assertRaises(ValueError):
            semantic_tools.resolve_semantics(self.conn, ["x"] * 6)
        with self.assertRaises(ValueError):
            semantic_tools.resolve_semantics(self.conn, ["x" * 201])
        with self.assertRaises(ValueError):
            semantic_tools.resolve_semantics(self.conn, ["x"], context="y" * 501)