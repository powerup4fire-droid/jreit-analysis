"""J-REIT 分析ダッシュボード（read-only / cached-first）。
SQLite(data/jreit.db) のみ参照。スマホ/PC両対応。起動: streamlit run app.py

画面（上部の切替ボタン）:
  📋 ダッシュボード … サマリ一覧 + 個別銘柄（サマリで選んだ行が自動で個別に反映）
  ⚖️ 銘柄比較      … 複数銘柄を横並びでスペック比較（Apple compare 風）
  💼 マイポートフォリオ … 保有銘柄から全体の利回り・分配金見込み・含み益・用途構成を集計
"""
from __future__ import annotations
import datetime as dt
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

# multiselect のタグ（チップ）背景を白に統一（用途別の赤色を消す）
st.markdown(
    """<style>
    span[data-baseweb="tag"]{background-color:#ffffff !important;border:1px solid #cbd5e1 !important;}
    span[data-baseweb="tag"] span{color:#1f2937 !important;}
    span[data-baseweb="tag"] svg{fill:#64748b !important;}
    </style>""",
    unsafe_allow_html=True,
)


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


# ---------------------------------------------------------------------------
# 共通: データフレーム整形
# ---------------------------------------------------------------------------
def build_frame():
    reits, metrics, divs, runs = load("reits"), load("stock_metrics"), load("dividends"), load("scrape_runs")
    if reits.empty:
        return None, None, None, reits
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
    return df, divs, runs, reits


def label_maps(df):
    d = df.sort_values("code")
    l2c, c2l, labels = {}, {}, []
    for _, r in d.iterrows():
        lbl = f"{r['code']} {r['name']}"
        labels.append(lbl)
        l2c[lbl] = r["code"]
        c2l[r["code"]] = lbl
    return labels, l2c, c2l


def annual_distribution(divs, code):
    """直近12ヶ月ぶんの1口当たり分配金・利益超過分配金を返す。
    年1回/半期/四半期決算が混在しても正しく年換算できるよう、最新期から12ヶ月内の期を合算。"""
    d = divs[(divs["code"] == code) & (divs["period_label"] != "latest")].copy()
    if d.empty:
        return None, None
    d["ym"] = d["period_label"].map(lambda l: (lambda k: k[0] * 12 + k[1])(period_key(l)))
    d = d[d["ym"] > 0]
    if d.empty:
        return None, None
    latest = d["ym"].max()
    win = d[latest - d["ym"] < 12]   # 直近12ヶ月（年1回=1期, 半期=2期, 四半期=4期）
    tot = win["total_distribution"].sum(skipna=True)
    exc = win["excess_distribution"].sum(skipna=True)
    return (float(tot) if pd.notna(tot) else None,
            float(exc) if pd.notna(exc) else None)


# ===========================================================================
# 📋 ダッシュボード
# ===========================================================================
def render_dashboard(df, divs):
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

    st.caption("行をクリックすると下の「個別銘柄」に自動表示されます。")
    event = st.dataframe(styled, use_container_width=True, hide_index=True, height=460,
                         on_select="rerun", selection_mode="single-row", key="summary_tbl")
    sel = event.selection.rows if event and event.selection else []
    if sel:
        st.session_state["detail_code"] = summary.iloc[sel[0]]["コード"]

    legend = "　".join(
        f'<span style="background:{c[0]};color:{c[1]};padding:2px 8px;border-radius:4px;font-size:12px">{k}</span>'
        for k, c in ASSET_STYLE.items())
    st.markdown("セルの用途色: " + legend, unsafe_allow_html=True)
    st.caption(f"表示 {len(summary)} / 全 {len(df)} 銘柄")

    # ===== 個別 =====
    st.subheader("🔎 個別銘柄")
    labels, l2c, c2l = label_maps(df)
    default_code = st.session_state.get("detail_code", df.sort_values("code").iloc[0]["code"])
    if default_code not in c2l:
        default_code = df.sort_values("code").iloc[0]["code"]
    chosen = st.selectbox("銘柄を選択", labels, index=labels.index(c2l[default_code]))
    st.session_state["detail_code"] = l2c[chosen]
    render_detail(df, divs, l2c[chosen])


def render_detail(df, divs, code):
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
            donut_chart(amap)
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
        return
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


