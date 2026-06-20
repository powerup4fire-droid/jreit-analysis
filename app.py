"""J-REIT 分析ダッシュボード（read-only / cached-first）。
SQLite(data/jreit.db) のみ参照し、ライブスクレイピングは一切行わない。
スマホ/PC 両対応（Streamlitの自動リフロー）。起動: streamlit run app.py
"""
from __future__ import annotations
import sqlite3
from pathlib import Path
import numpy as np
import pandas as pd
import streamlit as st

DB = Path(__file__).resolve().parent / "data" / "jreit.db"
ASSET_COLS = {
    "asset_office": "オフィス", "asset_residential": "住居", "asset_logistics": "物流",
    "asset_retail": "商業", "asset_hotel": "ホテル", "asset_healthcare": "ヘルスケア",
    "asset_other": "その他",
}
MA_KEYS = ["ma_25d", "ma_75d", "ma_200d", "ma_75w", "ma_200w", "ma_75m", "ma_200m"]

st.set_page_config(page_title="J-REIT 分析", page_icon="🏢", layout="wide",
                   initial_sidebar_state="collapsed")


@st.cache_data(ttl=120)
def load(table: str) -> pd.DataFrame:
    if not DB.exists():
        return pd.DataFrame()
    con = sqlite3.connect(DB)
    try:
        return pd.read_sql_query(f"SELECT * FROM {table}", con)
    finally:
        con.close()


def fmt(v, dec=0, suf=""):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    return f"{v:,.{dec}f}{suf}"


def dominant_asset(row) -> str:
    vals = {ja: row.get(col) for col, ja in ASSET_COLS.items()}
    vals = {k: v for k, v in vals.items() if pd.notna(v) and v}
    return max(vals, key=vals.get) if vals else "—"


