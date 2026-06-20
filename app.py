"""J-REIT 分析ダッシュボード（read-only / cached-first）。
SQLite(data/jreit.db) のみ参照。スマホ/PC両対応。起動: streamlit run app.py
"""
from __future__ import annotations
import re
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
# 用途 → (背景色, 文字色)
ASSET_STYLE = {
    "オフィス": ("#1b5e20", "white"),    # 深緑
    "ホテル": ("#b71c1c", "white"),      # 濃い赤
    "物流": ("#6fa8dc", "#0b2545"),      # 落ち着いた水色
    "商業": ("#e6a817", "#3a2a00"),      # 山吹色
    "住居": ("#9bbb59", "#1f2d0a"),      # 落ち着いた黄緑
    "ヘルスケア": ("#1a4f8a", "white"),  # 濃い青
    "その他": ("#9e9e9e", "white"),
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
    return max(vals, key=vals.get) if vals else "その他"


def period_key(label):
    m = re.match(r"(\d{4})年(\d{1,2})月期", str(label))
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def main():
    st.title("🏢 J-REIT 分析ダッシュボード")
    if not DB.exists():
        st.error("data/jreit.db がありません。`python update_data.py` を実行してください。")
        return
    reits, metrics, divs, runs = load("reits"), load("stock_metrics"), load("dividends"), load("scrape_runs")
    if reits.empty:
        st.warning("データが空です。"); return
    if not runs.empty:
        last = runs.sort_values("finished_at").iloc[-1]
        st.caption(f"最終更新 {last.get('finished_at','?')} ・ 銘柄 {len(reits)} ・ キャッシュ参照のみ（ライブ取得なし）")

    df = reits.merge(metrics, on="code", how="left", suffixes=("", "_m"))
    df["dominant"] = df.apply(dominant_asset, axis=1)
    df["mktcap_oku"] = df["market_cap"] / 1e8
    df["dev_200d_pct"] = np.where(df["ma_200d"].notna() & df["latest_price"].notna() & (df["ma_200d"] != 0),
                                  (df["latest_price"] - df["ma_200d"]) / df["ma_200d"] * 100, np.nan)

    # 利益超過分配金: 直近6期/10期に「あり」が含まれるか
    def excess_in_window(code, n):
        d = divs[(divs["code"] == code) & (divs["period_label"] != "latest")]
        if d.empty:
            return None
        d = d.assign(k=d["period_label"].map(period_key)).sort_values("k", ascending=False).head(n)
        if d.empty:
            return None
        return bool((d["excess_present"] == 1).any())

    df["exc6"] = df["code"].map(lambda c: excess_in_window(c, 6))
    df["exc10"] = df["code"].map(lambda c: excess_in_window(c, 10))

    # ===== フィルタ =====
    st.subheader("📋 サマリ")
    c1, c2, c3 = st.columns([2, 2, 1])
    types = sorted(df["dominant"].dropna().unique().tolist())
    pick = c1.multiselect("主用途で絞り込み", types, default=types)
    only_no_excess = c2.checkbox("利益超過分配金なしのみ", value=False,
                                 help="直近10期で利益超過分配金が一度も無い銘柄だけ表示")
    sort_key = c3.selectbox("並び替え", ["利回り%", "6年平均乖離%", "リーマン比%", "時価総額"])

    view = df[df["dominant"].isin(pick)].copy()
    if only_no_excess:
        view = view[view["exc10"] != True]  # noqa: E712  なし（False or 不明）

    def yn(v):
        return "✓" if v is True else ("—" if v is None else "")

    summary = pd.DataFrame({
        "コード": view["code"], "名称": view["name"], "主用途": view["dominant"],
        "タイプ": view["reit_type"], "利回り%": view["yield_total"].round(2),
        "価格": view["latest_price"], "出来高": view["volume"],
        "時価総額(億円)": view["mktcap_oku"].round(0), "NAV倍率": view["nav_ratio"].round(2),
        "200日乖離%": view["dev_200d_pct"].round(1), "6年平均乖離%": view["dev_mean_6y_pct"].round(1),
        "リーマン比%": view["lehman_ratio_pct"].round(1),
        "利益超過(6期)": view["exc6"].map(yn), "利益超過(10期)": view["exc10"].map(yn),
    })
    sort_map = {"利回り%": ("利回り%", False), "6年平均乖離%": ("6年平均乖離%", True),
                "リーマン比%": ("リーマン比%", True), "時価総額": ("時価総額(億円)", False)}
    col, asc = sort_map[sort_key]
    summary = summary.sort_values(col, ascending=asc, na_position="last").reset_index(drop=True)

    # 主用途・タイプのセルを用途色で着色
    cols = list(summary.columns)
    i_use, i_type = cols.index("主用途"), cols.index("タイプ")

    def color_row(row):
        s = [""] * len(row)
        bg, fg = ASSET_STYLE.get(row["主用途"], ("", ""))
        if bg:
            css = f"background-color:{bg};color:{fg};font-weight:600"
            s[i_use] = css
            s[i_type] = css
        return s

    sty = summary.style.apply(color_row, axis=1)
    st.dataframe(sty, use_container_width=True, hide_index=True, height=460)
    # 凡例
    legend = "　".join(
        f'<span style="background:{c[0]};color:{c[1]};padding:2px 8px;border-radius:4px;font-size:12px">{k}</span>'
        for k, c in ASSET_STYLE.items())
    st.markdown("用途の色: " + legend, unsafe_allow_html=True)
    st.caption(f"表示 {len(summary)} / 全 {len(df)} 銘柄")

    # ===== 個別 =====
    st.subheader("🔎 個別銘柄")
    opts = {f"{r.code} {r['name']}": r.code for _, r in df.sort_values("code").iterrows()}
    code = opts[st.selectbox("銘柄を選択", list(opts.keys()))]
    row = df[df["code"] == code].iloc[0]

    bg, fg = ASSET_STYLE.get(row["dominant"], ("#ddd", "#000"))
    st.markdown(
        f'**{row["name"]}**（{code}）　'
        f'<span style="background:{bg};color:{fg};padding:2px 10px;border-radius:6px">'
        f'{row["dominant"]} / {row["reit_type"]}</span>', unsafe_allow_html=True)

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
            st.caption(f"物件数 {fmt(row['num_properties'])}　{est}")
        else:
            st.caption("構成データなし")
    with cR:
        st.markdown("**価格 vs 移動平均（乖離%）**")
        rr = []
        for k in MA_KEYS:
            v = row.get(k)
            dev = ((row["latest_price"] - v) / v * 100) if (pd.notna(v) and v and pd.notna(row["latest_price"])) else np.nan
            rr.append({"MA": k.replace("ma_", "").upper(), "値": fmt(v), "乖離%": fmt(dev, 1, "%")})
        st.dataframe(pd.DataFrame(rr), use_container_width=True, hide_index=True)

    # ===== 分配金・利益超過分配金 =====
    st.markdown("**分配金・利益超過分配金（直近10期）**")
    d = divs[(divs["code"] == code) & (divs["period_label"] != "latest")].copy()
    if d.empty:
        st.caption("分配金データなし")
    else:
        d = d.assign(k=d["period_label"].map(period_key)).sort_values("k", ascending=False)
        d10 = d.head(10)
        # 集計（6期/10期）
        def agg(dd):
            n = len(dd)
            ne = int((dd["excess_present"] == 1).sum())
            tot = dd["total_distribution"].sum(skipna=True)
            exc = dd["excess_distribution"].sum(skipna=True)
            ratio = (exc / tot * 100) if tot else None
            return n, ne, ratio
        n6, ne6, r6 = agg(d.head(6))
        n10, ne10, r10 = agg(d10)
        a, b = st.columns(2)
        a.metric("利益超過 直近6期", f"{ne6}/{n6} 期", f"分配金の {r6:.1f}%" if (ne6 and r6) else "なし")
        b.metric("利益超過 直近10期", f"{ne10}/{n10} 期", f"分配金の {r10:.1f}%" if (ne10 and r10) else "なし")

        tbl = pd.DataFrame({
            "期": d10["period_label"],
            "総分配金(円)": d10["total_distribution"],
            "うち利益超過(円)": d10["excess_distribution"],
            "利益超過比率%": d10["excess_ratio_pct"].round(2),
            "状態": d10["parse_status"],
        })
        st.dataframe(tbl, use_container_width=True, hide_index=True)
        plot = d10.dropna(subset=["total_distribution"]).sort_values("k")
        if not plot.empty:
            st.line_chart(plot.set_index("period_label")[["total_distribution", "excess_distribution"]])


if __name__ == "__main__":
    main()
