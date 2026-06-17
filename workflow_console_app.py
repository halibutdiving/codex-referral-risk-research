#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local web console for domain-scoped workflow orchestration."""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from workflow_console_commands import (
    DEFAULT_PROXY_TEMPLATE,
    WorkflowCommandBuilder,
    create_retry_csv_for_missing_auth,
    generate_seed_accounts,
)
from workflow_console_state import WorkflowStore, safe_domain_name


PROJECT_ROOT = Path(__file__).resolve().parent
RUNS_DIR = PROJECT_ROOT / "runs"
DEFAULT_KEYCLOAK_URL = "http://127.0.0.1:18081"
DEFAULT_REALM = "master"
DEFAULT_ADMIN_USER = "admin"
DEFAULT_ADMIN_PASSWORD_FILE = RUNS_DIR / "keycloak_admin.password.txt"


class TaskManager:
    def __init__(self, store: WorkflowStore, builder: WorkflowCommandBuilder):
        self.store = store
        self.builder = builder
        self.lock = threading.Lock()
        self.current: dict[str, Any] | None = None

    def snapshot(self) -> dict[str, Any] | None:
        with self.lock:
            return dict(self.current) if self.current else None

    def start(self, domain: str, step: str, command: list[str], log_path: Path) -> dict[str, Any]:
        with self.lock:
            if self.current and self.current.get("status") == "running":
                raise RuntimeError("已有任务正在运行")
            task = {
                "domain": safe_domain_name(domain),
                "step": step,
                "command": command,
                "log_path": str(log_path),
                "status": "running",
                "started_at": _now(),
                "returncode": None,
            }
            self.current = task
        thread = threading.Thread(target=self._run, args=(task, log_path), daemon=True)
        thread.start()
        return task

    def _run(self, task: dict[str, Any], log_path: Path) -> None:
        domain = task["domain"]
        step = task["step"]
        self.store.mark_step(domain, step, "running", log_path=str(log_path))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        returncode = 1
        try:
            with log_path.open("w", encoding="utf-8") as log:
                log.write("$ " + " ".join(_quote_arg(x) for x in task["command"]) + "\n\n")
                log.flush()
                proc = subprocess.Popen(
                    task["command"],
                    cwd=str(PROJECT_ROOT),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=_subprocess_env(),
                )
                assert proc.stdout is not None
                for line in proc.stdout:
                    log.write(line)
                    log.flush()
                returncode = proc.wait()
        except Exception as e:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as log:
                log.write(f"\n[workflow-console] task failed: {e}\n")
            returncode = 1

        status = "done" if returncode == 0 else "failed"
        self.store.mark_step(domain, step, status, log_path=str(log_path), returncode=returncode)
        with self.lock:
            if self.current:
                self.current["status"] = status
                self.current["returncode"] = returncode
                self.current["finished_at"] = _now()


