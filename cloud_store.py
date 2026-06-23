"""Cloudflare KV による、ログインユーザー単位のポートフォリオ永続化。

仕組み:
  - Streamlit Community Cloud で private アプリにすると、閲覧者の Google ログイン情報が
    st.user に入る（st.user.email）。そのメールをハッシュ化したものを KV のキーにする。
  - これにより「自分専用」かつ「端末をまたいで同じデータ」を実現（iPhoneで入れて Mac で見る）。
  - secrets 未設定 / 未ログイン（ローカル開発）の場合は無効化され、従来どおりセッション内のみ。

必要な secrets（.streamlit/secrets.toml もしくは Streamlit Cloud の Secrets）:
  [cloudflare]
  account_id = "..."
  kv_namespace_id = "..."
  api_token = "..."   # Workers KV Storage:Edit 権限のトークン
"""
from __future__ import annotations
import hashlib
import json
import urllib.error
import urllib.request

import streamlit as st

_TIMEOUT = 10


def _cfg() -> tuple[str | None, str | None, str | None]:
    try:
        s = st.secrets["cloudflare"]
        return s.get("account_id"), s.get("kv_namespace_id"), s.get("api_token")
    except Exception:
        return None, None, None


def enabled() -> bool:
    """KV の接続情報が揃っているか。"""
    return all(_cfg())


def _viewer_email() -> str | None:
    """ログイン中の閲覧者メール（Community Cloud の private アプリで自動付与）。"""
    u = getattr(st, "user", None)
    if u is None:
        return None
    for getter in (lambda: u.get("email"), lambda: u.email, lambda: u["email"]):
        try:
            v = getter()
            if v:
                return v
        except Exception:
            continue
    return None


def current_user() -> str | None:
    """保存キーに使う識別子。

    1. 正規: ログイン中の閲覧者メール（複数人を招待しても各自専用になる）
    2. 保険: secrets で solo_mode=true の時は固定キー "solo" を返す。
       （private・自分だけで使う運用向け。オーナーがダッシュボード経由で開くと
        st.user が空になることがあるため、それでも端末間同期を効かせる）
    None を返すと呼び出し側が KV を使わない（＝セッションのみ）。
    """
    email = _viewer_email()
    if email:
        return email
    try:
        if bool(st.secrets["cloudflare"].get("solo_mode")):
            return "solo"
    except Exception:
        pass
    return None


def _key() -> str:
    email = current_user() or "local-dev"
    return "pf_" + hashlib.sha256(email.encode("utf-8")).hexdigest()[:32]


def _url() -> str | None:
    acc, ns, _ = _cfg()
    if not (acc and ns):
        return None
    return (f"https://api.cloudflare.com/client/v4/accounts/{acc}"
            f"/storage/kv/namespaces/{ns}/values/{_key()}")


def load() -> list[dict] | None:
    """保存済みポートフォリオ(list[dict])を返す。未設定なら None、保存なしなら []。"""
    if not enabled():
        return None
    _, _, tok = _cfg()
    req = urllib.request.Request(_url(), headers={"Authorization": f"Bearer {tok}"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []  # まだ保存なし
        raise


def save(rows: list[dict]) -> None:
    """ポートフォリオ(list[dict])を現在ユーザーのキーで保存。"""
    if not enabled():
        return
    _, _, tok = _cfg()
    data = json.dumps(rows, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        _url(), data=data, method="PUT",
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "text/plain"})
    urllib.request.urlopen(req, timeout=_TIMEOUT).read()
