#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import base64
import hashlib
import json
import string
from pathlib import Path
from typing import Any


def proxy_sid_for_email(email: str, length: int = 8) -> str:
    alphabet = string.ascii_letters + string.digits
    digest = hashlib.sha256(str(email or "").strip().lower().encode("utf-8")).digest()
    value = int.from_bytes(digest, "big")
    chars = []
    for _ in range(max(1, length)):
        value, idx = divmod(value, len(alphabet))
        chars.append(alphabet[idx])
    return "".join(chars)


def proxy_for_email(email: str, proxy_template: str = "", sid_len: int = 8) -> str:
    if not proxy_template:
        return ""
    sid = proxy_sid_for_email(email, sid_len)
    return proxy_template.replace("{sid}", sid)


def jwt_decode(token: str) -> dict[str, Any]:
    parts = str(token or "").split(".")
    if len(parts) < 2:
        return {}
    payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload_b64).decode("utf-8", errors="ignore"))
    except Exception:
        return {}


def extract_email_from_auth_data(auth_data: dict[str, Any]) -> str:
    tokens = auth_data.get("tokens", {})
    if not isinstance(tokens, dict):
        return ""
    for token_name in ("access_token", "id_token"):
        jwt_p = jwt_decode(tokens.get(token_name, ""))
        profile = jwt_p.get("https://api.openai.com/profile", {})
        if isinstance(profile, dict) and profile.get("email"):
            return str(profile["email"]).strip().lower()
        if jwt_p.get("email"):
            return str(jwt_p["email"]).strip().lower()
    return ""


def email_from_auth_filename(path: Path) -> str:
    stem = path.stem
    if "__at__" in stem:
        return stem.replace("__at__", "@").strip().lower()
    return stem.strip().lower()


def extract_email_from_auth_file(path: Path) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    if isinstance(data, dict):
        email = extract_email_from_auth_data(data)
        if email:
            return email
    return email_from_auth_filename(path)


def proxy_for_auth_file(path: Path, proxy_template: str = "", sid_len: int = 8) -> str:
    if not proxy_template:
        return ""
    return proxy_for_email(extract_email_from_auth_file(path), proxy_template, sid_len)
