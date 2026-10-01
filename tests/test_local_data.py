import hashlib
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import db, local_data, ontosql


class LocalSQLiteSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.sources = self.root / "sources"
        self.sources.mkdir()
        self.env_patcher = patch.dict(os.environ, {
            "UTOPIA_DATA_ROOT": str(self.root / "data"),
            "UTOPIA_SQLITE_ROOT": str(self.sources),
            "UTOPIA_LOCAL_UPLOAD_MAX_BYTES": str(2 * 1024 * 1024),
            "UTOPIA_MATERIALIZE_MAX_ROWS": "500",
        })
        self.env_patcher.start()
        self.db_patcher = patch.object(db, "DB_PATH", str(self.root / "utopia.db"))
        self.db_patcher.start()

    def tearDown(self):
        self.db_patcher.stop()
        self.env_patcher.stop()
        self.temp_dir.cleanup()

    def make_source(self, rows=None):
        path = self.sources / "hr records #1.sqlite"
        connection = sqlite3.connect(path)
        connection.execute("CREATE TABLE employees (employee_id INTEGER PRIMARY KEY, name TEXT NOT NULL, department TEXT, salary REAL)")
        connection.executemany(
            "INSERT INTO employees(employee_id, name, department, salary) VALUES (?, ?, ?, ?)",
            rows or [(1, "Same Name", "Sales", 50.0), (2, "Same Name", "Sales", 55.0), (3, "Lee", "Engineering", 60.0)],
        )
        connection.commit()
        connection.close()
        return path

    def test_paths_upload_and_read_only_queries(self):
        source = self.make_source()
        self.assertEqual(local_data.resolve_sqlite_path(str(source)), source.resolve())
        with self.assertRaises(ValueError):
            local_data.resolve_sqlite_path("/etc/passwd")

        escaped = self.root / "escape.sqlite"
        escaped_db = sqlite3.connect(escaped)
        escaped_db.execute("CREATE TABLE t (id INTEGER)")
        escaped_db.close()
        (self.sources / "escape.sqlite").symlink_to(escaped)
        with self.assertRaises(ValueError):
            local_data.resolve_sqlite_path(str(self.sources / "escape.sqlite"))

        copied, digest = local_data.store_sqlite_upload("uploaded.sqlite", source.read_bytes())
        self.assertTrue(copied.is_file())
        self.assertEqual(digest, local_data.file_sha256(copied))

        schema = local_data.sqlite_schema(copied)
        self.assertEqual(schema[0]["name"], "employees")
        self.assertEqual(schema[0]["columns"][0]["name"], "employee_id")
        result = local_data.query_table(
            copied,
            "employees",
            columns=["name", "salary"],
            filters=[{"column": "department", "operator": "eq", "value": "Sales"}],
            limit=10,
        )
        self.assertEqual(len(result["rows"]), 2)
        self.assertEqual(result["rows"][0]["name"], "Same Name")
        first_page = local_data.query_table(copied, "employees", columns=["employee_id"], limit=2)
        second_page = local_data.query_table(copied, "employees", columns=["employee_id"], limit=2, offset=2)
        self.assertTrue(first_page["has_more"])
        self.assertEqual(len(second_page["rows"]), 1)
        self.assertFalse(second_page["has_more"])

        before = hashlib.sha256(copied.read_bytes()).hexdigest()
        self.assertEqual(len(local_data.execute_readonly(copied, "SELECT * FROM employees")["rows"]), 3)
        for statement in (
            "DELETE FROM employees",
            "WITH changed AS (DELETE FROM employees RETURNING *) SELECT * FROM changed",
            "ATTACH DATABASE ':memory:' AS other",
            "PRAGMA writable_schema=ON",
            "SELECT load_extension('x')",
            "SELECT randomblob(1048576)",
        ):
            with self.subTest(statement=statement), self.assertRaises(ValueError):
                local_data.execute_readonly(copied, statement)
        self.assertEqual(before, hashlib.sha256(copied.read_bytes()).hexdigest())

    def test_mapping_preview_and_explicit_materialization_are_idempotent(self):
        source = self.make_source()
        conn = db.init_db()
        try:
            ontosql.mount_db(conn, "hr-local", str(source))
            ontosql.map_table(conn, "hr-local", "employees", "Employee", "name", "employee_id")
            ontosql.map_column(conn, "hr-local", "employees", "department", "Department", "works_in")
            ontosql.map_column(conn, "hr-local", "employees", "salary", "Department", "works_in", "salary")

            preview = ontosql.preview_materialization(conn, "hr-local")
            self.assertTrue(preview["ok"], preview["errors"])
            self.assertEqual(preview["summary"], {"rows": 3, "entities": 5, "facts": 3, "attributes": 3})
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0], 0)

            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            result = ontosql.materialize_mappings(conn, "hr-local")
            self.assertFalse(result["already_materialized"])
            self.assertEqual(result["entities_created"], 5)
            self.assertEqual(result["facts_created"], 3)
            self.assertEqual(result["attrs_created"], 3)
            self.assertEqual(result["valid_from"], preview["valid_from"])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM entities WHERE type='Employee'").fetchone()[0], 3)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM facts WHERE source LIKE 'sqlite_snapshot:%'").fetchone()[0], 3)
            self.assertEqual(source_hash, hashlib.sha256(source.read_bytes()).hexdigest())

            repeated = ontosql.materialize_mappings(conn, "hr-local")
            self.assertTrue(repeated["already_materialized"])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0], 5)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM materialization_batches WHERE status='complete'").fetchone()[0], 1)
        finally:
            conn.close()

    def test_preview_rejects_duplicate_source_keys(self):
        source = self.make_source()
        conn = db.init_db()
        try:
            ontosql.mount_db(conn, "duplicate-source", str(source))
            ontosql.map_table(conn, "duplicate-source", "employees", "Employee", "name", "name")
            preview = ontosql.preview_materialization(conn, "duplicate-source")
            self.assertFalse(preview["ok"])
            self.assertTrue(any("重复源键" in error for error in preview["errors"]))
        finally:
            conn.close()

    def test_internal_snapshot_reader_honors_configured_batch_limit_over_one_thousand(self):
        source = self.make_source([(row_id, f"Employee {row_id}", "Ops", float(row_id)) for row_id in range(1, 1202)])
        with patch.dict(os.environ, {"UTOPIA_MATERIALIZE_MAX_ROWS": "1200"}):
            rows = local_data.read_table_rows(source, "employees", ["name"], "employee_id", 1100)
        self.assertEqual(len(rows), 1101)
        self.assertEqual(rows[-1]["name"], "Employee 1101")

    def test_legacy_seed_hr_mount_without_metadata_still_works(self):
        sample = self.root / "sample_hr.db"
        source = sqlite3.connect(sample)
        source.execute("CREATE TABLE employees (id INTEGER PRIMARY KEY, name TEXT)")
        source.execute("INSERT INTO employees VALUES (1, 'Legacy sample')")
        source.commit()
        source.close()
        conn = db.init_db()
        try:
            conn.execute("INSERT INTO mounted_dbs(name, path) VALUES ('hr', ?)", (str(sample),))
            conn.commit()
            self.assertEqual(ontosql.schema_of(conn, "hr")["employees"], ["id", "name"])
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
