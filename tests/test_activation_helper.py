import unittest
from unittest.mock import patch

import codex_activation_helper


class FakeActivationResponse:
    status_code = 200
    text = "{}"

    def json(self):
        return {}


class RecordingSession:
    def __init__(self):
        self.trust_env = True
        self.proxies = {}
        self.urls = []

    def request(self, method, url, **kwargs):
        self.urls.append(url)
        return FakeActivationResponse()

    def post(self, *args, **kwargs):
        return FakeActivationResponse()


class ActivationHelperTests(unittest.TestCase):
    def test_protocol_activation_skips_remaining_balance_probe(self):
        session = RecordingSession()
        auth_data = {
            "tokens": {
                "access_token": "access",
                "id_token": "id",
            }
        }

        with (
            patch.object(codex_activation_helper.requests, "Session", return_value=session),
            patch.object(codex_activation_helper, "jwt_decode", return_value={}),
            patch.object(codex_activation_helper, "extract_account_id", return_value="acct_123"),
        ):
            ok = codex_activation_helper.run_protocol_activation(auth_data)

        self.assertTrue(ok)
        self.assertTrue(session.urls)
        self.assertFalse(any("remaining_balance" in url for url in session.urls))


if __name__ == "__main__":
    unittest.main()
