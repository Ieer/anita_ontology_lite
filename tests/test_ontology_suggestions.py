import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app import api, db, llm, ontology_suggestions, ontosql


class OntologySuggestionsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.env = patch.dict(os.environ, {
            "UTOPIA_SQLITE_ROOT": str(self.root),
            "UTOPIA_DATA_ROOT": str(self.root),
            "LLM_BASE_URL": "http://localhost:11434/v1",
            "LLM_MODEL": "local-model",
            "UTOPIA_ONTOLOGY_REMOTE_BASE_URL": "https://api.example.test/v1",
            "UTOPIA_ONTOLOGY_REMOTE_MODEL": "remote-model",
            "UTOPIA_ONTOLOGY_REMOTE_API_KEY": "remote-key",
            "UTOPIA_LOCAL_ADMIN_TOKEN": "admin-token",
        })
        self.env.start()
        self.conn = db.init_db(str(self.root / "app.db"))
        source = self.root / "source.db"
        with sqlite3.connect(source) as connection:
            connection.execute("CREATE TABLE accounts (id INTEGER PRIMARY KEY, name TEXT)")
        ontosql.mount_db(self.conn, "source", str(source))
        self.document_id = self.conn.execute(
            "INSERT INTO documents(title, content) VALUES (?, ?)", ("Glossary", "An account owns a name")
        ).lastrowid
        self.conn.commit()
        self.proposal = json.dumps({
            "document": {"name": "Accounts", "entityTypes": [{"id": "account", "name": "Account", "properties": [{"name": "name", "type": "string"}]}], "relationships": []},
            "evidence": [
                {"element_id": "account", "source_id": "sqlite:source", "locator": "accounts"},
                {"element_id": "account.name", "source_id": "document:1", "locator": "An account owns a name"},
            ],
        })

    def tearDown(self):
        self.conn.close()
        self.env.stop()
        self.temp_dir.cleanup()

    def test_remote_requires_confirmation_and_token_before_call(self):
        with patch.object(ontology_suggestions.llm, "complete", return_value=self.proposal) as complete:
            with self.assertRaises(PermissionError):
                ontology_suggestions.generate(self.conn, "remote", ["source"], [self.document_id], True)
            with self.assertRaises(ValueError):
                ontology_suggestions.generate(self.conn, "remote", ["source"], [self.document_id], admin_token="admin-token")
            self.assertFalse(complete.called)
            result = ontology_suggestions.generate(self.conn, "remote", ["source"], [self.document_id], True, "admin-token")
            self.assertEqual(result["document"]["name"], "Accounts")
            self.assertIn("An account owns a name", complete.call_args.args[0][1]["content"])
            self.assertEqual(complete.call_args.args[1]["LLM_MODEL"], "remote-model")
            self.assertEqual(complete.call_args.kwargs["max_tokens"], ontology_suggestions.MAX_OUTPUT_TOKENS)
            self.assertNotIn("content", result["sources"][1])
            self.assertEqual(result["document"]["sourceEvidence"], result["evidence"])
            request = self.conn.execute("SELECT * FROM ontology_suggestion_requests").fetchone()
            self.assertEqual(request["model"], "remote-model")
            self.assertEqual(request["prompt_version"], ontology_suggestions.PROMPT_VERSION)
            self.assertEqual(request["input_sha256"], result["document"]["generation"]["input_sha256"])
            self.assertEqual(len(request["input_sha256"]), 64)
            self.assertNotIn("An account owns a name", json.dumps(dict(request)))

    def test_local_uses_existing_configuration_and_rejects_unknown_sources(self):
        with patch.object(ontology_suggestions.llm, "complete", return_value=self.proposal) as complete:
            ontology_suggestions.generate(self.conn, "local", ["source"], [self.document_id])
            self.assertIsNone(complete.call_args.args[1])
            complete.return_value = self.proposal.replace("sqlite:source", "document:999")
            with self.assertRaisesRegex(ValueError, "未选材料"):
                ontology_suggestions.generate(self.conn, "local", ["source"], [self.document_id])

    def test_source_limit_prevents_external_call(self):
        self.conn.execute("UPDATE documents SET content=? WHERE id=?", ("x" * ontology_suggestions.MAX_SOURCE_CHARS, self.document_id))
        with patch.object(ontology_suggestions.llm, "complete") as complete:
            with self.assertRaisesRegex(ValueError, "超过单次模型输入上限"):
                ontology_suggestions.generate(self.conn, "remote", [], [self.document_id], True, "admin-token")
            complete.assert_not_called()

    def test_remote_chat_configuration_is_not_labeled_local(self):
        with patch.dict(os.environ, {"LLM_BASE_URL": "https://other.example.test/v1"}):
            self.assertNotIn("local", {item["id"] for item in ontology_suggestions.providers()})

    def test_existing_request_audit_table_is_migrated(self):
        legacy_path = self.root / "legacy.db"
        with sqlite3.connect(legacy_path) as connection:
            connection.execute(
                "CREATE TABLE ontology_suggestion_requests (id INTEGER PRIMARY KEY, provider TEXT NOT NULL, "
                "source_ids_json TEXT NOT NULL, created_at TEXT NOT NULL)"
            )
        with db.init_db(str(legacy_path)) as connection:
            fields = {row[1] for row in connection.execute("PRAGMA table_info(ontology_suggestion_requests)")}
        self.assertTrue({"model", "prompt_version", "input_sha256"} <= fields)

    def test_completion_output_limit_is_opt_in(self):
        response = Mock()
        response.json.return_value = {"choices": [{"message": {"content": "{}"}}]}
        with patch("requests.post", return_value=response) as post:
            llm.complete([{"role": "user", "content": "draft"}], max_tokens=ontology_suggestions.MAX_OUTPUT_TOKENS)
            self.assertEqual(post.call_args.kwargs["json"]["max_tokens"], ontology_suggestions.MAX_OUTPUT_TOKENS)
            llm.complete([{"role": "user", "content": "other"}])
            self.assertNotIn("max_tokens", post.call_args.kwargs["json"])

    def test_api_generates_only_after_confirmation(self):
        client = TestClient(api.app)
        payload = {"provider": "remote", "mount_names": ["source"], "document_ids": [self.document_id]}
        with patch.object(api, "get_conn", lambda: db.init_db(str(self.root / "app.db"))), patch.object(ontology_suggestions.llm, "complete", return_value=self.proposal) as complete:
            sources = client.get("/api/ontology-suggestions/sources")
            self.assertEqual(sources.status_code, 200)
            self.assertNotIn("remote-key", sources.text)
            forbidden = client.post("/api/ontology-suggestions/generate", json={**payload, "confirmed": True})
            self.assertEqual(forbidden.status_code, 403)
            unconfirmed = client.post("/api/ontology-suggestions/generate", json=payload, headers={"x-utopia-local-token": "admin-token"})
            self.assertEqual(unconfirmed.status_code, 400)
            complete.assert_not_called()
            generated = client.post("/api/ontology-suggestions/generate", json={**payload, "confirmed": True}, headers={"x-utopia-local-token": "admin-token"})
            self.assertEqual(generated.status_code, 200, generated.text)
            self.assertNotIn("content", generated.json()["sources"][1])
            self.assertNotIn("remote-key", generated.text)


if __name__ == "__main__":
    unittest.main()