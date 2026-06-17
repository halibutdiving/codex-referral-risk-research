import sys
import tempfile
import time
import unittest
from pathlib import Path

from workflow_console_app import TaskManager
from workflow_console_commands import WorkflowCommandBuilder
from workflow_console_state import WorkflowStore


def wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    return None


class WorkflowConsoleTaskManagerTests(unittest.TestCase):
    def test_cancel_running_task_marks_step_cancelled(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp)
            store = WorkflowStore(runs)
            store.create_domain("nextgenart.online")
            store.mark_step("nextgenart.online", "create_seed", "done")
            store.mark_step("nextgenart.online", "import_seed", "done")
            manager = TaskManager(store, WorkflowCommandBuilder(Path.cwd(), runs))
            log_path = store.domain_dir("nextgenart.online") / "logs" / "sleep.log"

            manager.start(
                "nextgenart.online",
                "seed_login",
                [sys.executable, "-c", "import time; print('started', flush=True); time.sleep(30)"],
                log_path,
            )
            self.assertIsNotNone(wait_for(lambda: manager.snapshot() and manager.snapshot().get("pid")))

            task = manager.cancel("test requested")

            self.assertEqual(task["status"], "cancelling")
            state = wait_for(
                lambda: store.load_state("nextgenart.online")
                if store.load_state("nextgenart.online")["steps"]["seed_login"]["status"] == "cancelled"
                else None
            )
            self.assertIsNotNone(state)
            self.assertTrue(store.can_run_step("nextgenart.online", "seed_login"))


if __name__ == "__main__":
    unittest.main()