class WorkflowHandler(BaseHTTPRequestHandler):
    store = WorkflowStore(RUNS_DIR)
    builder = WorkflowCommandBuilder(PROJECT_ROOT, RUNS_DIR)
    tasks = TaskManager(store, builder)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {self.address_string()} {fmt % args}")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                self._send_html(INDEX_HTML)
            elif parsed.path == "/api/domains":
                self._send_json({"domains": self.store.list_domains(), "task": self.tasks.snapshot()})
            elif parsed.path == "/api/domain":
                query = parse_qs(parsed.query)
                domain = _required(query, "domain")
                self._send_json(self.store.summary(domain))
            elif parsed.path == "/api/task":
                self._send_json({"task": self.tasks.snapshot()})
            elif parsed.path == "/api/log":
                query = parse_qs(parsed.query)
                path = Path(_required(query, "path"))
                self._send_text(_read_log(path))
            elif parsed.path == "/api/config":
                self._send_json({
                    "proxy_template": DEFAULT_PROXY_TEMPLATE,
                    "keycloak_url": DEFAULT_KEYCLOAK_URL,
                    "realm": DEFAULT_REALM,
                    "admin_user": DEFAULT_ADMIN_USER,
                    "admin_password_file": str(DEFAULT_ADMIN_PASSWORD_FILE),
                })
            else:
                self.send_error(404)
        except Exception as e:
            self._send_json({"error": str(e)}, status=400)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            payload = self._read_json()
            if parsed.path == "/api/domain":
                state = self.store.create_domain(payload["domain"])
                self._send_json({"state": state})
                return
            if parsed.path == "/api/run":
                self._handle_run(payload)
                return
            if parsed.path == "/api/retry-login":
                self._handle_retry_login(payload)
                return
            self.send_error(404)
        except Exception as e:
            self._send_json({"error": str(e)}, status=400)

    def _handle_run(self, payload: dict[str, Any]) -> None:
        domain = safe_domain_name(payload["domain"])
        step = payload["step"]
        if not self.store.can_run_step(domain, step):
            raise RuntimeError(f"步骤不可运行或已完成: {step}")

        if step == "create_seed":
            domain_dir = self.store.domain_dir(domain)
            seed_count = int(payload.get("seed_count") or 100)
            result = generate_seed_accounts(domain_dir, domain, seed_count)
            self.store.mark_step(domain, step, "done", result=result)
            self._send_json({"ok": True, "result": result})
            return

        command = self._command_for_step(domain, step, payload)
        log_path = self._log_path(domain, step)
        task = self.tasks.start(domain, step, command, log_path)
        self._send_json({"ok": True, "task": task})

    def _handle_retry_login(self, payload: dict[str, Any]) -> None:
        domain = safe_domain_name(payload["domain"])
        step = payload["step"]
        if not self.store.can_retry_login(domain, step):
            raise RuntimeError(f"步骤不可补跑: {step}")
        domain_dir = self.store.domain_dir(domain)
        if step == "seed_login":
            retry_csv = domain_dir / "seed_accounts.retry_missing.csv"
            missing = create_retry_csv_for_missing_auth(domain_dir / "seed_accounts.csv", domain_dir / "seeds", retry_csv)
            command = self.builder.seed_login(
                domain,
                _proxy_template(payload),
                _positive_int(payload, "concurrency", 5),
                _nonnegative_int(payload, "retries", 2),
                csv_name=retry_csv.name,
            )
        elif step == "invitee_login":
            retry_csv = domain_dir / "invitee_accounts.retry_missing.csv"
            missing = create_retry_csv_for_missing_auth(domain_dir / "invitee_accounts.csv", domain_dir / "invitees", retry_csv)
            command = self.builder.invitee_login(
                domain,
                _proxy_template(payload),
                _positive_int(payload, "concurrency", 5),
                _nonnegative_int(payload, "retries", 2),
                csv_name=retry_csv.name,
            )
        else:
            raise RuntimeError(f"不支持补跑: {step}")
        if missing == 0:
            self._send_json({"ok": True, "missing": 0, "message": "没有缺失账号"})
            return
        log_path = self._log_path(domain, f"{step}_retry")
        task = self.tasks.start(domain, step, command, log_path)
        self._send_json({"ok": True, "missing": missing, "task": task})

    def _command_for_step(self, domain: str, step: str, payload: dict[str, Any]) -> list[str]:
        if step == "import_seed":
            return self.builder.import_seed(
                domain,
                payload.get("keycloak_url") or DEFAULT_KEYCLOAK_URL,
                payload.get("realm") or DEFAULT_REALM,
                payload.get("admin_user") or DEFAULT_ADMIN_USER,
                _admin_password(payload),
            )
        if step == "seed_login":
            return self.builder.seed_login(
                domain,
                _proxy_template(payload),
                _positive_int(payload, "concurrency", 5),
                _nonnegative_int(payload, "retries", 2),
            )
        if step == "invite":
            return self.builder.invite(
                domain,
                _proxy_template(payload),
                _optional_positive_int(payload, "concurrency"),
                _positive_int(payload, "per_account", 5),
                _positive_int(payload, "burst_timeout", 60),
            )
        if step == "import_invitees":
            return self.builder.import_invitees(
                domain,
                payload.get("keycloak_url") or DEFAULT_KEYCLOAK_URL,
                payload.get("realm") or DEFAULT_REALM,
                payload.get("admin_user") or DEFAULT_ADMIN_USER,
                _admin_password(payload),
            )
        if step == "invitee_login":
            return self.builder.invitee_login(
                domain,
                _proxy_template(payload),
                _positive_int(payload, "concurrency", 5),
                _nonnegative_int(payload, "retries", 2),
            )
        if step == "activate":
            return self.builder.activate(domain, _proxy_template(payload), _positive_int(payload, "concurrency", 5))
        raise RuntimeError(f"未知步骤: {step}")

    def _log_path(self, domain: str, step: str) -> Path:
        name = f"{step}_{time.strftime('%Y%m%d_%H%M%S')}.log"
        return self.store.domain_dir(domain) / "logs" / name

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        return json.loads(raw or "{}")

    def _send_json(self, data: Any, status: int = 200) -> None:
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_html(self, html: str) -> None:
        raw = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_text(self, text: str) -> None:
        raw = text.encode("utf-8", errors="replace")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def run(host: str, port: int) -> None:
    server = ThreadingHTTPServer((host, port), WorkflowHandler)
    print(f"Workflow console: http://{host}:{port}")
    server.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser(description="Local workflow console")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    run(args.host, args.port)
    return 0


