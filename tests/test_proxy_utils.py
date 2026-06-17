import base64
import json
import tempfile
import unittest
from pathlib import Path

from proxy_utils import (
    extract_email_from_auth_file,
    proxy_for_email,
    proxy_for_auth_file,
    proxy_sid_for_email,
)


def unsigned_jwt(payload):
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return f"header.{encoded}.signature"


class ProxyUtilsTests(unittest.TestCase):
    def test_proxy_sid_for_email_is_stable(self):
        self.assertEqual(
            proxy_sid_for_email("Seed001@Gatekeeper1998.xyz"),
            proxy_sid_for_email(" seed001@gatekeeper1998.xyz "),
        )

    def test_proxy_for_email_replaces_sid(self):
        proxy = proxy_for_email(
            "seed001@gatekeeper1998.xyz",
            "http://user-sid-{sid}:pass@example.net:3010",
        )

        self.assertRegex(proxy, r"^http://user-sid-[A-Za-z0-9]{8}:pass@example.net:3010$")

    def test_extract_email_from_auth_file_uses_access_token_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "account.json"
            path.write_text(
                json.dumps(
                    {
                        "tokens": {
                            "access_token": unsigned_jwt(
                                {"https://api.openai.com/profile": {"email": "alice@example.com"}}
                            )
                        }
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(extract_email_from_auth_file(path), "alice@example.com")

    def test_proxy_for_auth_file_falls_back_to_file_stem(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "seed001__at__example.com.json"
            path.write_text(json.dumps({"tokens": {}}), encoding="utf-8")

            proxy = proxy_for_auth_file(path, "http://sid-{sid}:pass@example.net:3010")

        self.assertRegex(proxy, r"^http://sid-[A-Za-z0-9]{8}:pass@example.net:3010$")


if __name__ == "__main__":
    unittest.main()
