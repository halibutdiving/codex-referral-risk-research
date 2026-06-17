#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Import successfully invited emails into a Keycloak realm and write a login CSV.

Example:
  python keycloak_import_invitees.py \
    --invite-results runs/example/invite_results.json \
    --keycloak-url https://keycloak.gatekeeper1998.xyz \
    --realm openai-lab \
    --admin-user admin \
    --admin-password admin \
    --user-password 'YourPassword' \
    --out-csv runs/example/invitee_accounts.csv
"""

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

import requests

sys.stdout.reconfigure(encoding="utf-8")


def log(msg: str, symbol: str = "*") -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] [{symbol}] {msg}", flush=True)


def normalize_email(value: Any) -> str:
    return str(value or "").strip().lower()


def extract_invited_emails(results: Any) -> list[str]:
    """Extract unique emails from service-confirmed invites[].email entries."""
    if not isinstance(results, list):
        raise ValueError("邀请结果 JSON 顶层必须是数组")

    emails: list[str] = []
    seen: set[str] = set()
    for account_result in results:
        if not isinstance(account_result, dict):
            continue
        invites = account_result.get("invites", [])
        if not isinstance(invites, list):
            continue
        for invite in invites:
            if not isinstance(invite, dict):
                continue
            email = normalize_email(invite.get("email"))
            if not email or "@" not in email or email in seen:
                continue
            seen.add(email)
            emails.append(email)
    return emails


def build_user_payload(email: str, username_mode: str = "email") -> dict[str, Any]:
    normalized = normalize_email(email)
    if username_mode == "localpart":
        username = normalized.split("@", 1)[0]
    else:
        username = normalized
    return {
        "username": username,
        "email": normalized,
        "enabled": True,
        "emailVerified": False,
    }


def write_login_csv(path: Path, emails: Iterable[str], password: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        for email in emails:
            writer.writerow([email, password])


class KeycloakAdmin:
    def __init__(
        self,
        base_url: str,
        realm: str,
        admin_user: str,
        admin_password: str,
        admin_realm: str = "master",
        verify_tls: bool = True,
    ):
        self.base_url = base_url.rstrip("/")
        self.realm = realm
        self.admin_user = admin_user
        self.admin_password = admin_password
        self.admin_realm = admin_realm
        self.verify_tls = verify_tls
        self.session = requests.Session()
        self.session.trust_env = False

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", 30)
        kwargs.setdefault("verify", self.verify_tls)
        resp = self.session.request(method, url, **kwargs)
        if resp.status_code == 401:
            self.authenticate()
            resp = self.session.request(method, url, **kwargs)
        return resp

    def authenticate(self) -> None:
        token_url = f"{self.base_url}/realms/{quote(self.admin_realm)}/protocol/openid-connect/token"
        resp = self.session.post(
            token_url,
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": self.admin_user,
                "password": self.admin_password,
            },
            timeout=30,
            verify=self.verify_tls,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Keycloak admin 登录失败: HTTP {resp.status_code} {resp.text[:300]}")
        token = resp.json().get("access_token")
        if not token:
            raise RuntimeError("Keycloak admin 登录响应缺少 access_token")
        self.session.headers.update({"Authorization": f"Bearer {token}"})

    def find_user_id_by_username(self, username: str) -> str:
        users_url = f"{self.base_url}/admin/realms/{quote(self.realm)}/users"
        resp = self._request(
            "GET",
            users_url,
            params={"username": username, "exact": "true"},
        )
        if resp.status_code != 200:
            raise RuntimeError(f"查询 Keycloak 用户失败: HTTP {resp.status_code} {resp.text[:300]}")
        for user in resp.json():
            if user.get("username") == username:
                return str(user.get("id") or "")
        return ""

    def create_user(self, payload: dict[str, Any]) -> tuple[str, bool]:
        users_url = f"{self.base_url}/admin/realms/{quote(self.realm)}/users"
        resp = self._request(
            "POST",
            users_url,
            json=payload,
        )
        username = str(payload["username"])
        if resp.status_code == 201:
            location = resp.headers.get("Location", "")
            user_id = location.rstrip("/").rsplit("/", 1)[-1] if location else ""
            return user_id, True
        if resp.status_code == 409:
            user_id = self.find_user_id_by_username(username)
            if user_id:
                return user_id, False
            raise RuntimeError(f"用户已存在但无法查到 ID: {username}")
        raise RuntimeError(f"创建 Keycloak 用户失败 {username}: HTTP {resp.status_code} {resp.text[:300]}")

    def set_password(self, user_id: str, password: str, temporary: bool = False) -> None:
        if not user_id:
            raise RuntimeError("设置密码失败: user_id 为空")
        reset_url = (
            f"{self.base_url}/admin/realms/{quote(self.realm)}/users/"
            f"{quote(user_id)}/reset-password"
        )
        resp = self._request(
            "PUT",
            reset_url,
            json={"type": "password", "value": password, "temporary": temporary},
        )
        if resp.status_code not in (204, 200):
            raise RuntimeError(f"设置 Keycloak 密码失败: HTTP {resp.status_code} {resp.text[:300]}")


def load_invite_results(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    parser = argparse.ArgumentParser(description="将 OpenAI 邀请结果中的邮箱批量导入 Keycloak")
    parser.add_argument("--invite-results", required=True, help="codex_invitation_batch.py 输出的 invite_results.json")
    parser.add_argument("--keycloak-url", required=True, help="Keycloak 根 URL，例如 https://keycloak.example.com")
    parser.add_argument("--realm", required=True, help="目标 realm，例如 openai-lab")
    parser.add_argument("--admin-realm", default="master", help="管理员所在 realm [默认: master]")
    parser.add_argument("--admin-user", required=True, help="Keycloak 管理员用户名")
    parser.add_argument("--admin-password", required=True, help="Keycloak 管理员密码")
    parser.add_argument("--user-password", required=True, help="给导入用户设置的统一密码")
    parser.add_argument("--out-csv", required=True, help="输出给 codex_protocol_login.py 使用的 email,password CSV")
    parser.add_argument(
        "--username-mode",
        choices=["email", "localpart"],
        default="email",
        help="Keycloak username 使用完整邮箱或 @ 前缀 [默认: email]",
    )
    parser.add_argument("--temporary-password", action="store_true", help="将密码标记为临时密码")
    parser.add_argument("--dry-run", action="store_true", help="只提取邮箱和写 CSV，不调用 Keycloak")
    parser.add_argument("--insecure", action="store_true", help="关闭 TLS 证书校验")
    args = parser.parse_args()

    if args.insecure:
        try:
            import urllib3

            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except Exception:
            pass

    invite_results_path = Path(args.invite_results)
    if not invite_results_path.exists():
        print(f"[!] 邀请结果文件不存在: {invite_results_path}")
        return 1

    try:
        emails = extract_invited_emails(load_invite_results(invite_results_path))
    except Exception as e:
        print(f"[!] 解析邀请结果失败: {e}")
        return 1

    if not emails:
        print("[!] 未提取到任何 invites[].email")
        return 1

    log(f"提取到 {len(emails)} 个成功邀请邮箱", "✓")

    imported = 0
    existing = 0
    failed = 0
    if not args.dry_run:
        client = KeycloakAdmin(
            base_url=args.keycloak_url,
            realm=args.realm,
            admin_user=args.admin_user,
            admin_password=args.admin_password,
            admin_realm=args.admin_realm,
            verify_tls=not args.insecure,
        )
        try:
            client.authenticate()
        except Exception as e:
            print(f"[!] {e}")
            return 1

        for idx, email in enumerate(emails, 1):
            payload = build_user_payload(email, args.username_mode)
            try:
                user_id, created = client.create_user(payload)
                client.set_password(user_id, args.user_password, args.temporary_password)
                if created:
                    imported += 1
                    log(f"[{idx}/{len(emails)}] 创建用户: {email}", "✓")
                else:
                    existing += 1
                    log(f"[{idx}/{len(emails)}] 用户已存在，已重置密码: {email}", "!")
            except Exception as e:
                failed += 1
                log(f"[{idx}/{len(emails)}] 导入失败 {email}: {e}", "!")

    write_login_csv(Path(args.out_csv), emails, args.user_password)
    log(f"登录 CSV 已写入: {args.out_csv}", "✓")

    if args.dry_run:
        log("dry-run 模式未调用 Keycloak")
        return 0
    log(f"Keycloak 导入汇总: created={imported}, existing={existing}, failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[!] 用户取消。")
        sys.exit(130)
