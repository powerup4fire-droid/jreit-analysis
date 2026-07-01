"""招待制アクセスの許可リスト管理（Cloudflare KV・cloud_store と同じ資格情報を利用）。

仕組み:
  - 招待リンク `https://<app>/?invite=<code>` を踏んだログインユーザーの email を
    KV の許可リスト(1キーのJSON dict)へ自動登録する（改良1+2方式）。
  - 以降そのユーザーはコード不要で入場できる。コードをローテーションしても
    登録済みユーザーには影響しない。
  - 管理者(admin_email)はリストの閲覧・取り消しができる（app.py の招待管理UI）。

必要な secrets:
  [cloudflare]  … cloud_store と共通（account_id / kv_namespace_id / api_token）
  [invite]
  code = "招待コード（URLに載せる合言葉）"
  admin_email = "powerup4fire@gmail.com"
"""
from __future__ import annotations
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone

import streamlit as st

_TIMEOUT = 10
_KV_KEY = "invite_allowlist_v1"


def _cfg() -> tuple[str | None, str | None, str | None]:
    try:
        s = st.secrets["cloudflare"]
        return s.get("account_id"), s.get("kv_namespace_id"), s.get("api_token")
    except Exception:
        return None, None, None


def _invite_secrets() -> dict:
    try:
        return dict(st.secrets["invite"])
    except Exception:
        return {}


def invite_code() -> str | None:
    """招待コード。未設定なら None（＝招待ゲート無効）。"""
    return _invite_secrets().get("code") or None


def admin_email() -> str | None:
    return _invite_secrets().get("admin_email") or None


def enabled() -> bool:
    """招待ゲートを有効化できる状態か（KV接続 + コード設定済み）。"""
    return all(_cfg()) and bool(invite_code())


def _url() -> str:
    acc, ns, _ = _cfg()
    return (f"https://api.cloudflare.com/client/v4/accounts/{acc}"
            f"/storage/kv/namespaces/{ns}/values/{_KV_KEY}")


def _load_raw() -> dict:
    """KVから許可リスト {email: {added: iso}} を取得。未作成なら {}。"""
    _, _, tok = _cfg()
    req = urllib.request.Request(_url(), headers={"Authorization": f"Bearer {tok}"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
            return data if isinstance(data, dict) else {}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {}
        raise


def _save_raw(d: dict) -> None:
    _, _, tok = _cfg()
    body = json.dumps(d, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        _url(), data=body, method="PUT",
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "text/plain"})
    urllib.request.urlopen(req, timeout=_TIMEOUT).read()


@st.cache_data(ttl=60, show_spinner=False)
def _cached_allowlist() -> dict:
    """許可リスト（60秒キャッシュ）。追加・削除時は clear() で即時反映。"""
    try:
        return _load_raw()
    except Exception:
        return {}


def allowlist() -> dict:
    return _cached_allowlist()


def is_allowed(email: str) -> bool:
    if not email:
        return False
    if admin_email() and email.lower() == admin_email().lower():
        return True
    return email.lower() in {k.lower() for k in allowlist()}


def add(email: str) -> None:
    """許可リストへ登録（冪等）。"""
    d = _load_raw()
    if email.lower() not in {k.lower() for k in d}:
        d[email] = {"added": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        _save_raw(d)
    _cached_allowlist.clear()


def remove(email: str) -> None:
    """許可リストから取り消し。"""
    d = _load_raw()
    for k in list(d):
        if k.lower() == email.lower():
            del d[k]
    _save_raw(d)
    _cached_allowlist.clear()