def _required(query: dict[str, list[str]], key: str) -> str:
    values = query.get(key) or []
    if not values or not values[0]:
        raise ValueError(f"missing query parameter: {key}")
    return values[0]


def _admin_password(payload: dict[str, Any]) -> str:
    if payload.get("admin_password"):
        return str(payload["admin_password"])
    path = Path(payload.get("admin_password_file") or DEFAULT_ADMIN_PASSWORD_FILE)
    return path.read_text(encoding="utf-8").strip()


def _proxy_template(payload: dict[str, Any]) -> str:
    value = payload.get("proxy_template") or DEFAULT_PROXY_TEMPLATE
    if "{sid}" not in value:
        raise ValueError("proxy_template must include {sid}")
    return str(value)


def _positive_int(payload: dict[str, Any], key: str, default: int) -> int:
    value = int(payload.get(key) or default)
    if value <= 0:
        raise ValueError(f"{key} must be positive")
    return value


def _optional_positive_int(payload: dict[str, Any], key: str) -> int | None:
    raw = payload.get(key)
    if raw in (None, "", 0, "0"):
        return None
    value = int(raw)
    if value <= 0:
        raise ValueError(f"{key} must be positive")
    return value


def _nonnegative_int(payload: dict[str, Any], key: str, default: int) -> int:
    value = int(payload.get(key) if payload.get(key) not in (None, "") else default)
    if value < 0:
        raise ValueError(f"{key} must be non-negative")
    return value


