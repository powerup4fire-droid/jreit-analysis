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
from jreit.pipeline import run                # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", default="", help="カンマ区切りの銘柄コード（例 8985,8960）")
    ap.add_argument("--limit", type=int, default=0, help="検証銘柄の先頭N件")
    ap.add_argument("--all", action="store_true", help="全銘柄（sitemapから取得して全件）")
    args = ap.parse_args()

    cfg = load_config()
    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    elif args.all:
        from jreit.http import HttpClient                       # noqa: E402
        from jreit.scrapers.reit_list import fetch_all_codes    # noqa: E402
        net = cfg.net
        client = HttpClient(net.get("user_agent", "jreit/0.1"), net.get("max_retries", 3),
                            net.get("min_sleep_seconds", 2), net.get("timeout_seconds", 30))
        codes = fetch_all_codes(client)
        if args.limit:
            codes = codes[: args.limit]
        if not codes:
            print("⚠ 全銘柄コードを取得できませんでした。"); sys.exit(2)
    else:
        codes = cfg.validation_codes
        if args.limit:
            codes = codes[: args.limit]

    print(f"▶ pipeline 対象: {codes}")
    summary = run(codes, cfg)
    print(f"✅ 完了: {summary}")


if __name__ == "__main__":
    main()
