"""japan-reit.com のランキング表(/ranking/all)から全銘柄の指標を取得。
含み損益率・NOI利回り・有利子負債比率(LTV) を EDINETキー無しで取得できる。"""
from __future__ import annotations
import io
import re

import pandas as pd
from loguru import logger

from ..http import HttpClient

RANKING_URL = "https://www.japan-reit.com/ranking/all"


def _pct(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).replace("%", "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def fetch_ranking_metrics(client: HttpClient) -> dict[str, dict]:
    """{code: {"unrealized_gain_pct","noi_yield_pct","ltv_pct"}} を返す。失敗時は空。"""
    try:
        resp = client.get(RANKING_URL)
        tables = pd.read_html(io.StringIO(resp.text))
    except Exception as e:  # noqa
        logger.error(f"ranking fetch/parse failed: {e}")
        return {}
    # 含み損益率・NOI利回り・有利子負債比率 を含むテーブルを採用
    tbl = None
    for t in tables:
        cols = [str(c) for c in t.columns]
        if any("含み損益率" in c for c in cols) and any("有利子負債比率" in c for c in cols):
            tbl = t
            break
    if tbl is None:
        logger.error("ranking: target table not found")
        return {}

    def col(name):
        for c in tbl.columns:
            if name in str(c):
                return c
        return None

    c_code = col("証券コード") or col("コード")
    c_ug, c_noi, c_ltv = col("含み損益率"), col("NOI利回り"), col("有利子負債比率")
    out: dict[str, dict] = {}
    for _, r in tbl.iterrows():
        m = re.search(r"(\d{4})", str(r.get(c_code, "")))
        if not m:
            continue
        out[m.group(1)] = {
            "unrealized_gain_pct": _pct(r.get(c_ug)),
            "noi_yield_pct": _pct(r.get(c_noi)),
            "ltv_pct": _pct(r.get(c_ltv)),
        }
    logger.info(f"ranking: parsed {len(out)} codes")
    return out
