# J-REIT 分析システム

データ取得（バッチ）と ダッシュボード（Streamlit, read-only）を分離した cached-first 構成。

## 構成
- `update_data.py` … データ取得パイプライン（japan-reit.com / yfinance / 決算短信PDF）→ `data/jreit.db`
- `app.py` … Streamlit ダッシュボード（**SQLiteのみ参照・ライブ取得なし**）
- `src/jreit/` … スクレイパ・計算・DB・モデル

## ローカル実行
```bash
uv venv && uv pip install -e . && uv pip install setuptools
uv run python update_data.py            # 検証3銘柄
uv run python update_data.py --all      # 全銘柄（sitemap）
uv run streamlit run app.py             # ダッシュボード
```

## 恒久ホスティング（Streamlit Community Cloud, 無料）
cached-first なので **`data/jreit.db` をリポジトリに含めて配布**し、ホスト側はそれを読むだけ。

1. このフォルダを GitHub リポジトリにpush（`data/jreit.db` を含める。`.gitignore` で cache/logs は除外済み）
2. https://share.streamlit.io/ にGitHubでログイン →「New app」
3. リポジトリ / ブランチ / `app.py` を指定 → Deploy
4. 数分で `https://<app>.streamlit.app` の**恒久URL**が発行（iPhone可・自動レスポンシブ）

### データ更新の運用
- ローカル（またはcron）で `python update_data.py --all` を定期実行 → `data/jreit.db` を commit & push
- push の度に Streamlit Cloud が自動再デプロイ＝最新データが反映
- （任意）GitHub Actions のスケジュールで pipeline を回し DB を自動commitも可

## 設計上の制約
- UIからライブスクレイピングしない（cached-first）
- ネットワークは retry(最大3)＋指数バックオフ＋各リクエスト後 sleep(2)
- 欠損はクラッシュさせず NULL＋`field_errors`/`parse_status` に記録（再現性のため raw を `data/cache` に保存）
