import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import api, db, local_markdown, search


class LocalDataApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.sources = self.root / "sources"
        self.sources.mkdir()
        self.database_path = self.root / "utopia.db"
        self.env_patcher = patch.dict(os.environ, {
            "UTOPIA_DATA_ROOT": str(self.root / "data"),
            "UTOPIA_SQLITE_ROOT": str(self.sources),
            "UTOPIA_LOCAL_UPLOAD_MAX_BYTES": str(4 * 1024 * 1024),
            "UTOPIA_ENABLE_MOUNT_WRITES": "0",
        })
        self.env_patcher.start()
        self.conn_patcher = patch.object(api, "get_conn", lambda: db.init_db(str(self.database_path)))
        self.conn_patcher.start()
        self.client = TestClient(api.app)
        self.source = self.sources / "inventory.sqlite"
        connection = sqlite3.connect(self.source)
        connection.execute("CREATE TABLE employees (id INTEGER PRIMARY KEY, name TEXT, department TEXT, salary REAL)")
        connection.executemany(
            "INSERT INTO employees(id, name, department, salary) VALUES (?, ?, ?, ?)",
            [(1, "Alex", "Sales", 50.0), (2, "Alex", "Sales", 60.0), (3, "Rae", "Ops", 70.0)],
        )
        connection.commit()
        connection.close()

    def tearDown(self):
        self.conn_patcher.stop()
        self.env_patcher.stop()
        self.temp_dir.cleanup()

    def main_connection(self):
        return db.init_db(str(self.database_path))

    def test_mounted_read_only_schema_query_mapping_and_confirmed_snapshot(self):
        mounted = self.client.post("/api/mounted", json={"name": "inventory", "path": str(self.source)})
        self.assertEqual(mounted.status_code, 200, mounted.text)
        self.assertNotIn("path", self.client.get("/api/mounted").json()[0])
        second_source = self.sources / "different.sqlite"
        second_source.write_bytes(self.source.read_bytes())
        collision = self.client.post("/api/mounted", json={"name": "inventory", "path": str(second_source)})
        self.assertEqual(collision.status_code, 400)
        self.assertIn("新名称", collision.json()["detail"])

        schema = self.client.get("/api/mounted/inventory/schema")
        self.assertEqual(schema.status_code, 200, schema.text)
        self.assertEqual([column["name"] for column in schema.json()[0]["columns"]], ["id", "name", "department", "salary"])
        table_query = self.client.post("/api/mounted/query/table", json={
            "mount_name": "inventory", "table": "employees", "columns": ["id", "name"],
            "filters": [{"column": "department", "operator": "eq", "value": "Sales"}],
        })
        self.assertEqual(len(table_query.json()["rows"]), 2)

        select = self.client.post("/api/mounted/query", json={"mount_name": "inventory", "sql": "SELECT COUNT(*) AS n FROM employees"})
        self.assertEqual(select.status_code, 200, select.text)
        self.assertEqual(select.json()[0]["n"], 3)
        for statement in ("DELETE FROM employees", "ATTACH DATABASE ':memory:' AS x", "PRAGMA writable_schema=ON"):
            response = self.client.post("/api/mounted/query", json={"mount_name": "inventory", "sql": statement})
            self.assertEqual(response.status_code, 400, statement)
        blocked_write = self.client.post("/api/mounted/write", json={"mount_name": "inventory", "sql": "DELETE FROM employees"})
        self.assertEqual(blocked_write.status_code, 403)
        self.assertEqual(sqlite3.connect(self.source).execute("SELECT COUNT(*) FROM employees").fetchone()[0], 3)

        table_map = self.client.post("/api/mounted/map", json={
            "mount_name": "inventory", "table": "employees", "entity_type": "Employee", "name_col": "name", "key_col": "id",
        })
        self.assertEqual(table_map.status_code, 200, table_map.text)
        column_map = self.client.post("/api/mounted/map/column", json={
            "mount_name": "inventory", "table": "employees", "column": "department", "target_type": "Department", "predicate": "works_in",
        })
        self.assertEqual(column_map.status_code, 200, column_map.text)
        preview = self.client.get("/api/mounted/inventory/materialize-preview")
        self.assertTrue(preview.json()["ok"], preview.text)
        self.assertEqual(preview.json()["summary"]["rows"], 3)
        not_confirmed = self.client.post("/api/mounted/materialize", json={"mount_name": "inventory"})
        self.assertEqual(not_confirmed.status_code, 400)
        stale_confirm = self.client.post("/api/mounted/materialize", json={
            "mount_name": "inventory", "confirmed": True, "source_sha256": "stale", "mapping_sha256": preview.json()["mapping_sha256"],
        })
        self.assertEqual(stale_confirm.status_code, 409)
        confirmed = self.client.post("/api/mounted/materialize", json={
            "mount_name": "inventory", "confirmed": True,
            "source_sha256": preview.json()["source_sha256"], "mapping_sha256": preview.json()["mapping_sha256"],
        })
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        repeat = self.client.post("/api/mounted/materialize", json={
            "mount_name": "inventory", "confirmed": True,
            "source_sha256": preview.json()["source_sha256"], "mapping_sha256": preview.json()["mapping_sha256"],
        })
        self.assertTrue(repeat.json()["already_materialized"])
        connection = self.main_connection()
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM entities WHERE type='Employee'").fetchone()[0], 3)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM materialization_batches WHERE status='complete'").fetchone()[0], 1)
        finally:
            connection.close()

    def test_upload_copy_and_local_markdown_index_lifecycle(self):
        upload = self.client.post(
            "/api/mounted/upload",
            data={"name": "uploaded"},
            files={"file": ("inventory.sqlite", self.source.read_bytes(), "application/octet-stream")},
        )
        self.assertEqual(upload.status_code, 200, upload.text)
        self.assertTrue((self.sources / upload.json()["filename"]).is_file())

        created = self.client.post("/api/local-markdown", json={
            "title": "Inventory ontology notes", "body": "# Warehouses\n\nEvery stocked item has a stable inventory identifier.",
        })
        self.assertEqual(created.status_code, 200, created.text)
        file_id = created.json()["file_id"]
        relative_path = self.main_connection().execute(
            "SELECT relative_path FROM local_markdown_files WHERE file_id=?", (file_id,)
        ).fetchone()[0]
        markdown_path = local_markdown._resolve_relative_path(relative_path)
        self.assertTrue(markdown_path.is_file())
        self.assertIn('"file_id"', markdown_path.read_text(encoding="utf-8"))

        updated = self.client.post("/api/local-markdown", json={
            "file_id": file_id, "title": "Inventory ontology notes", "body": "# Replenishment\n\nThe reorder threshold is twenty units.",
        })
        self.assertEqual(updated.status_code, 200, updated.text)
        connection = self.main_connection()
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 1)
            results = search.hybrid_search(connection, "reorder threshold", k=5)
            self.assertTrue(any("Replenishment" in item["content"] or "reorder threshold" in item["content"] for item in results))
        finally:
            connection.close()

        metadata, _body = local_markdown.parse_frontmatter(markdown_path.read_text(encoding="utf-8"))
        markdown_path.write_text(
            local_markdown.render_frontmatter(metadata, "# Safety review\n\nSafety reserve is fifteen units."),
            encoding="utf-8",
        )
        changed = self.client.get(f"/api/local-markdown/{file_id}").json()
        self.assertFalse(changed["indexed"])
        synced_file = self.client.post(f"/api/local-markdown/{file_id}/sync")
        self.assertEqual(synced_file.status_code, 200, synced_file.text)
        connection = self.main_connection()
        try:
            results = search.hybrid_search(connection, "safety reserve", k=5)
            self.assertTrue(any("Safety reserve" in item["content"] for item in results))
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM chunk_vectors").fetchone()[0], 0)
        finally:
            connection.close()

        removed_index = self.client.delete(f"/api/local-markdown/{file_id}/index")
        self.assertTrue(removed_index.json()["removed"])
        self.assertTrue(markdown_path.is_file())
        connection = self.main_connection()
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 0)
        finally:
            connection.close()

        imported = self.client.post(
            "/api/local-markdown/upload",
            files={"file": ("field-guide.md", b"# Field guide\n\nLocal markdown import works.", "text/markdown")},
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        self.assertTrue(imported.json()["file_id"])

        synced = self.client.post(f"/api/local-markdown/{file_id}/sync")
        self.assertEqual(synced.status_code, 200, synced.text)
        deleted = self.client.delete(f"/api/local-markdown/{file_id}")
        self.assertTrue(deleted.json()["deleted"])
        self.assertFalse(markdown_path.exists())
        connection = self.main_connection()
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 1)
        finally:
            connection.close()
        self.assertTrue(self.client.delete(f"/api/local-markdown/{imported.json()['file_id']}").json()["deleted"])
        connection = self.main_connection()
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 0)
        finally:
            connection.close()

    def test_remote_local_data_routes_require_admin_token(self):
        remote_client = TestClient(api.app, client=("203.0.113.10", 3456))
        denied = remote_client.get("/api/mounted")
        self.assertEqual(denied.status_code, 403)
        with patch.dict(os.environ, {"UTOPIA_LOCAL_ADMIN_TOKEN": "local-admin-secret"}):
            allowed = remote_client.get("/api/mounted", headers={"x-utopia-local-token": "local-admin-secret"})
        self.assertEqual(allowed.status_code, 200, allowed.text)


if __name__ == "__main__":
    unittest.main()
