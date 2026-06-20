"""ETLオーケストレーション。フィールド単位で例外を握り、NULLで継続（クラッシュさせない）。"""
from __future__ import annotations
import datetime as dt
import os
import uuid
from .config import Config
from .logging_conf import setup_logging
from .http import HttpClient
from . import db
from .models import StockMetrics
from .scrapers.reit_list import scrape_reit
from .scrapers.dividends_pdf import analyze_dividends
from .stock.prices import get_prices, fetch_info
from .stock.metrics import compute_metrics


def _now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def run(codes: list[str], cfg: Config) -> dict:
    log = setup_logging(cfg.path("log"))
    con = db.connect(cfg.path("db"))
    client = HttpClient(cfg.net.get("user_agent", "jreit/0.1"),
                        cfg.net.get("max_retries", 3),
                        cfg.net.get("min_sleep_seconds", 2),
                        cfg.net.get("timeout_seconds", 30))
    run_id = uuid.uuid4().hex[:12]
    started = _now()
    db.start_run(con, run_id, started, len(codes)); con.commit()
    log.bind(run=run_id).info(f"START run codes={codes}")

    succeeded = 0
    for code in codes:
        clog = log.bind(run=run_id, code=code)
        try:
            # 1) japan-reit.com
            try:
                reit = scrape_reit(client, cfg.sources["japan_reit_base"], code)
                if not reit.name:   # 上場廃止等の汎用ページ＝無効。DBに入れずスキップ。
                    clog.info("invalid/delisted page (no name) -> skip")
                    continue
                db.upsert_reit(con, reit, _now())
            except Exception as e:  # noqa
                clog.error(f"reit scrape error: {e}")
                db.log_field_error(con, run_id, code, "reit", e, _now())
                from .models import Reit
                reit = Reit(code=code)

            # 2) 価格 + 指標
            try:
                df, source = get_prices(code, cfg.stock.get("history_period", "max"),
                                        cfg.path("price_cache"), cfg.net.get("min_sleep_seconds", 2))
                if not df.empty:
                    db.save_price_history(con, code, df)
                mres = compute_metrics(df, cfg.stock.get("deviation_years", 6),
                                       cfg.stock["lehman_start"], cfg.stock["lehman_end"])
                info = fetch_info(code) if source != "cache" else {}
                m = StockMetrics(
                    code=code, price_asof=mres["price_asof"], latest_price=mres["latest_price"],
                    volume=mres["volume"], market_cap=info.get("market_cap"),
                    nav_ratio=info.get("nav_ratio"),
                    dev_mean_6y_pct=mres["dev_mean_6y_pct"], dev_median_6y_pct=mres["dev_median_6y_pct"],
                    ma=mres["ma"], lehman_min=mres["lehman_min"],
                    lehman_ratio_pct=mres["lehman_ratio_pct"], price_source=source)
                db.upsert_metrics(con, m, _now())
            except Exception as e:  # noqa
                clog.error(f"stock metrics error: {e}")
                db.log_field_error(con, run_id, code, "stock", e, _now())

            # 3) 分配金（決算短信PDF）
            try:
                for d in analyze_dividends(client, reit, cfg.path("pdf_cache"),
                                           cfg.dividends.get("quarters", 10),
                                           cfg.sources["japan_reit_base"]):
                    db.upsert_dividend(con, d)
            except Exception as e:  # noqa
                clog.error(f"dividend error: {e}")
                db.log_field_error(con, run_id, code, "dividends", e, _now())

            con.commit()
            succeeded += 1
            clog.info("done")
        except Exception as e:  # noqa  最後の砦
            clog.error(f"fatal per-code error (continuing): {e}")
            db.log_field_error(con, run_id, code, "fatal", e, _now())
            con.commit()

    db.finish_run(con, run_id, _now(), succeeded, "ok" if succeeded else "empty")
    con.commit(); con.close()
    log.bind(run=run_id).info(f"FINISH succeeded={succeeded}/{len(codes)}")
    return {"run_id": run_id, "attempted": len(codes), "succeeded": succeeded}


def run_edinet(codes: list[str], cfg: Config) -> dict:
    """EDINETバッチ取込: 各REITのファンダ(含み損益/LTV/NOI等)を取得し fundamentals テーブルへ。"""
    log = setup_logging(cfg.path("log"))
    con = db.connect(cfg.path("db"))
    client = HttpClient(cfg.net.get("user_agent", "jreit/0.1"),
                        cfg.net.get("max_retries", 3),
                        cfg.net.get("min_sleep_seconds", 2),
                        cfg.net.get("timeout_seconds", 30))
    key_env = cfg.dividends.get("edinet_api_key_env", "EDINET_API_KEY")
    api_key = os.environ.get(key_env)
    days_back = int(cfg.dividends.get("edinet_days_back", 400))
    from .scrapers.edinet import fetch_fundamentals
    if not api_key:
        log.warning(f"EDINET: 環境変数 {key_env} が未設定 → no_key で記録（取得スキップ）")

    ok = 0
    for f in fetch_fundamentals(codes, client, api_key, days_back=days_back):
        try:
            db.upsert_fundamentals(con, f, _now())
            con.commit()
            if f.get("parse_status") in ("ok", "partial"):
                ok += 1
        except Exception as e:  # noqa
            log.error(f"{f.get('code')}: fundamentals upsert error: {e}")
    con.close()
    status = "ok" if api_key else "no_key"
    log.info(f"EDINET FINISH parsed={ok}/{len(codes)} status={status}")
    return {"attempted": len(codes), "parsed": ok, "status": status}