def donut_chart(amap: dict, height=300):
    order = list(PIE_COLORS.keys())
    pdf = pd.DataFrame({"用途": list(amap.keys()), "比率": list(amap.values())})
    donut = alt.Chart(pdf).mark_arc(innerRadius=55).encode(
        theta=alt.Theta("比率:Q", stack=True),
        color=alt.Color("用途:N",
                        scale=alt.Scale(domain=order, range=[PIE_COLORS[k] for k in order]),
                        legend=alt.Legend(title=None)),
        order=alt.Order("比率:Q", sort="descending"),
        tooltip=[alt.Tooltip("用途:N"), alt.Tooltip("比率:Q", format=".1f")],
    ).properties(height=height)
    st.altair_chart(donut, use_container_width=True)


# ===========================================================================
# ⚖️ 銘柄比較
# ===========================================================================
def render_comparison(df, divs):
    st.subheader("⚖️ 銘柄比較")
    labels, l2c, c2l = label_maps(df)
    default = [c2l[c] for c in ["8985", "8960", "8963"] if c in c2l][:3]
    picks = st.multiselect("比較する銘柄（2〜6銘柄を推奨）", labels, default=default, max_selections=6)
    if len(picks) < 2:
        st.info("2銘柄以上を選択してください。")
        return
    codes = [l2c[p] for p in picks]
    rows = {c: df[df["code"] == c].iloc[0] for c in codes}

    # 比較する指標（label, accessor, 数値の良し悪し: 'high'=高いほど良 / 'low' / None=非数値）
    def excess_label(c):
        a, b = annual_distribution(divs, c)
        if a is None:
            return "—"
        if not b:
            return "なし"
        return f"{b / a * 100:.0f}%"

    specs = [
        ("主用途", lambda c: rows[c]["use_label"], None),
        ("タイプ", lambda c: rows[c]["type_jp"], None),
        ("利回り %", lambda c: rows[c]["yield_total"], "high"),
        ("価格 円", lambda c: rows[c]["latest_price"], None),
        ("時価総額 億円", lambda c: rows[c]["mktcap_oku"], "high"),
        ("NAV倍率", lambda c: rows[c]["nav_ratio"], "low"),
        ("物件数", lambda c: rows[c]["num_properties"], "high"),
        ("200日乖離 %", lambda c: rows[c]["dev_200d_pct"], "low"),
        ("6年平均乖離 %", lambda c: rows[c]["dev_mean_6y_pct"], "low"),
        ("リーマン比 %", lambda c: rows[c]["lehman_ratio_pct"], "high"),
        ("年間分配金 円/口", lambda c: annual_distribution(divs, c)[0], "high"),
        ("利益超過(分配比)", excess_label, None),
    ]

    headers = [f"{c} {rows[c]['name']}" for c in codes]
    raw = {}      # 数値（ハイライト判定用）
    disp = {}     # 表示文字列
    for label, acc, _ in specs:
        raw[label] = [acc(c) for c in codes]
    fmt_map = {"利回り %": 2, "価格 円": 0, "時価総額 億円": 0, "NAV倍率": 2, "物件数": 0,
               "200日乖離 %": 1, "6年平均乖離 %": 1, "リーマン比 %": 1, "年間分配金 円/口": 0}
    for label, _, good in specs:
        vals = raw[label]
        out = []
        for v in vals:
            if label in fmt_map:                       # 数値指標 → 桁区切り整形
                out.append(fmt(v, fmt_map[label]))
            else:                                       # 主用途/タイプ/利益超過 等の文字列
                out.append("—" if (v is None or (isinstance(v, float) and pd.isna(v))) else str(v))
        disp[label] = out

    comp = pd.DataFrame(disp, index=headers).T  # index=指標, columns=銘柄
    comp.columns = headers

    # 各数値行のベスト値を太字ハイライト
    best_cells = {}   # (row_label, col_idx)
    for label, _, good in specs:
        if good is None:
            continue
        nums = [(i, v) for i, v in enumerate(raw[label]) if v is not None and not (isinstance(v, float) and pd.isna(v))]
        if not nums:
            continue
        bi = (max if good == "high" else min)(nums, key=lambda x: x[1])[0]
        best_cells[label] = bi

    def hl(row):
        styles = []
        bi = best_cells.get(row.name)
        for i in range(len(row)):
            styles.append("background-color:#fff7cc;font-weight:700" if (bi is not None and i == bi) else "")
        return styles

    use_rows = {"主用途", "タイプ"}

    def color_use(row):
        if row.name not in use_rows:
            return [""] * len(row)
        out = []
        for c in codes:
            key = rows[c]["use_primary"] if row.name == "主用途" else None
            if row.name == "タイプ":
                t = rows[c]["type_jp"]
                if any(k in t for k in ("総合", "複合", "統合")):
                    key = "その他"
                else:
                    key = next((ja for ja in ["オフィス", "ホテル", "物流", "商業", "住居", "ヘルスケア"] if ja in t), "その他")
            bgfg = ASSET_STYLE.get(key, ("", ""))
            out.append(f"background-color:{bgfg[0]};color:{bgfg[1]};font-weight:600" if bgfg[0] else "")
        return out

    styled = comp.style.apply(hl, axis=1).apply(color_use, axis=1)
    st.dataframe(styled, use_container_width=True, height=460)
    st.caption("黄色 = その指標のベスト値（利回り/物件数/リーマン比は高い方、NAV倍率/乖離は低い方）。")

    # 用途構成を並べて表示
    st.markdown("**用途別構成**")
    pcols = st.columns(len(codes))
    for col, c in zip(pcols, codes):
        with col:
            st.caption(f"{c} {rows[c]['name']}")
            amap = {ja: float(rows[c][k]) for k, ja in ASSET_COLS.items()
                    if pd.notna(rows[c].get(k)) and rows[c].get(k)}
            if amap:
                donut_chart(amap, height=200)
            else:
                st.caption("構成データなし")


