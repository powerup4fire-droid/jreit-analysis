#!/usr/bin/env python3
"""J-REIT データパイプライン エントリ（バッチ実行のみ。UIからは呼ばない）。

使い方:
  python update_data.py                 # config.yaml の検証3銘柄
  python update_data.py --codes 8985    # 指定銘柄
  python update_data.py --limit 1       # 検証銘柄の先頭N
  python update_data.py --all           # 全銘柄（未実装の安全弁: 明示が必要）
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from jreit.config import load_config          # noqa: E402
from jreit.pipeline import run, run_edinet     # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", default="", help="カンマ区切りの銘柄コード（例 8985,8960）")
    ap.add_argument("--limit", type=int, default=0, help="検証銘柄の先頭N件")
    ap.add_argument("--all", action="store_true", help="全銘柄（sitemapから取得して全件）")
    ap.add_argument("--edinet", action="store_true",
                    help="EDINET取込（含み損益/LTV/NOI等）。要 環境変数 EDINET_API_KEY")
    args = ap.parse_args()

    cfg = load_config()
    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    elif args.all:
        if args.edinet:
            # EDINETは既存DBの銘柄に対して実行（sitemap全件スクレイプは不要）
            import sqlite3
            con = sqlite3.connect(cfg.path("db"))
            codes = [r[0] for r in con.execute("SELECT code FROM reits ORDER BY code")]
            con.close()
        else:
            from jreit.http import HttpClient                       # noqa: E402
            from jreit.scrapers.reit_list import fetch_all_codes    # noqa: E402
            net = cfg.net
            client = HttpClient(net.get("user_agent", "jreit/0.1"), net.get("max_retries", 3),
                                net.get("min_sleep_seconds", 2), net.get("timeout_seconds", 30))
            codes = fetch_all_codes(client)
        if args.limit:
            codes = codes[: args.limit]
        if not codes:
            print("⚠ 対象銘柄を取得できませんでした。"); sys.exit(2)
    else:
        codes = cfg.validation_codes
        if args.limit:
            codes = codes[: args.limit]

    if args.edinet:
        print(f"▶ EDINET取込 対象: {len(codes)}銘柄")
        summary = run_edinet(codes, cfg)
    else:
        print(f"▶ pipeline 対象: {codes}")
        summary = run(codes, cfg)
    print(f"✅ 完了: {summary}")


if __name__ == "__main__":
    main()
