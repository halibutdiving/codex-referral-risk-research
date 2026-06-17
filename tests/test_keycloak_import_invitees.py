import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from keycloak_import_invitees import (
    KeycloakAdmin,
    build_user_payload,
    extract_invited_emails,
    write_login_csv,
)


class KeycloakImportInviteesTests(unittest.TestCase):
    def test_extract_invited_emails_deduplicates_successful_invites(self):
        data = [
            {
                "auth_file": "seed1.json",
                "emails": ["requested-only@example.com"],
                "invites": [
                    {"email": "Alice@Example.com"},
                    {"email": "bob@example.com"},
                ],
            },
            {
                "auth_file": "seed2.json",
                "invites": [
                    {"email": "alice@example.com"},
                    {"email": "carol@example.com"},
                    {"not_email": "ignored@example.com"},
                ],
            },
        ]

        self.assertEqual(
            extract_invited_emails(data),
            ["alice@example.com", "bob@example.com", "carol@example.com"],
        )

    def test_build_user_payload_uses_email_as_username_by_default(self):
        payload = build_user_payload("alice@example.com")

        self.assertEqual(payload["username"], "alice@example.com")
        self.assertEqual(payload["email"], "alice@example.com")
        self.assertTrue(payload["enabled"])
        self.assertFalse(payload["emailVerified"])

    def test_build_user_payload_can_strip_domain_for_username(self):
        payload = build_user_payload("alice@example.com", username_mode="localpart")

        self.assertEqual(payload["username"], "alice")
        self.assertEqual(payload["email"], "alice@example.com")

    def test_write_login_csv_outputs_email_and_password_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "invitee_accounts.csv"

            write_login_csv(out_path, ["alice@example.com", "bob@example.com"], "Passw0rd!")

            with out_path.open(newline="", encoding="utf-8") as f:
                rows = list(csv.reader(f))

        self.assertEqual(
            rows,
            [
                ["alice@example.com", "Passw0rd!"],
                ["bob@example.com", "Passw0rd!"],
            ],
        )

    def test_find_user_refreshes_admin_token_once_after_401(self):
        client = KeycloakAdmin(
            base_url="https://keycloak.example.com",
            realm="master",
            admin_user="admin",
            admin_password="admin",
        )
        client.authenticate = Mock()
        unauthorized = Mock(status_code=401, text="Unauthorized")
        ok = Mock(status_code=200)
        ok.json.return_value = [{"id": "user-1", "username": "alice@example.com"}]
        client.session.request = Mock(side_effect=[unauthorized, ok])

        self.assertEqual(client.find_user_id_by_username("alice@example.com"), "user-1")
        client.authenticate.assert_called_once()
        self.assertEqual(client.session.request.call_count, 2)


if __name__ == "__main__":
    unittest.main()
