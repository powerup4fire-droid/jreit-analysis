"""EDINET API v2 から各REITの財務ファンダメンタルズ（含み損益・LTV・NOI 等）を取得。

設計（バッチ取込・cached-first を維持）:
  1) documents.json を日次で走査し、対象REIT(secCode)の最新の有価証券報告書/半期報告書のdocIDを特定
  2) documents/{docID}?type=5 で XBRL→CSV(zip) を取得・展開
  3) CSV(要素ID/項目名/値) から 総資産・有利子負債・鑑定評価額・帳簿価額・NOI を抽出
  4) 含み損益 = 鑑定評価額 - 帳簿価額 / LTV = 有利子負債 / 総資産 を算出

APIキー: 環境変数 EDINET_API_KEY（config.dividends.edinet_api_key_env で変更可）。
キー未設定なら全件 parse_status="no_key" で安全にスキップ（クラッシュしない）。

⚠ REIT固有項目（鑑定評価額・NOI）はタクソノミ/開示様式に依存するため、初回はラベル一致の
  ベストエフォート。実データ（キー設定後の取込結果）を見て LABELS の調整が必要な場合がある。
"""
from __future__ import annotations
import datetime as dt
import io
import zipfile

from loguru import logger

from ..http import HttpClient

API_BASE = "https://api.edinet-fsa.go.jp/api/v2"
# 対象書類種別: 120=有価証券報告書, 160=半期報告書（REITは資産運用報告も併用するが、まず法定開示の有報/半期）
DOC_TYPES = {"120", "160"}

# --- 抽出ターゲット（要素ID: 貸借対照表の標準タクソノミ jppfs_cor は信頼性高） ---
ASSETS_IDS = ["jppfs_cor:Assets"]
DEBT_IDS = [
    "jppfs_cor:ShortTermLoansPayable",
    "jppfs_cor:CurrentPortionOfLongTermLoansPayable",
    "jppfs_cor:LongTermLoansPayable",
    "jppfs_cor:CurrentPortionOfInvestmentCorporationBonds",
    "jppfs_cor:InvestmentCorporationBondsPayable",
    "jppfs_cor:InvestmentCorporationBonds",
]
# --- REIT固有（ベストエフォート: 項目名の部分一致）---
LABELS = {
    "appraisal_value": ["期末算定価額", "期末算定価額合計", "鑑定評価額", "当期末算定価額"],
    "book_value": ["不動産等の帳簿価額", "帳簿価額合計", "期末帳簿価額"],
    "unrealized_gain": ["含み損益", "評価損益", "差額"],
    "noi": ["NOI", "賃貸事業損益", "不動産賃貸事業損益", "営業純収益"],
}


def sec_code(code: str) -> str:
    """4桁証券コード -> EDINETの5桁secCode（末尾0）。"""
    return f"{int(code):04d}0"