def main():
    st.title("🏢 J-REIT 分析ダッシュボード")
    if not DB.exists():
        st.error("data/jreit.db がありません。`python update_data.py` を実行してください。")
        return
    reits, metrics, divs, runs = load("reits"), load("stock_metrics"), load("dividends"), load("scrape_runs")
    if reits.empty:
        st.warning("データが空です。`python update_data.py` を実行してください。")
        return
    if not runs.empty:
        last = runs.sort_values("finished_at").iloc[-1]
        st.caption(f"最終更新 {last.get('finished_at','?')} ・ 銘柄 {len(reits)} ・ "
                   f"キャッシュ参照のみ（ライブ取得なし）")

    df = reits.merge(metrics, on="code", how="left", suffixes=("", "_m"))
    df["dominant"] = df.apply(dominant_asset, axis=1)
    df["mktcap_oku"] = df["market_cap"] / 1e8
    # 200日MA乖離
    df["dev_200d_pct"] = np.where(df["ma_200d"].notna() & df["latest_price"].notna() & (df["ma_200d"] != 0),
                                  (df["latest_price"] - df["ma_200d"]) / df["ma_200d"] * 100, np.nan)
    # 利益超過分配の有無（10期で1度でもあれば True）
    exc = divs.groupby("code")["excess_present"].max() if not divs.empty else pd.Series(dtype=int)
    df["excess_any"] = df["code"].map(exc).fillna(0).astype(int)

    # ===== フィルタ =====
    st.subheader("📋 サマリ")
    f1, f2, f3 = st.columns([2, 2, 1])
    types = sorted(df["dominant"].dropna().unique().tolist())
    pick_types = f1.multiselect("主用途で絞り込み", types, default=types)
    only_excess = f2.checkbox("利益超過分配ありのみ", value=False)
    sort_key = f3.selectbox("並び替え", ["利回り%", "6年平均乖離%", "リーマン比%", "時価総額"])

    view = df[df["dominant"].isin(pick_types)].copy()
    if only_excess:
        view = view[view["excess_any"] == 1]

    summary = pd.DataFrame({
        "コード": view["code"], "名称": view["name"], "主用途": view["dominant"],
        "タイプ": view["reit_type"], "利回り%": view["yield_total"].round(2),
        "価格": view["latest_price"], "出来高": view["volume"],
        "時価総額(億円)": view["mktcap_oku"].round(0), "NAV倍率": view["nav_ratio"].round(2),
        "200日乖離%": view["dev_200d_pct"].round(1),
        "6年平均乖離%": view["dev_mean_6y_pct"].round(1),
        "リーマン比%": view["lehman_ratio_pct"].round(1),
        "利益超過": np.where(view["excess_any"] == 1, "✓", ""),
    })
    sort_map = {"利回り%": ("利回り%", False), "6年平均乖離%": ("6年平均乖離%", True),
                "リーマン比%": ("リーマン比%", True), "時価総額": ("時価総額(億円)", False)}
    col, asc = sort_map[sort_key]
    summary = summary.sort_values(col, ascending=asc, na_position="last")
    st.dataframe(summary, use_container_width=True, hide_index=True, height=420)
    st.caption(f"表示 {len(summary)} / 全 {len(df)} 銘柄")

    # ===== 個別 =====
    st.subheader("🔎 個別銘柄")
    opts = {f"{r.code} {r['name']}": r.code for _, r in df.sort_values("code").iterrows()}
    code = opts[st.selectbox("銘柄を選択", list(opts.keys()))]
    row = df[df["code"] == code].iloc[0]

    m = st.columns(4)
    m[0].metric("価格", fmt(row["latest_price"]))
    m[1].metric("利回り", fmt(row["yield_total"], 2, "%"))
    m[2].metric("200日乖離", fmt(row["dev_200d_pct"], 1, "%"))
    m[3].metric("リーマン比", fmt(row["lehman_ratio_pct"], 1, "%"))
    m2 = st.columns(4)
    m2[0].metric("NAV倍率", fmt(row["nav_ratio"], 2))
    m2[1].metric("時価総額", fmt(row["mktcap_oku"], 0, "億円"))
    m2[2].metric("6年平均乖離", fmt(row["dev_mean_6y_pct"], 1, "%"))
    m2[3].metric("6年中央乖離", fmt(row["dev_median_6y_pct"], 1, "%"))

    cL, cR = st.columns(2)
    with cL:
        st.markdown("**用途別ポートフォリオ構成 (%)**")
        amap = {ja: row[col] for col, ja in ASSET_COLS.items() if pd.notna(row.get(col)) and row.get(col)}
        if amap:
            st.bar_chart(pd.Series(amap, name="比率%"))
            est = "（推定）" if row.get("asset_estimated") else ""
            st.caption(f"物件数 {fmt(row['num_properties'])}　主用途 {row['dominant']}{est}")
        else:
            st.caption("構成データなし")
    with cR:
        st.markdown("**価格 vs 移動平均（乖離%）**")
        rows = []
        for k in MA_KEYS:
            v = row.get(k)
            dev = ((row["latest_price"] - v) / v * 100) if (pd.notna(v) and v and pd.notna(row["latest_price"])) else np.nan
            rows.append({"MA": k.replace("ma_", "").upper(), "値": fmt(v), "乖離%": fmt(dev, 1, "%")})
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # ===== 分配金 =====
    st.markdown("**分配金（1口当たり・利益超過分配金）**")
    d = divs[(divs["code"] == code) & (divs["period_label"] != "latest")].copy()
    if not d.empty:
        d = d.sort_values("period_label")
        dd = pd.DataFrame({
            "期": d["period_label"], "総分配金": d["total_distribution"],
            "利益超過分配金": d["excess_distribution"],
            "比率%": d["excess_ratio_pct"].round(2), "状態": d["parse_status"],
        })
        st.dataframe(dd, use_container_width=True, hide_index=True)
        plot = d.dropna(subset=["total_distribution"])
        if not plot.empty:
            st.line_chart(plot.set_index("period_label")["total_distribution"])
        n_excess = int((d["excess_present"] == 1).sum())
        st.caption(f"利益超過分配: {n_excess}/{len(d)} 期で実施" if n_excess else "利益超過分配: 実施なし")
    else:
        st.caption("分配金データなし")


if __name__ == "__main__":
    main()
