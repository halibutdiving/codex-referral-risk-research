# Workflow Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local web console for running one isolated referral workflow per domain.

**Architecture:** Add-only implementation. The console stores workflow state under `runs/domains/<domain>/workflow_state.json`, builds subprocess commands for the existing scripts, and serves a minimal browser UI from Python standard-library HTTP handlers.

**Tech Stack:** Python standard library, existing project virtualenv, existing command-line scripts, plain HTML/CSS/JavaScript.

---

### Task 1: State Model

**Files:**
- Create: `workflow_console_state.py`
- Create: `tests/test_workflow_console_state.py`

- [ ] Write tests for domain validation, state loading, step locking, and count calculation.
- [ ] Implement domain path helpers and JSON state persistence.
- [ ] Implement `can_run_step` and `can_retry_login`.
- [ ] Verify with `python -m unittest tests/test_workflow_console_state.py`.

### Task 2: Command Builder

**Files:**
- Create: `workflow_console_commands.py`
- Create: `tests/test_workflow_console_commands.py`

- [ ] Write tests for seed generation, script command construction, invite concurrency defaults, and retry CSV generation.
- [ ] Implement command builders without invoking scripts directly.
- [ ] Implement retry CSV generation by comparing source CSV rows with expected auth JSON files.
- [ ] Verify with `python -m unittest tests/test_workflow_console_commands.py`.

### Task 3: Web Console

**Files:**
- Create: `workflow_console_app.py`

- [ ] Implement API endpoints for listing domains, creating domains, running steps, retrying login steps, task status, and log reads.
- [ ] Implement a single-page dashboard with domain sidebar, step cards, status badges, form fields, and log panel.
- [ ] Run tasks as one subprocess at a time to avoid overlapping writes in the same domain.
- [ ] Verify with `python -m py_compile workflow_console_app.py workflow_console_state.py workflow_console_commands.py`.

### Task 4: Final Verification

**Files:**
- No modifications to existing scripts.

- [ ] Run new tests.
- [ ] Run existing related tests.
- [ ] Start the console on localhost and verify the landing page returns HTTP 200.
- [ ] Confirm `git diff --name-only` contains only added workflow console files, docs, and tests.
