import unittest

from proxy_utils import proxy_for_email, proxy_sid_for_email


class CodexProtocolLoginProxyTests(unittest.TestCase):
    def test_proxy_sid_for_email_is_stable_and_case_insensitive(self):
        first = proxy_sid_for_email("Seed001@Gatekeeper1998.xyz")
        second = proxy_sid_for_email(" seed001@gatekeeper1998.xyz ")

        self.assertEqual(first, second)
        self.assertEqual(len(first), 8)
        self.assertRegex(first, r"^[A-Za-z0-9]{8}$")

    def test_proxy_sid_for_email_changes_for_different_email(self):
        self.assertNotEqual(
            proxy_sid_for_email("seed001@gatekeeper1998.xyz"),
            proxy_sid_for_email("seed002@gatekeeper1998.xyz"),
        )

    def test_proxy_for_email_replaces_sid_template(self):
        proxy = proxy_for_email(
            "seed001@gatekeeper1998.xyz",
            "http://user-sid-{sid}-t-20:pass@example.net:3010",
        )

        self.assertNotIn("{sid}", proxy)
        self.assertRegex(proxy, r"^http://user-sid-[A-Za-z0-9]{8}-t-20:pass@example.net:3010$")


if __name__ == "__main__":
    unittest.main()
