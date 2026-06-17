import unittest

from workflow_console_app import INDEX_HTML


class WorkflowConsoleAppTests(unittest.TestCase):
    def test_auto_refresh_preserves_form_input_drafts(self):
        self.assertIn("let formDraft = {};", INDEX_HTML)
        self.assertIn("saveFormDraft();", INDEX_HTML)
        self.assertIn('input.addEventListener("input"', INDEX_HTML)
        self.assertIn("draftKey(step, name)", INDEX_HTML)
        self.assertIn("formDraft = {};", INDEX_HTML)


if __name__ == "__main__":
    unittest.main()
