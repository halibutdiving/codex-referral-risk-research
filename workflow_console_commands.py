#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Command builders for the add-only workflow console."""

from __future__ import annotations

import csv
import secrets
import string
from pathlib import Path

from workflow_console_state import count_valid_auth_files, safe_domain_name


DEFAULT_PROXY_TEMPLATE = (
    "http://proxy-user-region-US-st-Oregon-sid-{sid}-t-20:"
    "proxy-password@proxy.example.com:3010"
)


def safe_account_filename(email: str) -> str:
    name = email.strip().replace("@", "__at__")
    out = []
    for ch in name:
        if ch.isalnum() or ch in "._-":
            out.append(ch)
        else:
            out.append("_")
    value = "".join(out).strip("._")
    return value or "account"


def generate_password(length: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*()-_=+[]{}:,.?"
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(c.islower() for c in password)
            and any(c.isupper() for c in password)
            and any(c.isdigit() for c in password)
            and any(c in "!@#$%^&*()-_=+[]{}:,.?" for c in password)
        ):
            return password


def generate_seed_accounts(domain_dir: Path, domain: str, seed_count: int = 100) -> dict[str, str | int]:
    if seed_count <= 0:
        raise ValueError("seed_count must be positive")
    domain = safe_domain_name(domain)
    domain_dir.mkdir(parents=True, exist_ok=True)
    csv_path = domain_dir / "seed_accounts.csv"
    password_path = domain_dir / "seed_accounts.password.txt"
    if password_path.exists():
        password = password_path.read_text(encoding="utf-8").strip()
    else:
        password = generate_password()
        password_path.write_text(password + "\n", encoding="utf-8")
        password_path.chmod(0o600)
    rows = [[f"seed{i:03d}@{domain}", password] for i in range(1, seed_count + 1)]
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)
    csv_path.chmod(0o600)
    return {"seed_count": seed_count, "csv_path": str(csv_path), "password_path": str(password_path)}


def create_retry_csv_for_missing_auth(source_csv: Path, auth_dir: Path, retry_csv: Path) -> int:
    missing_rows: list[list[str]] = []
    with source_csv.open(newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) < 2 or not row[0].strip():
                continue
            email = row[0].strip().lower()
            auth_path = auth_dir / f"{safe_account_filename(email)}.json"
            if not _is_valid_auth_json(auth_path):
                missing_rows.append([email, row[1]])
    retry_csv.parent.mkdir(parents=True, exist_ok=True)
    with retry_csv.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(missing_rows)
    retry_csv.chmod(0o600)
    return len(missing_rows)


class WorkflowCommandBuilder:
    def __init__(self, project_root: Path, runs_dir: Path):
        self.project_root = Path(project_root)
        self.runs_dir = Path(runs_dir)

    @property
    def python_bin(self) -> str:
        return str(self.project_root / ".venv" / "bin" / "python")

    def domain_dir(self, domain: str) -> Path:
        return self.runs_dir / "domains" / safe_domain_name(domain)

    def rel(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.project_root))
        except ValueError:
            return str(path)

    def import_seed(
        self,
        domain: str,
        keycloak_url: str,
        realm: str,
        admin_user: str,
        admin_password: str,
    ) -> list[str]:
        domain_dir = self.domain_dir(domain)
        return [
            self.python_bin,
            "keycloak_import_invitees.py",
            "--invite-results",
            self.rel(_seed_import_json(domain_dir)),
            "--keycloak-url",
            keycloak_url,
            "--realm",
            realm,
            "--admin-user",
            admin_user,
            "--admin-password",
            admin_password,
            "--user-password",
            _read_secret(domain_dir / "seed_accounts.password.txt"),
            "--out-csv",
            self.rel(domain_dir / "seed_accounts.imported.csv"),
        ]

    def seed_login(
        self,
        domain: str,
        proxy_template: str,
        concurrency: int,
        retries: int,
        csv_name: str = "seed_accounts.csv",
    ) -> list[str]:
        domain_dir = self.domain_dir(domain)
        return self._login_command(
            domain_dir / csv_name,
            domain_dir / "seeds",
            proxy_template,
            concurrency,
            retries,
        )

    def invite(
        self,
        domain: str,
        proxy_template: str,
        concurrency: int | None,
        per_account: int,
        burst_timeout: int,
    ) -> list[str]:
        domain_dir = self.domain_dir(domain)
        seed_count = count_valid_auth_files(domain_dir / "seeds")
        final_concurrency = concurrency if concurrency and concurrency > 0 else max(1, seed_count)
        return [
            self.python_bin,
            "codex_invitation_batch.py",
            "--auth-dir",
            self.rel(domain_dir / "seeds"),
            "--domain",
            safe_domain_name(domain),
            "--per-account",
            str(per_account),
            "--concurrency",
            str(final_concurrency),
            "--proxy-template",
            proxy_template,
            "--save-back",
            "--burst-invite",
            "--burst-timeout",
            str(burst_timeout),
            "--out",
            self.rel(domain_dir / "invite_results.json"),
        ]

    def import_invitees(
        self,
        domain: str,
        keycloak_url: str,
        realm: str,
        admin_user: str,
        admin_password: str,
    ) -> list[str]:
        domain_dir = self.domain_dir(domain)
        return [
            self.python_bin,
            "keycloak_import_invitees.py",
            "--invite-results",
            self.rel(domain_dir / "invite_results.json"),
            "--keycloak-url",
            keycloak_url,
            "--realm",
            realm,
            "--admin-user",
            admin_user,
            "--admin-password",
            admin_password,
            "--user-password",
            _read_secret(domain_dir / "seed_accounts.password.txt"),
            "--out-csv",
            self.rel(domain_dir / "invitee_accounts.csv"),
        ]

    def invitee_login(
        self,
        domain: str,
        proxy_template: str,
        concurrency: int,
        retries: int,
        csv_name: str = "invitee_accounts.csv",
    ) -> list[str]:
        domain_dir = self.domain_dir(domain)
        return self._login_command(
            domain_dir / csv_name,
            domain_dir / "invitees",
            proxy_template,
            concurrency,
            retries,
        )

    def activate(self, domain: str, proxy_template: str, concurrency: int) -> list[str]:
        domain_dir = self.domain_dir(domain)
        return [
            self.python_bin,
            "codex_activation_batch.py",
            "--auth-dir",
            self.rel(domain_dir / "invitees"),
            "--proxy-template",
            proxy_template,
            "--concurrency",
            str(concurrency),
            "--save-back",
        ]

    def _login_command(
        self,
        csv_path: Path,
        out_dir: Path,
        proxy_template: str,
        concurrency: int,
        retries: int,
    ) -> list[str]:
        return [
            self.python_bin,
            "codex_protocol_login.py",
            "--csv",
            self.rel(csv_path),
            "--out-dir",
            self.rel(out_dir),
            "--proxy-template",
            proxy_template,
            "--concurrency",
            str(concurrency),
            "--retries",
            str(retries),
            "--skip-existing",
        ]


def _seed_import_json(domain_dir: Path) -> Path:
    import json

    source = domain_dir / "seed_accounts.csv"
    out = domain_dir / "seed_import_payload.json"
    invites = []
    with source.open(newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            if row and row[0].strip():
                invites.append({"email": row[0].strip().lower()})
    out.write_text(json.dumps([{"invites": invites}], ensure_ascii=False), encoding="utf-8")
    out.chmod(0o600)
    return out


def _read_secret(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def _is_valid_auth_json(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    tokens = data.get("tokens", {})
    return isinstance(tokens, dict) and bool(tokens.get("access_token") or tokens.get("refresh_token"))