def _read_log(path: Path) -> str:
    resolved = path.resolve()
    runs = RUNS_DIR.resolve()
    if not str(resolved).startswith(str(runs)):
        raise ValueError("log path outside runs directory")
    if not resolved.exists():
        return ""
    return resolved.read_text(encoding="utf-8", errors="replace")[-120000:]


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def _quote_arg(value: str) -> str:
    if not value or any(ch.isspace() for ch in value):
        return "'" + value.replace("'", "'\\''") + "'"
    return value


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Referral Flow Console</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f8fafc;
      --panel: #ffffff;
      --line: #dbe3ea;
      --text: #0f172a;
      --muted: #475569;
      --blue: #0369a1;
      --green: #15803d;
      --red: #b91c1c;
      --amber: #b45309;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    * { box-sizing: border-box; }
    body { margin: 0; background: var(--bg); color: var(--text); }
    button, input, select { font: inherit; }
    button { cursor: pointer; border: 1px solid var(--line); background: #fff; color: var(--text); border-radius: 6px; padding: 8px 10px; }
    button.primary { background: var(--blue); color: #fff; border-color: var(--blue); }
    button:disabled { cursor: not-allowed; opacity: .55; }
    input { width: 100%; border: 1px solid var(--line); border-radius: 6px; padding: 8px 10px; background: #fff; color: var(--text); }
    label { display: block; color: var(--muted); font-size: 12px; margin-bottom: 4px; }
    .layout { display: grid; grid-template-columns: 280px 1fr; min-height: 100vh; }
    .sidebar { border-right: 1px solid var(--line); background: #eef6fb; padding: 16px; }
    .main { padding: 18px; }
    .brand { font-weight: 700; margin-bottom: 16px; }
    .domain-item { width: 100%; text-align: left; margin-bottom: 8px; }
    .domain-item.active { border-color: var(--blue); color: var(--blue); }
    .new-domain { display: grid; gap: 8px; margin-top: 16px; }
    .topbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
    .status-grid { display: grid; grid-template-columns: repeat(4, minmax(120px, 1fr)); gap: 10px; margin-bottom: 16px; }
    .metric { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 12px; }
    .metric-value { font-size: 22px; font-weight: 700; }
    .steps { display: grid; grid-template-columns: repeat(2, minmax(320px, 1fr)); gap: 12px; }
    .card { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 14px; }
    .card-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 10px; }
    .card-title { font-weight: 700; }
    .badge { font-size: 12px; border-radius: 999px; padding: 3px 8px; border: 1px solid var(--line); color: var(--muted); }
    .badge.done { color: var(--green); border-color: #86efac; background: #f0fdf4; }
    .badge.running { color: var(--blue); border-color: #7dd3fc; background: #eff6ff; }
    .badge.failed { color: var(--red); border-color: #fecaca; background: #fef2f2; }
    .form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
    .form-grid .wide { grid-column: 1 / -1; }
    .actions { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; }
    .log { margin-top: 16px; background: #08111f; color: #dbeafe; border-radius: 8px; padding: 12px; min-height: 260px; max-height: 420px; overflow: auto; white-space: pre-wrap; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 12px; }
    .small { font-size: 12px; color: var(--muted); }
    @media (max-width: 960px) {
      .layout { grid-template-columns: 1fr; }
      .sidebar { border-right: 0; border-bottom: 1px solid var(--line); }
      .steps { grid-template-columns: 1fr; }
      .status-grid { grid-template-columns: repeat(2, minmax(120px, 1fr)); }
    }
  </style>
</head>
<body>
  <div class="layout">
    <aside class="sidebar">
      <div class="brand">Referral Flow Console</div>
      <div id="domains"></div>
      <div class="new-domain">
        <input id="newDomain" placeholder="nextgenart.online">
        <button class="primary" onclick="createDomain()">新建域名</button>
      </div>
    </aside>
    <main class="main">
      <div class="topbar">
        <div>
          <h2 id="title">选择域名</h2>
          <div class="small" id="pathLine"></div>
        </div>
        <button onclick="refresh()">刷新</button>
      </div>
      <div class="status-grid">
        <div class="metric"><div class="small">Seed CSV</div><div class="metric-value" id="seedCsv">0</div></div>
        <div class="metric"><div class="small">Seed 登录</div><div class="metric-value" id="seedAuth">0</div></div>
        <div class="metric"><div class="small">子号 CSV</div><div class="metric-value" id="inviteeCsv">0</div></div>
        <div class="metric"><div class="small">子号登录</div><div class="metric-value" id="inviteeAuth">0</div></div>
      </div>
      <section class="steps" id="steps"></section>
      <pre class="log" id="log">等待任务...</pre>
    </main>
  </div>
<script>
let selectedDomain = "";
let config = {};
let summary = null;
let task = null;
let formDraft = {};
let pendingSummaryRefresh = false;

const stepDefs = [
  ["create_seed", "1. 造 seed", [["seed_count", "Seed 数量", "100"]]],
  ["import_seed", "2. 导入 seed 到 Keycloak", [["keycloak_url", "Keycloak URL", ""], ["realm", "Realm", ""], ["admin_user", "Admin 用户", ""], ["admin_password_file", "Admin 密码文件", ""]]],
  ["seed_login", "3. Seed 登录", [["proxy_template", "代理模板", ""], ["concurrency", "并发", "5"], ["retries", "Retries", "2"]], true],
  ["invite", "4. 邀请", [["proxy_template", "代理模板", ""], ["concurrency", "并发，空则用 seed 成功数", ""], ["per_account", "每母号邀请数", "5"], ["burst_timeout", "Barrier 超时秒", "60"]]],
  ["import_invitees", "5. 导入被邀请用户", [["keycloak_url", "Keycloak URL", ""], ["realm", "Realm", ""], ["admin_user", "Admin 用户", ""], ["admin_password_file", "Admin 密码文件", ""]]],
  ["invitee_login", "6. 子号登录", [["proxy_template", "代理模板", ""], ["concurrency", "并发", "5"], ["retries", "Retries", "2"]], true],
  ["activate", "7. 激活", [["proxy_template", "代理模板", ""], ["concurrency", "并发", "5"]]],
];

async function api(path, options) {
  const res = await fetch(path, options);
  const data = res.headers.get("content-type")?.includes("application/json") ? await res.json() : await res.text();
  if (!res.ok || data.error) throw new Error(data.error || data);
  return data;
}

async function refresh() {
  saveFormDraft();
  pendingSummaryRefresh = false;
  config = await api("/api/config");
  const domains = await api("/api/domains");
  task = domains.task;
  renderDomains(domains.domains);
  if (selectedDomain) {
    summary = await api("/api/domain?domain=" + encodeURIComponent(selectedDomain));
    renderSummary();
  }
  await refreshLog();
}

async function pollTask() {
  try {
    const previousStatus = task?.status;
    const data = await api("/api/task");
    task = data.task;
    if (task?.log_path) await loadLog(task.log_path);
    if (previousStatus === "running" && task?.status !== "running") {
      pendingSummaryRefresh = true;
      refreshWhenIdle();
    }
  } catch (e) {
    document.getElementById("log").textContent = e.message;
  }
}

function renderDomains(domains) {
  const box = document.getElementById("domains");
  box.innerHTML = "";
  domains.forEach(state => {
    const btn = document.createElement("button");
    btn.className = "domain-item" + (state.domain === selectedDomain ? " active" : "");
    btn.textContent = state.domain;
    btn.onclick = () => { selectedDomain = state.domain; formDraft = {}; refresh(); };
    box.appendChild(btn);
  });
}

function renderSummary() {
  document.getElementById("title").textContent = selectedDomain;
  document.getElementById("pathLine").textContent = summary.paths.domain_dir;
  document.getElementById("seedCsv").textContent = summary.counts.seed_csv;
  document.getElementById("seedAuth").textContent = summary.counts.seed_auth;
  document.getElementById("inviteeCsv").textContent = summary.counts.invitee_csv;
  document.getElementById("inviteeAuth").textContent = summary.counts.invitee_auth;
  const steps = document.getElementById("steps");
  steps.innerHTML = "";
  stepDefs.forEach(def => steps.appendChild(stepCard(def)));
}

function stepCard(def) {
  const [step, title, fields, retryable] = def;
  const entry = summary.state.steps[step] || {};
  const status = entry.status || "pending";
  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML = `<div class="card-head"><div class="card-title">${title}</div><span class="badge ${status}">${status}</span></div>`;
  const form = document.createElement("div");
  form.className = "form-grid";
  fields.forEach(([name, label, fallback]) => {
    const div = document.createElement("div");
    if (name === "proxy_template" || name === "admin_password_file") div.className = "wide";
    const input = document.createElement("input");
    input.id = `${step}_${name}`;
    input.value = formDraft[draftKey(step, name)] ?? defaultValue(name, fallback);
    input.addEventListener("input", () => {
      formDraft[draftKey(step, name)] = input.value;
    });
    input.addEventListener("blur", refreshWhenIdle);
    div.innerHTML = `<label>${label}</label>`;
    div.appendChild(input);
    form.appendChild(div);
  });
  card.appendChild(form);
  const actions = document.createElement("div");
  actions.className = "actions";
  const run = document.createElement("button");
  run.className = "primary";
  run.textContent = "运行";
  run.disabled = status === "done" || status === "running" || task?.status === "running";
  run.onclick = () => runStep(step, fields);
  actions.appendChild(run);
  if (retryable) {
    const retry = document.createElement("button");
    retry.textContent = "补跑失败";
    retry.disabled = status !== "done" || task?.status === "running";
    retry.onclick = () => retryLogin(step, fields);
    actions.appendChild(retry);
  }
  if (entry.log_path) {
    const view = document.createElement("button");
    view.textContent = "看日志";
    view.onclick = () => loadLog(entry.log_path);
    actions.appendChild(view);
  }
  card.appendChild(actions);
  return card;
}

function defaultValue(name, fallback) {
  if (name === "proxy_template") return config.proxy_template || fallback;
  if (name === "keycloak_url") return config.keycloak_url || fallback;
  if (name === "realm") return config.realm || fallback;
  if (name === "admin_user") return config.admin_user || fallback;
  if (name === "admin_password_file") return config.admin_password_file || fallback;
  return fallback;
}

function draftKey(step, name) {
  return `${selectedDomain}:${step}:${name}`;
}

function saveFormDraft() {
  if (!selectedDomain) return;
  stepDefs.forEach(([step, title, fields]) => {
    fields.forEach(([name]) => {
      const input = document.getElementById(`${step}_${name}`);
      if (input) formDraft[draftKey(step, name)] = input.value;
    });
  });
}

function collect(step, fields) {
  const payload = { domain: selectedDomain, step };
  fields.forEach(([name]) => payload[name] = document.getElementById(`${step}_${name}`)?.value || "");
  return payload;
}

async function createDomain() {
  const domain = document.getElementById("newDomain").value;
  await api("/api/domain", { method: "POST", body: JSON.stringify({ domain }) });
  selectedDomain = domain.trim().toLowerCase();
  formDraft = {};
  await refresh();
}

async function runStep(step, fields) {
  await api("/api/run", { method: "POST", body: JSON.stringify(collect(step, fields)) });
  formDraft = {};
  await refresh();
}

async function retryLogin(step, fields) {
  await api("/api/retry-login", { method: "POST", body: JSON.stringify(collect(step, fields)) });
  formDraft = {};
  await refresh();
}

function isEditingForm() {
  const el = document.activeElement;
  return el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT");
}

function refreshWhenIdle() {
  if (!pendingSummaryRefresh || isEditingForm()) return;
  refresh().catch(e => document.getElementById("log").textContent = e.message);
}

async function refreshLog() {
  if (task?.log_path) await loadLog(task.log_path);
}

async function loadLog(path) {
  const text = await api("/api/log?path=" + encodeURIComponent(path));
  const box = document.getElementById("log");
  box.textContent = text || "暂无日志";
  box.scrollTop = box.scrollHeight;
}

setInterval(pollTask, 3000);
refresh().catch(e => document.getElementById("log").textContent = e.message);
</script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