def _to_float(s) -> float | None:
    if s is None:
        return None
    t = str(s).replace(",", "").replace("△", "-").replace("▲", "-").strip()
    if t in ("", "-", "—", "－", "NA"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


class EdinetClient:
    def __init__(self, client: HttpClient, api_key: str):
        self.c = client
        self.key = api_key

    def list_documents(self, date: dt.date) -> list[dict]:
        url = f"{API_BASE}/documents.json"
        try:
            r = self.c.get(url, params={"date": date.isoformat(), "type": "2",
                                        "Subscription-Key": self.key})
            return r.json().get("results", []) or []
        except Exception as e:  # noqa
            logger.warning(f"EDINET list {date}: {e}")
            return []

    def harvest_doc_ids(self, codes: list[str], days_back: int = 400) -> dict[str, dict]:
        """直近 days_back 日を1回ずつ走査し、対象REITの最新(有報/半期)docを返す。
        戻り値: {code: {"doc_id":..., "period_end":..., "doc_type":...}}"""
        want = {sec_code(c): c for c in codes}
        best: dict[str, dict] = {}
        today = dt.date.today()
        for i in range(days_back):
            day = today - dt.timedelta(days=i)
            for d in self.list_documents(day):
                sc = (d.get("secCode") or "").strip()
                if sc not in want or d.get("docTypeCode") not in DOC_TYPES:
                    continue
                code = want[sc]
                pend = d.get("periodEnd") or d.get("submitDateTime") or ""
                cur = best.get(code)
                if cur is None or pend > cur["period_end"]:
                    best[code] = {"doc_id": d.get("docID"), "period_end": pend,
                                  "doc_type": d.get("docTypeCode")}
            if len(best) == len(codes):
                break
        logger.info(f"EDINET harvest: {len(best)}/{len(codes)} docs found in {days_back}d")
        return best

    def fetch_csv_rows(self, doc_id: str) -> list[dict]:
        """documents/{docID}?type=5 の zip を取得し、主CSVを (要素ID,項目名,値…) の行dictで返す。"""
        url = f"{API_BASE}/documents/{doc_id}"
        try:
            r = self.c.get(url, params={"type": "5", "Subscription-Key": self.key})
        except Exception as e:  # noqa
            logger.warning(f"EDINET csv {doc_id}: {e}")
            return []
        rows: list[dict] = []
        try:
            zf = zipfile.ZipFile(io.BytesIO(r.content))
        except Exception as e:  # noqa
            logger.warning(f"EDINET zip {doc_id}: {e}")
            return []
        for name in zf.namelist():
            if not name.lower().endswith(".csv"):
                continue
            raw = zf.read(name)
            text = None
            for enc in ("utf-16", "utf-16-le", "cp932", "utf-8"):
                try:
                    text = raw.decode(enc)
                    break
                except Exception:  # noqa
                    continue
            if not text:
                continue
            lines = text.splitlines()
            if not lines:
                continue
            header = lines[0].split("\t")
            idx = {h.strip('"'): i for i, h in enumerate(header)}
            ei = idx.get("要素ID", 0)
            ni = idx.get("項目名", 1)
            vi = idx.get("値", len(header) - 1)
            yi = idx.get("相対年度")
            ci = idx.get("コンテキストID")
            for ln in lines[1:]:
                p = [c.strip('"') for c in ln.split("\t")]
                if len(p) <= max(ei, ni, vi):
                    continue
                rows.append({
                    "id": p[ei], "label": p[ni], "value": p[vi],
                    "year": p[yi] if (yi is not None and yi < len(p)) else "",
                    "ctx": p[ci] if (ci is not None and ci < len(p)) else "",
                })
        return rows


def _pick_value(rows, ids=None, labels=None, prefer_current=True):
    """要素ID完全一致 or 項目名部分一致で値を1つ返す。当期(年度=当期/contextに CurrentYear)を優先。"""
    cands = []
    for r in rows:
        hit = (ids and r["id"] in ids) or (labels and any(lb in r["label"] for lb in labels))
        if not hit:
            continue
        v = _to_float(r["value"])
        if v is None:
            continue
        cur = ("当期" in r["year"]) or ("CurrentYear" in r["ctx"]) or ("CurrentQuarter" in r["ctx"])
        cands.append((cur, v))
    if not cands:
        return None
    if prefer_current and any(c for c, _ in cands):
        cands = [(c, v) for c, v in cands if c]
    return cands[0][1]


def parse_fundamentals(rows: list[dict]) -> dict:
    """CSV行群 -> ファンダ項目dict（取れない項目は None）。"""
    out = {"appraisal_value": None, "book_value": None, "unrealized_gain": None,
           "unrealized_gain_pct": None, "total_assets": None,
           "interest_bearing_debt": None, "ltv_pct": None, "noi": None}
    if not rows:
        return out

    out["total_assets"] = _pick_value(rows, ids=ASSETS_IDS)
    debt = 0.0
    got_debt = False
    for did in DEBT_IDS:
        v = _pick_value(rows, ids=[did])
        if v is not None:
            debt += v
            got_debt = True
    out["interest_bearing_debt"] = debt if got_debt else None
    if out["total_assets"] and out["interest_bearing_debt"] is not None and out["total_assets"] > 0:
        out["ltv_pct"] = round(out["interest_bearing_debt"] / out["total_assets"] * 100, 2)

    out["appraisal_value"] = _pick_value(rows, labels=LABELS["appraisal_value"])
    out["book_value"] = _pick_value(rows, labels=LABELS["book_value"])
    out["noi"] = _pick_value(rows, labels=LABELS["noi"])
    ug = _pick_value(rows, labels=LABELS["unrealized_gain"])
    if out["appraisal_value"] is not None and out["book_value"] is not None:
        out["unrealized_gain"] = out["appraisal_value"] - out["book_value"]
    elif ug is not None:
        out["unrealized_gain"] = ug
    if out["unrealized_gain"] is not None and out["book_value"]:
        out["unrealized_gain_pct"] = round(out["unrealized_gain"] / out["book_value"] * 100, 2)
    return out


def fetch_fundamentals(codes: list[str], client: HttpClient, api_key: str | None,
                       days_back: int = 400):
    """各codeのファンダ dict をyield。キー未設定なら no_key で全件返す。"""
    if not api_key:
        for c in codes:
            yield {"code": c, "source": "edinet", "parse_status": "no_key"}
        return
    ec = EdinetClient(client, api_key)
    found = ec.harvest_doc_ids(codes, days_back=days_back)
    for c in codes:
        info = found.get(c)
        if not info:
            yield {"code": c, "source": "edinet", "parse_status": "not_found"}
            continue
        try:
            rows = ec.fetch_csv_rows(info["doc_id"])
            f = parse_fundamentals(rows)
            got = sum(1 for v in f.values() if v is not None)
            f.update({
                "code": c, "doc_id": info["doc_id"],
                "fiscal_period": info.get("period_end"), "source": "edinet",
                "parse_status": "ok" if got >= 4 else ("partial" if got else "not_found"),
            })
            logger.info(f"{c}: EDINET parsed ({got} fields) doc={info['doc_id']}")
            yield f
        except Exception as e:  # noqa
            logger.error(f"{c}: EDINET parse error: {e}")
            yield {"code": c, "doc_id": info.get("doc_id"), "source": "edinet",
                   "parse_status": "error"}
