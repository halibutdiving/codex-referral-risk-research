import json
import tempfile
import unittest
from pathlib import Path

from workflow_console_state import (
    WorkflowStore,
    count_valid_auth_files,
    safe_domain_name,
)


class WorkflowConsoleStateTests(unittest.TestCase):
    def test_safe_domain_name_accepts_plain_domains(self):
        self.assertEqual(safe_domain_name("NextGenArt.Online "), "nextgenart.online")

    def test_safe_domain_name_rejects_path_traversal(self):
        with self.assertRaises(ValueError):
            safe_domain_name("../nextgenart.online")

    def test_create_domain_initializes_state_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WorkflowStore(Path(tmp))

            state = store.create_domain("nextgenart.online")

            self.assertEqual(state["domain"], "nextgenart.online")
            self.assertEqual(state["steps"], {})
            self.assertTrue((Path(tmp) / "domains" / "nextgenart.online").is_dir())
            with self.assertRaises(FileExistsError):
                store.create_domain("nextgenart.online")

    def test_step_can_run_once_after_previous_step_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WorkflowStore(Path(tmp))
            store.create_domain("nextgenart.online")

            self.assertTrue(store.can_run_step("nextgenart.online", "create_seed"))
            self.assertFalse(store.can_run_step("nextgenart.online", "import_seed"))

            store.mark_step("nextgenart.online", "create_seed", "done")

            self.assertFalse(store.can_run_step("nextgenart.online", "create_seed"))
            self.assertTrue(store.can_run_step("nextgenart.online", "import_seed"))

    def test_login_retry_allowed_after_login_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WorkflowStore(Path(tmp))
            store.create_domain("nextgenart.online")

            self.assertFalse(store.can_retry_login("nextgenart.online", "seed_login"))
            store.mark_step("nextgenart.online", "seed_login", "done")

            self.assertTrue(store.can_retry_login("nextgenart.online", "seed_login"))
            self.assertFalse(store.can_run_step("nextgenart.online", "seed_login"))

    def test_count_valid_auth_files_requires_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            auth_dir = Path(tmp)
            (auth_dir / "ok.json").write_text(
                json.dumps({"tokens": {"access_token": "a"}}),
                encoding="utf-8",
            )
            (auth_dir / "bad.json").write_text(json.dumps({"tokens": {}}), encoding="utf-8")
            (auth_dir / "metadata.json").write_text(
                json.dumps({"tokens": {"access_token": "a"}}),
                encoding="utf-8",
            )

            self.assertEqual(count_valid_auth_files(auth_dir), 1)

    def test_load_state_bootstraps_existing_domain_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp)
            domain_dir = runs / "domains" / "nextgenart.online"
            seeds = domain_dir / "seeds"
            invitees = domain_dir / "invitees"
            seeds.mkdir(parents=True)
            invitees.mkdir()
            (domain_dir / "seed_accounts.csv").write_text("seed001@nextgenart.online,pw\n", encoding="utf-8")
            (domain_dir / "invite_results.json").write_text("[]", encoding="utf-8")
            (domain_dir / "invitee_accounts.csv").write_text("child@nextgenart.online,pw\n", encoding="utf-8")
            (seeds / "seed001.json").write_text(json.dumps({"tokens": {"access_token": "a"}}), encoding="utf-8")
            (invitees / "child.json").write_text(json.dumps({"tokens": {"refresh_token": "r"}}), encoding="utf-8")
            store = WorkflowStore(runs)

            state = store.load_state("nextgenart.online")

            self.assertEqual(state["steps"]["create_seed"]["status"], "done")
            self.assertEqual(state["steps"]["seed_login"]["status"], "done")
            self.assertEqual(state["steps"]["invite"]["status"], "done")
            self.assertEqual(state["steps"]["import_invitees"]["status"], "done")
            self.assertEqual(state["steps"]["invitee_login"]["status"], "done")
            self.assertTrue((domain_dir / "workflow_state.json").exists())

    def test_mark_stale_running_steps_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WorkflowStore(Path(tmp))
            store.create_domain("nextgenart.online")
            store.mark_step("nextgenart.online", "create_seed", "done")
            store.mark_step("nextgenart.online", "seed_login", "running", log_path="/tmp/seed.log")

            changed = store.mark_stale_running_steps_failed("console restarted")
            state = store.load_state("nextgenart.online")

            self.assertEqual(changed, 1)
            self.assertEqual(state["steps"]["seed_login"]["status"], "failed")
            self.assertEqual(state["steps"]["seed_login"]["returncode"], 1)
            self.assertEqual(state["steps"]["seed_login"]["error"], "console restarted")

    def test_update_form_settings_persists_domain_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WorkflowStore(Path(tmp))
            store.create_domain("nextgenart.online")

            store.update_form_settings(
                "nextgenart.online",
                {
                    "seed_login": {
                        "proxy_template": "http://user-sid-{sid}:pass@example.test:3010",
                        "concurrency": 30,
                        "retries": 2,
                    },
                    "import_seed": {
                        "admin_password": "secret",
                        "admin_password_file": "/tmp/keycloak.password",
                    },
                },
            )
            state = store.load_state("nextgenart.online")

            self.assertEqual(
                state["settings"]["forms"]["seed_login"]["proxy_template"],
                "http://user-sid-{sid}:pass@example.test:3010",
            )
            self.assertEqual(state["settings"]["forms"]["seed_login"]["concurrency"], "30")
            self.assertEqual(state["settings"]["forms"]["import_seed"]["admin_password_file"], "/tmp/keycloak.password")
            self.assertNotIn("admin_password", state["settings"]["forms"]["import_seed"])


if __name__ == "__main__":
    unittest.main()