# ===========================================================================
# 💼 マイポートフォリオ
# ===========================================================================
def render_portfolio(df, divs):
    st.subheader("💼 マイポートフォリオ")
    st.caption("保有銘柄（コード・口数・取得単価）を入力すると、全体の利回り・分配金見込み・含み益・用途構成を集計します。")

    _, l2c, c2l = label_maps(df)
    if "pf" not in st.session_state:
        st.session_state["pf"] = pd.DataFrame(
            {"コード": ["8985", "8960"], "口数": [1, 1], "取得単価": [np.nan, np.nan]})
    edited = st.data_editor(
        st.session_state["pf"], num_rows="dynamic", use_container_width=True, key="pf_editor",
        column_config={
            "コード": st.column_config.TextColumn("コード", help="4桁の証券コード", required=True),
            "口数": st.column_config.NumberColumn("口数", min_value=0, step=1, default=1),
            "取得単価": st.column_config.NumberColumn("取得単価(円/口)", help="含み益の計算用。空欄可", min_value=0),
        })

    holds = []
    for _, r in edited.iterrows():
        code = str(r["コード"]).strip()
        if code not in c2l:
            continue
        rec = df[df["code"] == code].iloc[0]
        units = float(r["口数"]) if pd.notna(r["口数"]) else 0.0
        price = float(rec["latest_price"]) if pd.notna(rec["latest_price"]) else None
        cost = float(r["取得単価"]) if pd.notna(r["取得単価"]) else None
        annual_pu, excess_pu = annual_distribution(divs, code)
        mval = price * units if price is not None else None
        holds.append({
            "code": code, "name": rec["name"], "units": units, "price": price,
            "cost": cost, "value": mval,
            "acq": (cost * units) if cost is not None else None,
            "gain": ((price - cost) * units) if (price is not None and cost is not None) else None,
            "annual_income": (annual_pu * units) if annual_pu is not None else None,
            "annual_excess": (excess_pu * units) if excess_pu is not None else None,
            "yield": rec["yield_total"], "use_primary": rec["use_primary"],
            "asset": {k: rec.get(k) for k in ASSET_COLS},
        })
    if not holds:
        st.info("有効な保有銘柄がありません（コードがDBに無い等）。")
        return

    tot_val = sum(h["value"] for h in holds if h["value"] is not None)
    tot_acq = sum(h["acq"] for h in holds if h["acq"] is not None)
    tot_gain = sum(h["gain"] for h in holds if h["gain"] is not None)
    has_cost = any(h["gain"] is not None for h in holds)
    tot_income = sum(h["annual_income"] for h in holds if h["annual_income"] is not None)
    tot_excess = sum(h["annual_excess"] for h in holds if h["annual_excess"] is not None)
    pf_yield = (tot_income / tot_val * 100) if tot_val else None

    m = st.columns(4)
    m[0].metric("評価額合計", fmt(tot_val, 0, " 円"))
    m[1].metric("ポートフォリオ利回り", fmt(pf_yield, 2, "%"), help="年間分配金合計 ÷ 評価額合計（実績ベース）")
    m[2].metric("年間分配金（見込み）", fmt(tot_income, 0, " 円"), help="直近2期＝1年の実績を据え置いた推定")
    m[3].metric("含み益", fmt(tot_gain, 0, " 円") if has_cost else "—",
                f"取得額 {fmt(tot_acq,0)} 円" if has_cost else "取得単価未入力",
                delta_color="normal")
    if tot_income:
        st.caption(f"うち利益超過分配（年間・推定）: {fmt(tot_excess,0)} 円"
                   f"（分配金の {tot_excess / tot_income * 100:.1f}%）")

    # 分配金の累計見込み（直近実績を据え置いた推定）
    st.markdown("**分配金 累計見込み（推定）**")
    today = dt.date.today()
    eoy = dt.date(today.year, 12, 31)
    days_left = (eoy - today).days
    cum_eoy = tot_income * (days_left / 365.0)           # 今年の残り期間ぶん
    sched = pd.DataFrame({
        "時点": [f"{today:%Y-%m-%d}（本日）", f"{today.year}年末",
                f"{today.year + 1}年末", f"{today.year + 2}年末"],
        "累計分配金(円)": [0.0, cum_eoy, cum_eoy + tot_income, cum_eoy + tot_income * 2],
    })
    sched["累計分配金(円)"] = sched["累計分配金(円)"].map(lambda v: f"{v:,.0f}")
    st.dataframe(sched, use_container_width=True, hide_index=True)
    st.caption("※ 本日を基準（0円）に、直近実績の年間分配金が今後も継続すると仮定した推定値です。")

    # 用途構成（評価額加重）
    cL, cR = st.columns([1, 1])
    with cL:
        st.markdown("**運用物件タイプ（評価額加重）**")
        agg = {ja: 0.0 for ja in ASSET_COLS.values()}
        wsum = 0.0
        for h in holds:
            if h["value"] is None:
                continue
            for k, ja in ASSET_COLS.items():
                v = h["asset"].get(k)
                if pd.notna(v):
                    agg[ja] += float(v) / 100.0 * h["value"]
            wsum += h["value"]
        amap = {k: v / wsum * 100 for k, v in agg.items() if wsum and v > 0}
        if amap:
            donut_chart(amap, height=260)
            st.caption("　".join(f"{k} {v:.1f}%" for k, v in sorted(amap.items(), key=lambda x: -x[1])))
        else:
            st.caption("構成データなし")
    with cR:
        st.markdown("**保有明細**")
        det = pd.DataFrame([{
            "コード": h["code"], "名称": h["name"], "口数": int(h["units"]),
            "評価額": fmt(h["value"], 0), "含み益": fmt(h["gain"], 0) if h["gain"] is not None else "—",
            "年間分配金": fmt(h["annual_income"], 0),
        } for h in holds])
        st.dataframe(det, use_container_width=True, hide_index=True)


# ===========================================================================
# エントリ
# ===========================================================================
def main():
    st.title("🏢 J-REIT 分析ダッシュボード")
    if not DB.exists():
        st.error("data/jreit.db がありません。"); return
    df, divs, runs, reits = build_frame()
    if df is None:
        st.warning("データが空です。"); return
    if runs is not None and not runs.empty:
        last = runs.sort_values("finished_at").iloc[-1]
        st.caption(f"最終更新 {last.get('finished_at','?')} ・ 銘柄 {len(reits)} ・ キャッシュ参照のみ")

    page = st.radio("画面", ["📋 ダッシュボード", "⚖️ 銘柄比較", "💼 マイポートフォリオ"],
                    horizontal=True, label_visibility="collapsed")
    st.divider()
    if page == "📋 ダッシュボード":
        render_dashboard(df, divs)
    elif page == "⚖️ 銘柄比較":
        render_comparison(df, divs)
    else:
        render_portfolio(df, divs)


if __name__ == "__main__":
    main()
