import csv
import json
import tempfile
import unittest
from pathlib import Path

from workflow_console_commands import (
    DEFAULT_PROXY_TEMPLATE,
    WorkflowCommandBuilder,
    create_retry_csv_for_missing_auth,
    generate_seed_accounts,
)


class WorkflowConsoleCommandsTests(unittest.TestCase):
    def test_generate_seed_accounts_creates_csv_and_password_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            domain_dir = Path(tmp) / "nextgenart.online"

            result = generate_seed_accounts(domain_dir, "nextgenart.online", seed_count=3)

            self.assertEqual(result["seed_count"], 3)
            self.assertTrue((domain_dir / "seed_accounts.csv").exists())
            self.assertTrue((domain_dir / "seed_accounts.password.txt").exists())
            with (domain_dir / "seed_accounts.csv").open(newline="", encoding="utf-8") as f:
                rows = list(csv.reader(f))
            suffix = result["batch_id"]
            self.assertEqual([row[0] for row in rows], [
                f"seed001-{suffix}@nextgenart.online",
                f"seed002-{suffix}@nextgenart.online",
                f"seed003-{suffix}@nextgenart.online",
            ])
            self.assertTrue(all(row[1] for row in rows))

    def test_generate_seed_accounts_uses_new_batch_each_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            domain_dir = Path(tmp) / "nextgenart.online"

            first = generate_seed_accounts(domain_dir, "nextgenart.online", seed_count=2)
            with (domain_dir / "seed_accounts.csv").open(newline="", encoding="utf-8") as f:
                first_rows = list(csv.reader(f))
            second = generate_seed_accounts(domain_dir, "nextgenart.online", seed_count=2)
            with (domain_dir / "seed_accounts.csv").open(newline="", encoding="utf-8") as f:
                second_rows = list(csv.reader(f))

            self.assertNotEqual(first["batch_id"], second["batch_id"])
            self.assertNotEqual([row[0] for row in first_rows], [row[0] for row in second_rows])

    def test_seed_login_command_uses_domain_paths_and_proxy_template(self):
        builder = WorkflowCommandBuilder(Path("/repo"), Path("/repo/runs"))

        command = builder.seed_login(
            "nextgenart.online",
            proxy_template=DEFAULT_PROXY_TEMPLATE,
            concurrency=7,
            retries=3,
        )

        self.assertEqual(command[0], "/repo/.venv/bin/python")
        self.assertIn("codex_protocol_login.py", command)
        self.assertIn("runs/domains/nextgenart.online/seed_accounts.csv", command)
        self.assertIn("runs/domains/nextgenart.online/seeds", command)
        self.assertIn("--proxy-template", command)
        self.assertIn(DEFAULT_PROXY_TEMPLATE, command)
        self.assertIn("7", command)
        self.assertIn("3", command)

    def test_invite_command_defaults_concurrency_to_successful_seed_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            domain_dir = root / "runs" / "domains" / "nextgenart.online"
            seeds_dir = domain_dir / "seeds"
            seeds_dir.mkdir(parents=True)
            for idx in range(2):
                (seeds_dir / f"seed{idx}.json").write_text(
                    json.dumps({"tokens": {"access_token": "token"}}),
                    encoding="utf-8",
                )
            builder = WorkflowCommandBuilder(root, root / "runs")

            command = builder.invite(
                "nextgenart.online",
                proxy_template=DEFAULT_PROXY_TEMPLATE,
                concurrency=None,
                per_account=5,
                burst_timeout=60,
            )

            self.assertIn("--concurrency", command)
            self.assertEqual(command[command.index("--concurrency") + 1], "2")
            self.assertIn("--burst-invite", command)
            self.assertIn("runs/domains/nextgenart.online/invite_results.json", command)

    def test_create_retry_csv_for_missing_auth(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source_csv = base / "accounts.csv"
            auth_dir = base / "auth"
            retry_csv = base / "retry.csv"
            auth_dir.mkdir()
            with source_csv.open("w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["alice@example.com", "pw"])
                writer.writerow(["bob@example.com", "pw"])
            (auth_dir / "alice__at__example.com.json").write_text(
                json.dumps({"tokens": {"access_token": "token"}}),
                encoding="utf-8",
            )

            count = create_retry_csv_for_missing_auth(source_csv, auth_dir, retry_csv)

            self.assertEqual(count, 1)
            with retry_csv.open(newline="", encoding="utf-8") as f:
                rows = list(csv.reader(f))
            self.assertEqual(rows, [["bob@example.com", "pw"]])


if __name__ == "__main__":
    unittest.main()
