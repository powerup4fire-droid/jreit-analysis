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
import altair as alt   # Streamlit同梱（追加インストール不要）

DB = Path(__file__).resolve().parent / "data" / "jreit.db"
ASSET_COLS = {
    "asset_office": "オフィス", "asset_residential": "住居", "asset_logistics": "物流",
    "asset_retail": "商業", "asset_hotel": "ホテル", "asset_healthcare": "ヘルスケア",
    "asset_other": "その他",
}
# 主用途/タイプのセル着色（色で判別）
ASSET_STYLE = {
    "オフィス": ("#1b5e20", "white"), "ホテル": ("#b71c1c", "white"),
    "物流": ("#6fa8dc", "#0b2545"), "商業": ("#e6a817", "#3a2a00"),
    "住居": ("#9bbb59", "#1f2d0a"), "ヘルスケア": ("#1a4f8a", "white"),
    "その他": ("#9e9e9e", "white"),
}
# 円グラフ配色（japan-reit.com 準拠）
PIE_COLORS = {
    "オフィス": "#5b7fc7", "住居": "#d6749a", "商業": "#b03a48",
    "ホテル": "#de5b52", "物流": "#e8923a", "その他": "#ecc94b",
    "ヘルスケア": "#5aa06a",
}
# reit_type 内の英語表記 → 日本語
TYPE_JP = {"office": "オフィス", "residential": "住居", "logistics": "物流",
           "retail": "商業", "hotel": "ホテル", "healthcare": "ヘルスケア"}
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


def jp_type(t) -> str:
    s = str(t or "")
    for en, ja in TYPE_JP.items():
        s = s.replace(en, ja)
    return s or "—"


def use_info(row) -> tuple[str, str]:
    """(色用の主用途1つ, 表示ラベル) を返す。
    - その他は主用途に使わない。ヘルスケアは『ヘルスケア』表記。
    - 最大用途を採用。トップとの差が5%以内の用途は併記（例: オフィス・住居）。"""
    rt, nm = str(row.get("reit_type") or ""), str(row.get("name") or "")
    if "healthcare" in rt or "ヘルスケア" in rt or "ヘルスケア" in nm:
        return "ヘルスケア", "ヘルスケア"
    pcts = {ja: row.get(col) for col, ja in ASSET_COLS.items() if ja != "その他"}
    pcts = {k: float(v) for k, v in pcts.items() if pd.notna(v) and v > 0}
    if not pcts:
        lbl = jp_type(rt)
        return "その他", (lbl if lbl != "—" else "その他")
    ranked = sorted(pcts.items(), key=lambda x: -x[1])
    top = ranked[0][1]
    near = [k for k, v in ranked if top - v <= 5.0]
    return ranked[0][0], "・".join(near)


