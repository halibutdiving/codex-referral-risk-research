#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""State and filesystem helpers for the local workflow console."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any


STEP_ORDER = [
    "create_seed",
    "import_seed",
    "seed_login",
    "invite",
    "import_invitees",
    "invitee_login",
    "activate",
]

LOGIN_STEPS = {"seed_login", "invitee_login"}


def safe_domain_name(value: str) -> str:
    domain = (value or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]*[a-z0-9]", domain):
        raise ValueError("域名只能包含字母、数字、点和连字符")
    if ".." in domain or "/" in domain or "\\" in domain:
        raise ValueError("域名不能包含路径片段")
    return domain


def is_valid_auth_json(path: Path) -> bool:
    if "metadata" in path.name or "system_bak" in path.name:
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    tokens = data.get("tokens", {})
    return isinstance(tokens, dict) and bool(tokens.get("access_token") or tokens.get("refresh_token"))


def count_valid_auth_files(path: Path) -> int:
    if not path.is_dir():
        return 0
    return sum(1 for fp in path.glob("*.json") if is_valid_auth_json(fp))


def count_csv_rows(path: Path) -> int:
    if not path.exists():
        return 0
    count = 0
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip() and not line.lstrip().startswith("#"):
                count += 1
    return count


class WorkflowStore:
    def __init__(self, runs_dir: Path):
        self.runs_dir = Path(runs_dir)
        self.domains_dir = self.runs_dir / "domains"

    def domain_dir(self, domain: str) -> Path:
        return self.domains_dir / safe_domain_name(domain)

    def state_path(self, domain: str) -> Path:
        return self.domain_dir(domain) / "workflow_state.json"

    def create_domain(self, domain: str) -> dict[str, Any]:
        safe_domain = safe_domain_name(domain)
        domain_dir = self.domain_dir(safe_domain)
        state_path = self.state_path(safe_domain)
        if state_path.exists():
            raise FileExistsError(f"域名已存在: {safe_domain}")
        domain_dir.mkdir(parents=True, exist_ok=False)
        state = {
            "domain": safe_domain,
            "created_at": _now(),
            "updated_at": _now(),
            "steps": {},
            "settings": {},
        }
        self.save_state(safe_domain, state)
        return state

    def load_state(self, domain: str) -> dict[str, Any]:
        path = self.state_path(domain)
        if not path.exists():
            bootstrapped = self.bootstrap_existing_domain(domain)
            if bootstrapped:
                return bootstrapped
            raise FileNotFoundError(f"域名未初始化: {safe_domain_name(domain)}")
        with path.open(encoding="utf-8") as f:
            return json.load(f)

    def bootstrap_existing_domain(self, domain: str) -> dict[str, Any] | None:
        safe_domain = safe_domain_name(domain)
        domain_path = self.domain_dir(safe_domain)
        if not domain_path.is_dir():
            return None
        steps: dict[str, dict[str, Any]] = {}
        if (domain_path / "seed_accounts.csv").exists():
            steps["create_seed"] = {"status": "done", "updated_at": _now(), "bootstrapped": True}
        if (domain_path / "seed_accounts.imported.csv").exists() or (domain_path / "seed_import_payload.json").exists():
            steps["import_seed"] = {"status": "done", "updated_at": _now(), "bootstrapped": True}
        if count_valid_auth_files(domain_path / "seeds") > 0:
            steps["seed_login"] = {"status": "done", "updated_at": _now(), "bootstrapped": True}
        if (domain_path / "invite_results.json").exists():
            steps["invite"] = {"status": "done", "updated_at": _now(), "bootstrapped": True}
        if (domain_path / "invitee_accounts.csv").exists():
            steps["import_invitees"] = {"status": "done", "updated_at": _now(), "bootstrapped": True}
        if count_valid_auth_files(domain_path / "invitees") > 0:
            steps["invitee_login"] = {"status": "done", "updated_at": _now(), "bootstrapped": True}
        activation_logs = list(domain_path.glob("activation_*.log")) + list((domain_path / "logs").glob("activate_*.log"))
        if activation_logs:
            steps["activate"] = {"status": "done", "updated_at": _now(), "bootstrapped": True}
        if not steps:
            return None
        state = {
            "domain": safe_domain,
            "created_at": _now(),
            "updated_at": _now(),
            "steps": steps,
            "settings": {},
            "bootstrapped": True,
        }
        self.save_state(safe_domain, state)
        return state

    def save_state(self, domain: str, state: dict[str, Any]) -> None:
        path = self.state_path(domain)
        path.parent.mkdir(parents=True, exist_ok=True)
        state["updated_at"] = _now()
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def mark_step(self, domain: str, step: str, status: str, **extra: Any) -> dict[str, Any]:
        state = self.load_state(domain)
        steps = state.setdefault("steps", {})
        entry = dict(steps.get(step, {}))
        entry.update(extra)
        entry["status"] = status
        entry["updated_at"] = _now()
        steps[step] = entry
        self.save_state(domain, state)
        return state

    def can_run_step(self, domain: str, step: str) -> bool:
        if step not in STEP_ORDER:
            raise ValueError(f"未知步骤: {step}")
        state = self.load_state(domain)
        status = state.get("steps", {}).get(step, {}).get("status")
        if status in {"running", "done"}:
            return False
        idx = STEP_ORDER.index(step)
        if idx == 0:
            return True
        prev_step = STEP_ORDER[idx - 1]
        return state.get("steps", {}).get(prev_step, {}).get("status") == "done"

    def can_retry_login(self, domain: str, step: str) -> bool:
        if step not in LOGIN_STEPS:
            return False
        state = self.load_state(domain)
        return state.get("steps", {}).get(step, {}).get("status") == "done"

    def list_domains(self) -> list[dict[str, Any]]:
        if not self.domains_dir.is_dir():
            return []
        domains = []
        for path in sorted(self.domains_dir.iterdir()):
            if not path.is_dir():
                continue
            state_path = path / "workflow_state.json"
            if not state_path.exists():
                self.bootstrap_existing_domain(path.name)
            if not state_path.exists():
                continue
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            domains.append(state)
        return domains

    def summary(self, domain: str) -> dict[str, Any]:
        domain_path = self.domain_dir(domain)
        state = self.load_state(domain)
        return {
            "state": state,
            "counts": {
                "seed_csv": count_csv_rows(domain_path / "seed_accounts.csv"),
                "seed_auth": count_valid_auth_files(domain_path / "seeds"),
                "invitee_csv": count_csv_rows(domain_path / "invitee_accounts.csv"),
                "invitee_auth": count_valid_auth_files(domain_path / "invitees"),
            },
            "paths": {
                "domain_dir": str(domain_path),
                "seed_csv": str(domain_path / "seed_accounts.csv"),
                "seed_auth_dir": str(domain_path / "seeds"),
                "invite_results": str(domain_path / "invite_results.json"),
                "invitee_csv": str(domain_path / "invitee_accounts.csv"),
                "invitee_auth_dir": str(domain_path / "invitees"),
            },
        }


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
