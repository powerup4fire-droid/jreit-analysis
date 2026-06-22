"""SQLite: スキーマDDL・接続・UPSERT。"""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from .models import Reit, StockMetrics, Dividend, ASSET_KEYS

DDL = """
CREATE TABLE IF NOT EXISTS reits (
  code TEXT PRIMARY KEY, name TEXT, reit_type TEXT,
  yield_total REAL, yield_ex_excess REAL, num_properties INTEGER,
  asset_office REAL, asset_residential REAL, asset_logistics REAL,
  asset_retail REAL, asset_hotel REAL, asset_healthcare REAL, asset_other REAL,
  asset_estimated INTEGER DEFAULT 0, source_url TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS stock_metrics (
  code TEXT PRIMARY KEY REFERENCES reits(code),
  price_asof TEXT, latest_price REAL, volume INTEGER, market_cap REAL, nav_ratio REAL,
  dev_mean_6y_pct REAL, dev_median_6y_pct REAL,
  ma_25d REAL, ma_75d REAL, ma_200d REAL, ma_75w REAL, ma_200w REAL, ma_75m REAL, ma_200m REAL,
  lehman_min REAL, lehman_ratio_pct REAL, price_source TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS price_history (
  code TEXT, date TEXT, close REAL, volume INTEGER, PRIMARY KEY (code, date)
);
CREATE TABLE IF NOT EXISTS dividends (
  code TEXT REFERENCES reits(code), period_label TEXT, report_date TEXT,
  total_distribution REAL, excess_distribution REAL, excess_present INTEGER,
  excess_ratio_pct REAL, pdf_url TEXT, pdf_sha256 TEXT, parse_status TEXT,
  PRIMARY KEY (code, period_label)
);
CREATE TABLE IF NOT EXISTS scrape_runs (
  run_id TEXT PRIMARY KEY, started_at TEXT, finished_at TEXT,
  codes_attempted INTEGER, codes_succeeded INTEGER, status TEXT, notes TEXT
);
CREATE TABLE IF NOT EXISTS field_errors (
  run_id TEXT, code TEXT, field TEXT, error TEXT, ts TEXT
);
CREATE TABLE IF NOT EXISTS fundamentals (
  code TEXT PRIMARY KEY REFERENCES reits(code),
  fiscal_period TEXT,            -- 会計期間末 (YYYY-MM-DD)
  appraisal_value REAL,          -- 期末算定（鑑定評価）価額合計 円
  book_value REAL,               -- 帳簿価額合計 円
  unrealized_gain REAL,          -- 含み損益 = appraisal - book 円
  unrealized_gain_pct REAL,      -- 含み益率 = unrealized_gain / book * 100
  total_assets REAL,             -- 総資産 円
  interest_bearing_debt REAL,    -- 有利子負債 円
  ltv_pct REAL,                  -- LTV = interest_bearing_debt / total_assets * 100
  noi REAL,                      -- 不動産賃貸事業 NOI 円（取得できれば）
  noi_yield_pct REAL,            -- NOI利回り %（japan-reit ランキング）
  doc_id TEXT,                   -- EDINET docID
  source TEXT,                   -- "edinet" | "japan-reit"
  parse_status TEXT,             -- ok / partial / no_key / not_found / error
  updated_at TEXT
);
"""

MA_KEYS = ["25d", "75d", "200d", "75w", "200w", "75m", "200m"]
FUND_COLS = ["fiscal_period", "appraisal_value", "book_value", "unrealized_gain",
             "unrealized_gain_pct", "total_assets", "interest_bearing_debt", "ltv_pct",
             "noi", "doc_id", "source", "parse_status"]


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA journal_mode=WAL;")
    con.executescript(DDL)
    # 既存DBへの後付けカラム（無ければ追加）
    have = {r[1] for r in con.execute("PRAGMA table_info(fundamentals)")}
    for c, t in [("noi_yield_pct", "REAL")]:
        if c not in have:
            con.execute(f"ALTER TABLE fundamentals ADD COLUMN {c} {t}")
    return con


def upsert_ranking(con, code: str, metrics: dict, now: str):
    """japan-reit ランキング由来の率指標(含み益率/NOI利回り/LTV)を fundamentals へ。"""
    con.execute(
        """INSERT INTO fundamentals (code,unrealized_gain_pct,noi_yield_pct,ltv_pct,source,parse_status,updated_at)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(code) DO UPDATE SET
           unrealized_gain_pct=excluded.unrealized_gain_pct,
           noi_yield_pct=excluded.noi_yield_pct, ltv_pct=excluded.ltv_pct,
           source=excluded.source, parse_status=excluded.parse_status, updated_at=excluded.updated_at""",
        (code, metrics.get("unrealized_gain_pct"), metrics.get("noi_yield_pct"),
         metrics.get("ltv_pct"), "japan-reit", "ok", now),
    )


