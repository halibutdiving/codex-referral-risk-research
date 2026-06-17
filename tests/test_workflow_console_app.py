import unittest

from workflow_console_app import INDEX_HTML


class WorkflowConsoleAppTests(unittest.TestCase):
    def test_auto_refresh_preserves_form_input_drafts(self):
        self.assertIn("let formDraft = {};", INDEX_HTML)
        self.assertIn("saveFormDraft();", INDEX_HTML)
        self.assertIn('input.addEventListener("input"', INDEX_HTML)
        self.assertIn("draftKey(step, name)", INDEX_HTML)
        self.assertIn("formDraft = {};", INDEX_HTML)

    def test_refresh_polling_is_task_scoped_and_non_intrusive(self):
        self.assertNotIn("setInterval(refresh, 3000)", INDEX_HTML)
        self.assertIn("setInterval(pollTask, 3000)", INDEX_HTML)
        self.assertIn('api("/api/task")', INDEX_HTML)
        self.assertIn("pendingSummaryRefresh", INDEX_HTML)
        self.assertIn("function isEditingForm()", INDEX_HTML)

    def test_running_task_can_be_cancelled_from_ui(self):
        self.assertIn('id="cancelTask"', INDEX_HTML)
        self.assertIn("async function cancelTask()", INDEX_HTML)
        self.assertIn('api("/api/cancel"', INDEX_HTML)
        self.assertIn("renderTaskControls()", INDEX_HTML)


if __name__ == "__main__":
    unittest.main()