def period_key(label):
    m = re.match(r"(\d{4})年(\d{1,2})月期", str(label))
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def main():
    st.title("🏢 J-REIT 分析ダッシュボード")
    if not DB.exists():
        st.error("data/jreit.db がありません。"); return
    reits, metrics, divs, runs = load("reits"), load("stock_metrics"), load("dividends"), load("scrape_runs")
    if reits.empty:
        st.warning("データが空です。"); return
    if not runs.empty:
        last = runs.sort_values("finished_at").iloc[-1]
        st.caption(f"最終更新 {last.get('finished_at','?')} ・ 銘柄 {len(reits)} ・ キャッシュ参照のみ")

    df = reits.merge(metrics, on="code", how="left", suffixes=("", "_m"))
    uinfo = df.apply(use_info, axis=1)
    df["use_primary"] = uinfo.map(lambda x: x[0])
    df["use_label"] = uinfo.map(lambda x: x[1])
    df["type_jp"] = df["reit_type"].map(jp_type)
    df["mktcap_oku"] = df["market_cap"] / 1e8
    df["dev_200d_pct"] = np.where(df["ma_200d"].notna() & df["latest_price"].notna() & (df["ma_200d"] != 0),
                                  (df["latest_price"] - df["ma_200d"]) / df["ma_200d"] * 100, np.nan)

    def excess_in_window(code, n):
        d = divs[(divs["code"] == code) & (divs["period_label"] != "latest")]
        if d.empty:
            return None
        d = d.assign(k=d["period_label"].map(period_key)).sort_values("k", ascending=False).head(n)
        return bool((d["excess_present"] == 1).any()) if len(d) else None

    df["exc6"] = df["code"].map(lambda c: excess_in_window(c, 6))
    df["exc10"] = df["code"].map(lambda c: excess_in_window(c, 10))

    # ===== フィルタ =====
    st.subheader("📋 サマリ")
    c1, c2, c3 = st.columns([2, 2, 1])
    uses = sorted(df["use_primary"].dropna().unique().tolist())
    pick = c1.multiselect("主用途で絞り込み", uses, default=uses)
    only_no_excess = c2.checkbox("利益超過分配金なしのみ", value=False,
                                 help="直近10期で利益超過分配金が一度も無い銘柄だけ表示")
    sort_key = c3.selectbox("並び替え", ["利回り%", "6年平均乖離%", "リーマン比%", "時価総額"])

    view = df[df["use_primary"].isin(pick)].copy()
    if only_no_excess:
        view = view[view["exc10"] != True]  # noqa: E712

    def yn(v):
        return "✓" if v is True else ("—" if v is None else "")

    summary = pd.DataFrame({
        "コード": view["code"], "名称": view["name"], "主用途": view["use_label"],
        "タイプ": view["type_jp"], "利回り%": view["yield_total"].round(2),
        "価格": view["latest_price"], "出来高": view["volume"],
        "時価総額(億円)": view["mktcap_oku"].round(0), "NAV倍率": view["nav_ratio"].round(2),
        "200日乖離%": view["dev_200d_pct"].round(1), "6年平均乖離%": view["dev_mean_6y_pct"].round(1),
        "リーマン比%": view["lehman_ratio_pct"].round(1),
        "利益超過(6期)": view["exc6"].map(yn), "利益超過(10期)": view["exc10"].map(yn),
        "_primary": view["use_primary"],
    })
    sort_map = {"利回り%": ("利回り%", False), "6年平均乖離%": ("6年平均乖離%", True),
                "リーマン比%": ("リーマン比%", True), "時価総額": ("時価総額(億円)", False)}
    col, asc = sort_map[sort_key]
    summary = summary.sort_values(col, ascending=asc, na_position="last").reset_index(drop=True)
    primaries = summary.pop("_primary")

    cols = list(summary.columns)
    i_use, i_type = cols.index("主用途"), cols.index("タイプ")

    def type_style(type_jp):
        t = str(type_jp)
        if "総合" in t or "複合" in t or "統合" in t:   # 分散型はグレー
            return ASSET_STYLE["その他"]
        for ja in ["オフィス", "ホテル", "物流", "商業", "住居", "ヘルスケア"]:
            if ja in t:
                return ASSET_STYLE[ja]
        return ASSET_STYLE["その他"]

    def color_row(row):
        s = [""] * len(row)
        bg, fg = ASSET_STYLE.get(primaries.iloc[row.name], ("", ""))
        if bg:
            s[i_use] = f"background-color:{bg};color:{fg};font-weight:600"
        tb, tf = type_style(row["タイプ"])      # タイプは「総合/複合」=グレー, 特化=用途色
        s[i_type] = f"background-color:{tb};color:{tf};font-weight:600"
        return s

    # st.dataframe(Styler) は na_rep を無視するため、数値列を文字列へ整形して NA を「—」にする
    num_fmt = {
        "利回り%": "{:.2f}", "価格": "{:,.0f}", "出来高": "{:,.0f}",
        "時価総額(億円)": "{:,.0f}", "NAV倍率": "{:.2f}",
        "200日乖離%": "{:+.1f}", "6年平均乖離%": "{:+.1f}", "リーマン比%": "{:.1f}",
    }
    for c, spec in num_fmt.items():
        summary[c] = summary[c].map(lambda v, s=spec: "—" if pd.isna(v) else s.format(v))
    styled = summary.style.apply(color_row, axis=1)
    st.dataframe(styled, use_container_width=True, hide_index=True, height=460)
    legend = "　".join(
        f'<span style="background:{c[0]};color:{c[1]};padding:2px 8px;border-radius:4px;font-size:12px">{k}</span>'
        for k, c in ASSET_STYLE.items())
    st.markdown("セルの用途色: " + legend, unsafe_allow_html=True)
    st.caption(f"表示 {len(summary)} / 全 {len(df)} 銘柄")

    # ===== 個別 =====
    st.subheader("🔎 個別銘柄")
    opts = {f"{r.code} {r['name']}": r.code for _, r in df.sort_values("code").iterrows()}
    code = opts[st.selectbox("銘柄を選択", list(opts.keys()))]
    row = df[df["code"] == code].iloc[0]

    bg, fg = ASSET_STYLE.get(row["use_primary"], ("#ddd", "#000"))
    st.markdown(
        f'**{row["name"]}**（{code}）　'
        f'<span style="background:{bg};color:{fg};padding:2px 10px;border-radius:6px">'
        f'{row["use_label"]} / {row["type_jp"]}</span>', unsafe_allow_html=True)

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
        st.markdown("**用途別ポートフォリオ構成**")
        amap = {ja: float(row[col]) for col, ja in ASSET_COLS.items() if pd.notna(row.get(col)) and row.get(col)}
        if amap:
            order = list(PIE_COLORS.keys())
            pdf = pd.DataFrame({"用途": list(amap.keys()), "比率": list(amap.values())})
            donut = alt.Chart(pdf).mark_arc(innerRadius=55).encode(
                theta=alt.Theta("比率:Q", stack=True),
                color=alt.Color("用途:N",
                                scale=alt.Scale(domain=order, range=[PIE_COLORS[k] for k in order]),
                                legend=alt.Legend(title=None)),
                order=alt.Order("比率:Q", sort="descending"),
                tooltip=[alt.Tooltip("用途:N"), alt.Tooltip("比率:Q", format=".1f")],
            ).properties(height=300)
            st.altair_chart(donut, use_container_width=True)
            brk = "　".join(f"{k} {v:.1f}%" for k, v in sorted(amap.items(), key=lambda x: -x[1]))
            est = "（推定）" if row.get("asset_estimated") else ""
            st.caption(f"{brk}　／　物件数 {fmt(row['num_properties'])} {est}")
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
            "期": d10["period_label"], "総分配金(円)": d10["total_distribution"],
            "うち利益超過(円)": d10["excess_distribution"],
            "利益超過比率%": d10["excess_ratio_pct"].round(2), "状態": d10["parse_status"],
        })
        st.dataframe(tbl, use_container_width=True, hide_index=True)
        plot = d10.dropna(subset=["total_distribution"]).sort_values("k")
        if not plot.empty:
            st.line_chart(plot.set_index("period_label")[["total_distribution", "excess_distribution"]])


if __name__ == "__main__":
    main()
