import os
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
PYTHON = PROJECT_DIR / ".venv" / "bin" / "python"


class SeedLifecycleTests(unittest.TestCase):
    def test_seed_preserves_existing_data_unless_reset_is_explicit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "poc.db"
            env = os.environ.copy()
            env["UTOPIA_LITE_DB"] = str(database_path)

            def run_seed(*args):
                return subprocess.run(
                    [str(PYTHON), "seed.py", *args],
                    cwd=PROJECT_DIR,
                    env=env,
                    check=True,
                    capture_output=True,
                    text=True,
                )

            run_seed()
            subprocess.run(
                [
                    str(PYTHON),
                    "-c",
                    "from app import db, graph; c=db.init_db(); graph.add_entity(c, 'POC sentinel'); c.close()",
                ],
                cwd=PROJECT_DIR,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )

            result = run_seed()
            self.assertIn("跳过种子初始化", result.stdout)

            subprocess.run(
                [
                    str(PYTHON),
                    "-c",
                    "from app import db, graph; c=db.init_db(); names={r['name'] for r in graph.list_entities(c)}; assert 'POC sentinel' in names; c.close()",
                ],
                cwd=PROJECT_DIR,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )

            run_seed("--reset")
            subprocess.run(
                [
                    str(PYTHON),
                    "-c",
                    "from app import db, graph; c=db.init_db(); names={r['name'] for r in graph.list_entities(c)}; assert 'POC sentinel' not in names; c.close()",
                ],
                cwd=PROJECT_DIR,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )


if __name__ == "__main__":
    unittest.main()