def upsert_reit(con, r: Reit, now: str):
    con.execute(
        """INSERT INTO reits (code,name,reit_type,yield_total,yield_ex_excess,num_properties,
           asset_office,asset_residential,asset_logistics,asset_retail,asset_hotel,asset_healthcare,asset_other,
           asset_estimated,source_url,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(code) DO UPDATE SET
           name=excluded.name, reit_type=excluded.reit_type, yield_total=excluded.yield_total,
           yield_ex_excess=excluded.yield_ex_excess, num_properties=excluded.num_properties,
           asset_office=excluded.asset_office, asset_residential=excluded.asset_residential,
           asset_logistics=excluded.asset_logistics, asset_retail=excluded.asset_retail,
           asset_hotel=excluded.asset_hotel, asset_healthcare=excluded.asset_healthcare,
           asset_other=excluded.asset_other, asset_estimated=excluded.asset_estimated,
           source_url=excluded.source_url, updated_at=excluded.updated_at""",
        (r.code, r.name, r.reit_type, r.yield_total, r.yield_ex_excess, r.num_properties,
         *[r.asset.get(k) for k in ASSET_KEYS], int(r.asset_estimated), r.source_url, now),
    )


def upsert_metrics(con, m: StockMetrics, now: str):
    con.execute(
        """INSERT INTO stock_metrics (code,price_asof,latest_price,volume,market_cap,nav_ratio,
           dev_mean_6y_pct,dev_median_6y_pct,ma_25d,ma_75d,ma_200d,ma_75w,ma_200w,ma_75m,ma_200m,
           lehman_min,lehman_ratio_pct,price_source,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(code) DO UPDATE SET
           price_asof=excluded.price_asof, latest_price=excluded.latest_price, volume=excluded.volume,
           market_cap=excluded.market_cap, nav_ratio=excluded.nav_ratio,
           dev_mean_6y_pct=excluded.dev_mean_6y_pct, dev_median_6y_pct=excluded.dev_median_6y_pct,
           ma_25d=excluded.ma_25d, ma_75d=excluded.ma_75d, ma_200d=excluded.ma_200d,
           ma_75w=excluded.ma_75w, ma_200w=excluded.ma_200w, ma_75m=excluded.ma_75m, ma_200m=excluded.ma_200m,
           lehman_min=excluded.lehman_min, lehman_ratio_pct=excluded.lehman_ratio_pct,
           price_source=excluded.price_source, updated_at=excluded.updated_at""",
        (m.code, m.price_asof, m.latest_price, m.volume, m.market_cap, m.nav_ratio,
         m.dev_mean_6y_pct, m.dev_median_6y_pct, *[m.ma.get(k) for k in MA_KEYS],
         m.lehman_min, m.lehman_ratio_pct, m.price_source, now),
    )


def upsert_dividend(con, d: Dividend):
    con.execute(
        """INSERT INTO dividends (code,period_label,report_date,total_distribution,excess_distribution,
           excess_present,excess_ratio_pct,pdf_url,pdf_sha256,parse_status)
           VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(code,period_label) DO UPDATE SET
           report_date=excluded.report_date, total_distribution=excluded.total_distribution,
           excess_distribution=excluded.excess_distribution, excess_present=excluded.excess_present,
           excess_ratio_pct=excluded.excess_ratio_pct, pdf_url=excluded.pdf_url,
           pdf_sha256=excluded.pdf_sha256, parse_status=excluded.parse_status""",
        (d.code, d.period_label, d.report_date, d.total_distribution, d.excess_distribution,
         int(d.excess_present), d.excess_ratio_pct, d.pdf_url, d.pdf_sha256, d.parse_status),
    )


def upsert_fundamentals(con, f: dict, now: str):
    """f は FUND_COLS のキーを持つ dict（欠損は None 可）。code 必須。"""
    cols = ["code"] + FUND_COLS + ["updated_at"]
    vals = [f.get("code")] + [f.get(k) for k in FUND_COLS] + [now]
    sets = ", ".join(f"{c}=excluded.{c}" for c in FUND_COLS + ["updated_at"])
    con.execute(
        f"INSERT INTO fundamentals ({','.join(cols)}) VALUES ({','.join('?' * len(cols))}) "
        f"ON CONFLICT(code) DO UPDATE SET {sets}",
        vals,
    )


def save_price_history(con, code: str, df):
    rows = [(code, idx.strftime("%Y-%m-%d"), float(r.close) if r.close == r.close else None,
             int(r.volume) if r.volume == r.volume else None)
            for idx, r in df.iterrows()]
    con.executemany(
        "INSERT OR REPLACE INTO price_history (code,date,close,volume) VALUES (?,?,?,?)", rows)


def log_field_error(con, run_id, code, field_name, err, ts):
    con.execute("INSERT INTO field_errors (run_id,code,field,error,ts) VALUES (?,?,?,?,?)",
                (run_id, code, field_name, str(err)[:500], ts))


def start_run(con, run_id, started_at, attempted):
    con.execute("INSERT OR REPLACE INTO scrape_runs (run_id,started_at,codes_attempted,status) VALUES (?,?,?,?)",
                (run_id, started_at, attempted, "running"))


def finish_run(con, run_id, finished_at, succeeded, status, notes=""):
    con.execute("UPDATE scrape_runs SET finished_at=?, codes_succeeded=?, status=?, notes=? WHERE run_id=?",
                (finished_at, succeeded, status, notes, run_id))
