# Workflow Console Design

## Goal

Build a local web console that orchestrates the existing referral research scripts without modifying them. The console manages one isolated workflow per domain.

## Hard Constraints

- Only add new files for this feature.
- Do not modify existing scripts such as `codex_protocol_login.py`, `codex_invitation_batch.py`, `codex_activation_batch.py`, or `keycloak_import_invitees.py`.
- Store all domain artifacts under `runs/domains/<domain>/`.
- A domain can run one full workflow only once.
- Failed login attempts can be retried by generating retry CSVs for accounts missing successful JSON outputs.
- Invite steps are not rerunnable once completed.

## Workflow Steps

1. Create seed CSV and seed password.
2. Import seed users into Keycloak.
3. Log in seed users.
4. Invite target users.
5. Import successful invitees into Keycloak.
6. Log in invitee users.
7. Activate invitee users.

## UI

The UI is a local dashboard served by a Python standard-library HTTP server. It has:

- Domain selector and new domain creation.
- Step cards with status, counts, inputs, and action buttons.
- Proxy template and concurrency fields where relevant.
- Invite concurrency defaulting to the number of successful seed auth JSON files.
- Live task log polling.

## Backend

New modules only:

- `workflow_console_state.py`: domain state, file paths, counts, locking rules.
- `workflow_console_commands.py`: command construction and retry CSV generation.
- `workflow_console_app.py`: HTTP server, static UI, task runner.

The backend calls existing scripts as subprocesses. It writes logs under `runs/domains/<domain>/logs/`.
