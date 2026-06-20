"""japan-reit.com 個別ページ(/meigara/{code}/)から基礎情報を抽出。
HTML構造の変化に強いよう、テキスト+正規表現ベースで抽出し、欠損は None。"""
from __future__ import annotations
import json
import re
from bs4 import BeautifulSoup
from loguru import logger
from ..models import Reit, ASSET_KEYS
from ..http import HttpClient

# portfolio.json g1 のキー → asset キー（japan-reit.com /js/portfolio.js の s_purpose 準拠）
G1_MAP = {"1": "office", "2": "residential", "3": "retail",
          "4": "hotel", "5": "logistics", "6": "other"}

# 用途タイプ → asset キー
TYPE_MAP = [
    ("オフィス", "office"), ("住宅", "residential"), ("レジデン", "residential"),
    ("物流", "logistics"), ("商業", "retail"), ("リテール", "retail"),
    ("ホテル", "hotel"), ("ヘルスケア", "healthcare"), ("健康", "healthcare"),
]


def _num(pattern: str, text: str) -> float | None:
    m = re.search(pattern, text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


_SITEMAP = "https://www.japan-reit.com/sitemap.xml"
_CODE_RE = re.compile(r"meigara/(\d{4})")


def fetch_all_codes(client: HttpClient) -> list[str]:
    """sitemap.xml から全 /meigara/{code} を抽出（上場廃止含む可能性あり）。"""
    try:
        resp = client.get(_SITEMAP)
    except Exception as e:  # noqa
        logger.error(f"sitemap fetch failed: {e}")
        return []
    codes = sorted(set(_CODE_RE.findall(resp.text)))
    logger.info(f"sitemap: {len(codes)} REIT codes")
    return codes


def scrape_reit(client: HttpClient, base_url: str, code: str) -> Reit:
    url = f"{base_url}{code}/"
    r = Reit(code=code, source_url=url)
    try:
        resp = client.get(url)
    except Exception as e:  # noqa
        logger.warning(f"{code}: japan-reit fetch failed: {e}")
        return r

    soup = BeautifulSoup(resp.text, "lxml")
    text = re.sub(r"\s+", " ", soup.get_text(" "))

    # 銘柄名
    title = (soup.title.string if soup.title else "") or ""
    m = re.search(r"([^\s（(]+投資法人)", title) or re.search(r"([^\s（(]+投資法人)", text)
    r.name = m.group(1) if m else None

    # 分配金利回り（利益超過分配を除いた表記があれば別取り）
    r.yield_total = _num(r"利回り[^0-9]{0,12}(\d+\.\d+)\s*%", text)
    if r.yield_total is None:
        r.yield_total = _num(r"(\d+\.\d+)\s*%", text)  # 緩い保険
    r.yield_ex_excess = _num(r"利益超過分配[^%]{0,20}除[^0-9]{0,8}(\d+\.\d+)\s*%", text)

    # 物件数
    np_ = _num(r"物件数[^0-9]{0,8}(\d+)", text) or _num(r"(\d+)\s*棟", text) or _num(r"(\d+)\s*物件", text)
    r.num_properties = int(np_) if np_ is not None else None

    # 用途タイプ
    r.reit_type = _detect_type(text)
    # 用途別取得額比率（portfolio.json から実数値。失敗時はタイプ推定にフォールバック）
    if not _fill_asset_from_json(client, base_url, r):
        _fill_asset(r)
    logger.info(f"{code}: name={r.name} yield={r.yield_total} props={r.num_properties} "
                f"type={r.reit_type} asset_estimated={r.asset_estimated}")
    return r


def _fill_asset_from_json(client: HttpClient, base_url: str, r: Reit) -> bool:
    """/meigara/{code}/portfolio.json の g1（用途別取得額）を比率(%)に変換して格納。成功でTrue。"""
    url = f"{base_url}{r.code}/portfolio.json"
    try:
        resp = client.get(url)
        g1 = json.loads(resp.text).get("g1") or {}
    except Exception as e:  # noqa
        logger.warning(f"{r.code}: portfolio.json failed: {e}")
        return False
    vals = {G1_MAP[k]: float(v) for k, v in g1.items() if k in G1_MAP and v is not None}
    total = sum(vals.values())
    if total <= 0:
        return False
    r.asset = {k: round(vals.get(k, 0.0) / total * 100.0, 2) for k in ASSET_KEYS}
    r.asset_estimated = False
    return True


def _detect_type(text: str) -> str | None:
    if "総合型" in text or "総合" in text:
        return "総合"
    if "複合型" in text or "複合" in text:
        return "複合"
    for kw, key in TYPE_MAP:
        if f"{kw}特化" in text or f"{kw}主体" in text:
            return f"{key}特化"
    # 特化表記なしでも単一用途キーワードが支配的なら推定
    for kw, key in TYPE_MAP:
        if kw in text:
            return key
    return None


def _fill_asset(r: Reit):
    """特化型→当該100%。総合/複合・不明→ other=100 として推定フラグを立てる（spec: 不明分はother）。"""
    t = r.reit_type or ""
    for kw, key in TYPE_MAP:
        if t.startswith(key):
            r.asset = {k: (100.0 if k == key else 0.0) for k in ASSET_KEYS}
            r.asset_estimated = False
            return
    # 総合/複合/不明: 内訳の数値が取れていないため other に寄せて推定
    r.asset = {k: None for k in ASSET_KEYS}
    r.asset["other"] = 100.0
    r.asset_estimated = True
