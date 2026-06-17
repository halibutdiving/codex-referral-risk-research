import threading
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import codex_invitation_batch


class FakeResponse:
    status_code = 200
    text = ""

    def json(self):
        return {"invites": [{"email": "invitee@example.com"}]}


class FakeSession:
    def __init__(self, post_event):
        self.post_event = post_event

    def post(self, *args, **kwargs):
        self.post_event.set()
        return FakeResponse()


class InvitationBarrierTests(unittest.TestCase):
    def test_process_account_waits_for_barrier_before_invite_post(self):
        post_event = threading.Event()
        release_event = threading.Event()

        class Gate:
            def wait(self):
                release_event.wait(timeout=1)

        with tempfile.TemporaryDirectory() as tmp:
            auth_path = Path(tmp) / "seed.json"
            auth_path.write_text("{}", encoding="utf-8")

            with (
                patch.object(codex_invitation_batch, "load_auth_tokens", return_value=("access", "account")),
                patch.object(codex_invitation_batch, "build_session", return_value=("fake", FakeSession(post_event))),
                patch.object(codex_invitation_batch, "check_eligibility", return_value=1),
                patch.object(codex_invitation_batch, "random_email", return_value="invitee@example.com"),
            ):
                worker = threading.Thread(
                    target=codex_invitation_batch.process_account,
                    args=(auth_path, "example.com", 1),
                    kwargs={"invite_barrier": Gate()},
                )
                worker.start()
                time.sleep(0.05)
                self.assertFalse(post_event.is_set())

                release_event.set()
                worker.join(timeout=1)

        self.assertTrue(post_event.is_set())


if __name__ == "__main__":
    unittest.main()
