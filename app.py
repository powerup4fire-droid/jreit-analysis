"""J-REIT 分析ダッシュボード（read-only / cached-first）。
SQLite(data/jreit.db) のみ参照。スマホ/PC両対応。起動: streamlit run app.py

画面（上部の切替ボタン）:
  📋 ダッシュボード … サマリ一覧 + 個別銘柄（サマリで選んだ行が自動で個別に反映）
  ⚖️ 銘柄比較      … 複数銘柄を横並びでスペック比較（Apple compare 風）
  💼 マイポートフォリオ … 保有銘柄から全体の利回り・分配金見込み・含み益・用途構成を集計
"""
from __future__ import annotations
import datetime as dt
import hashlib
import json
import re
import sqlite3
from pathlib import Path
import numpy as np
import pandas as pd
import streamlit as st
import altair as alt   # Streamlit同梱（追加インストール不要）
import cloud_store      # Cloudflare KV 永続化（ログインユーザー単位・端末間同期）

DB = Path(__file__).resolve().parent / "data" / "jreit.db"
ASSET_COLS = {
    "asset_office": "オフィス", "asset_residential": "住居", "asset_logistics": "物流",
    "asset_retail": "商業", "asset_hotel": "ホテル", "asset_healthcare": "ヘルスケア",
    "asset_other": "その他",
}
# 用途カラー（サマリのセル・円グラフで共通。文字は全て黒のため、黒が読める明度に統一）
ASSET_COLOR = {
    "オフィス": "#7cb87c", "住居": "#b9cf66", "物流": "#8fc1e3",
    "商業": "#f0c14b", "ホテル": "#ef9a9a", "ヘルスケア": "#a6b8e6",
    "底地": "#c9a063", "その他": "#cfd4d8",
}
# 「その他(asset_other)」の実際の内訳が判っている銘柄: code -> 用途名
# （portfolio.json は g1=6分類のため、底地/ヘルスケア等は その他 に入る。overrides.json の "other_as" でも上書き可）
OTHER_AS = {"2971": "底地", "8977": "底地", "3249": "底地", "3455": "ヘルスケア"}
FONT = "#111111"   # フォントは全て黒で統一
# 後方互換: (bg, fg) 形式で参照する箇所向け（fgは常に黒）
ASSET_STYLE = {k: (v, FONT) for k, v in ASSET_COLOR.items()}
# 円グラフもサマリと同じ用途カラーに統一
PIE_COLORS = ASSET_COLOR
# reit_type 内の英語表記 → 日本語
TYPE_JP = {"office": "オフィス", "residential": "住居", "logistics": "物流",
           "retail": "商業", "hotel": "ホテル", "healthcare": "ヘルスケア"}
MA_KEYS = ["ma_25d", "ma_75d", "ma_200d", "ma_75w", "ma_200w", "ma_75m", "ma_200m"]

st.set_page_config(page_title="J-REIT 分析", page_icon="🏢", layout="wide",
                   initial_sidebar_state="collapsed")

st.markdown(
    """<style>
    /* 上部の Streamlit ヘッダ（Share/star/edit/GitHub/⋮）を非表示にして余白も詰める */
    header[data-testid="stHeader"]{display:none !important;}
    [data-testid="stToolbar"]{display:none !important;}
    [data-testid="stDecoration"]{display:none !important;}
    .block-container, [data-testid="stMainBlockContainer"]{padding-top:1.2rem !important;}
    /* multiselect のタグ（チップ）背景を白に統一 */
    span[data-baseweb="tag"]{background-color:#ffffff !important;border:1px solid #cbd5e1 !important;}
    span[data-baseweb="tag"] span{color:#1f2937 !important;}
    span[data-baseweb="tag"] svg{fill:#64748b !important;}
    /* ヘッダ周りの余白を引き締める */
    h1{margin-bottom:.1rem !important;padding-top:0 !important;letter-spacing:.5px;}
    hr{margin:.5rem 0 1.1rem !important;}
    /* 画面切替（segmented control）を見やすく */
    div[data-testid="stButtonGroup"]{margin-top:.2rem;}
    div[data-testid="stButtonGroup"] button{font-weight:700;}
    /* サブヘッダの上余白を少し詰める */
    h2, h3{margin-top:.4rem !important;}
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
    except Exception:           # テーブル未作成（古いDB）等は空で返す
        return pd.DataFrame()
    finally:
        con.close()


# 単位 → 直近の取引日数（営業日換算。200日=直近200営業日でMA200と整合）
UNIT_ROWS = {"日": 1, "週": 5, "月": 21, "年": 252}


@st.cache_data(ttl=120)
def deviation_series(stat: str, window_rows: int) -> pd.Series:
    """価格 vs 直近 window_rows 営業日の平均/中央値 の乖離率(%)。code をindexに返す。"""
    ph = load("price_history")
    if ph.empty:
        return pd.Series(dtype=float)
    ph = ph.dropna(subset=["close"]).sort_values(["code", "date"])

    def calc(g):
        w = g["close"].tail(int(window_rows))
        base = w.mean() if stat == "平均" else w.median()
        latest = g["close"].iloc[-1]
        return (latest - base) / base * 100 if base else np.nan

    return ph.groupby("code", group_keys=False).apply(calc)


FUND_FIELDS = ["unrealized_gain", "unrealized_gain_pct", "ltv_pct", "noi", "noi_yield_pct",
               "appraisal_value", "book_value", "total_assets", "fiscal_period"]


def oku(v, dec=0, suf="億円"):
    """円 → 億円表示。"""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    return f"{v / 1e8:,.{dec}f}{suf}"


def fmt(v, dec=0, suf=""):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    return f"{v:,.{dec}f}{suf}"


def fmt_goshya(v, suf=""):
    """小数点1桁・五捨六入（5以下切り捨て、6以上切り上げ）。"""
    import math
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    sign = -1 if v < 0 else 1
    r = sign * math.floor(abs(v) * 10 + 0.4) / 10
    return f"{r:.1f}{suf}"


def jp_type(t) -> str:
    s = str(t or "")
    for en, ja in TYPE_JP.items():
        s = s.replace(en, ja)
    return s or "—"


def asset_map(row) -> dict:
    """用途別比率 {用途: %}。判明している銘柄は『その他』を実内訳(底地/ヘルスケア等)へ振替える。"""
    m = {ja: row.get(col) for col, ja in ASSET_COLS.items()}
    m = {k: float(v) for k, v in m.items() if pd.notna(v) and float(v) > 0}
    oa = row.get("_other_as")
    if pd.notna(oa) and isinstance(oa, str) and oa and "その他" in m:   # NaNは真扱いになるため明示ガード
        m[oa] = m.get(oa, 0.0) + m.pop("その他")
    return m


def derive_type(row) -> str:
    """タイプを判定。明示ラベル(総合/複合/特化)は信頼してそのまま、
    キーワード誤マッチの bare 値は資産内訳から再判定（例: 3249 office→複合）。"""
    rt = str(row.get("reit_type") or "")
    if "総合" in rt:
        return "総合"
    if "複合" in rt:
        return "複合"
    if "特化" in rt:
        return jp_type(rt)
    assets = asset_map(row)
    if not assets:
        return jp_type(rt) if rt else "—"
    nonother = {k: v for k, v in assets.items() if k != "その他"}
    sig = [k for k, v in assets.items() if v >= 10]
    if nonother:
        nk = max(nonother, key=nonother.get)
        if nonother[nk] >= 80:
            return f"{nk}特化"
    if len(sig) >= 3:
        return "総合"
    if len(sig) == 2:
        return "複合"
    if len(sig) == 1:
        return f"{sig[0]}特化" if sig[0] != "その他" else "その他"
    return "—"


def is_infra(df: pd.DataFrame) -> pd.Series:
    """インフラファンド判定（名前パターン or コード 928x）。"""
    name = df["name"].fillna("")
    return name.str.contains("インフラ|再生可能|ソーラー|エネルギー") | df["code"].astype(str).str.startswith("928")


def use_info(row) -> tuple[str, str, list[str]]:
    """(色用の主用途1つ, 表示ラベル, 併記用途リスト) を返す。
    - その他は主用途に使わない。ヘルスケアは『ヘルスケア』表記。
    - 最大用途を採用。トップとの差が5%以内の用途は併記（例: オフィス・住居）。
    - 併記リストは主用途セルのグラデーション着色に使う。"""
    rt, nm = str(row.get("reit_type") or ""), str(row.get("name") or "")
    if "healthcare" in rt or "ヘルスケア" in rt or "ヘルスケア" in nm:
        return "ヘルスケア", "ヘルスケア", ["ヘルスケア"]
    pcts = {k: v for k, v in asset_map(row).items() if k != "その他"}  # 底地等は主用途候補に含む
    if not pcts:
        lbl = jp_type(rt)
        u = lbl if lbl != "—" else "その他"
        return "その他", u, [u]
    ranked = sorted(pcts.items(), key=lambda x: -x[1])
    top = ranked[0][1]
    near = [k for k, v in ranked if top - v <= 5.0]
    return ranked[0][0], "・".join(near), near


def period_key(label):
    m = re.match(r"(\d{4})年(\d{1,2})月期", str(label))
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


# ---------------------------------------------------------------------------
# 共通: データフレーム整形
# ---------------------------------------------------------------------------
def load_overrides() -> dict:
    """data/overrides.json で reits 列を銘柄ごとに手動上書き（スポンサー変更有無など）。"""
    p = DB.parent / "overrides.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return {k: v for k, v in data.items() if k.isdigit() and isinstance(v, dict)}
    except Exception:
        return {}


def build_frame():
    reits, metrics, divs, runs = load("reits"), load("stock_metrics"), load("dividends"), load("scrape_runs")
    if reits.empty:
        return None, None, None, reits
    df = reits.merge(metrics, on="code", how="left", suffixes=("", "_m"))

    # 手動上書き（overrides.json）を反映 — スクレイプ値より優先
    ov = load_overrides()
    if ov:
        df = df.set_index("code")
        for code, fields in ov.items():
            if code in df.index:
                for k, val in fields.items():
                    if k in df.columns:
                        df.at[code, k] = val
        df = df.reset_index()

    # EDINET由来ファンダ（含み損益/LTV/NOI 等）。未取込・古いDBでも安全に NULL で継続。
    fund = load("fundamentals")
    if not fund.empty:
        keep = ["code"] + [c for c in FUND_FIELDS if c in fund.columns]
        ren = fund[keep].copy()
        if "parse_status" in fund.columns:
            ren["fund_status"] = fund["parse_status"]
        df = df.merge(ren, on="code", how="left")
    else:
        for c in FUND_FIELDS:
            df[c] = np.nan
        df["fund_status"] = None

    # 「その他」の実内訳マップ（定数 + overrides.json の "other_as"）。use_info より前に必要。
    other_as = dict(OTHER_AS)
    for code, fields in ov.items():
        if isinstance(fields, dict) and fields.get("other_as"):
            other_as[code] = fields["other_as"]
    df["_other_as"] = df["code"].map(other_as)

    uinfo = df.apply(use_info, axis=1)
    df["use_primary"] = uinfo.map(lambda x: x[0])
    df["use_label"] = uinfo.map(lambda x: x[1])
    df["use_types"] = uinfo.map(lambda x: x[2])
    df["type_jp"] = df.apply(derive_type, axis=1)
    df["mktcap_oku"] = df["market_cap"] / 1e8
    df["dev_200d_pct"] = np.where(df["ma_200d"].notna() & df["latest_price"].notna() & (df["ma_200d"] != 0),
                                  (df["latest_price"] - df["ma_200d"]) / df["ma_200d"] * 100, np.nan)

    def excess_ratio_window(code, n):
        """直近n期の「利益超過分が分配金に占める割合(%)」= Σ利益超過 / Σ分配金 ×100。
        平均値ベース（sum/sum は平均/平均と同値）。データ無し=None / 利益超過なし=0.0。"""
        d = divs[(divs["code"] == code) & (divs["period_label"] != "latest")]
        if d.empty:
            return None
        d = d.assign(k=d["period_label"].map(period_key)).sort_values("k", ascending=False).head(n)
        tot = d["total_distribution"].sum(skipna=True)
        exc = d["excess_distribution"].sum(skipna=True)
        if not tot or pd.isna(tot):
            return None
        return round(exc / tot * 100, 1) if pd.notna(exc) else 0.0

    df["exc6"] = df["code"].map(lambda c: excess_ratio_window(c, 6))
    df["exc10"] = df["code"].map(lambda c: excess_ratio_window(c, 10))
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
    投資口分割を自動検出（ウィンドウ内で前期比60%未満の急減）し、
    スプリット後のデータのみ抽出して決算頻度で年換算する。"""
    d = divs[(divs["code"] == code) & (divs["period_label"] != "latest")].copy()
    if d.empty:
        return None, None
    d["ym"] = d["period_label"].map(lambda l: (lambda k: k[0] * 12 + k[1])(period_key(l)))
    d = d[d["ym"] > 0].sort_values("ym").reset_index(drop=True)
    if d.empty:
        return None, None
    latest = d["ym"].max()
    win = d[latest - d["ym"] < 12].reset_index(drop=True)   # 直近12ヶ月
    if win.empty:
        return None, None

    # 投資口分割検出: ウィンドウ内で前期比60%未満に急減した場合はスプリット後インデックスを記録
    split_from = 0
    for i in range(1, len(win)):
        prev = win.loc[i - 1, "total_distribution"]
        curr = win.loc[i, "total_distribution"]
        if pd.notna(prev) and pd.notna(curr) and prev > 0 and curr / prev < 0.60:
            split_from = i

    if split_from > 0:
        post = win.iloc[split_from:].reset_index(drop=True)
        # 全履歴から決算頻度（期/年）を推定
        if len(d) >= 2:
            valid_ivl = d["ym"].diff().dropna()
            valid_ivl = valid_ivl[valid_ivl > 0]
            avg_interval = valid_ivl.mean() if len(valid_ivl) > 0 else 6
        else:
            avg_interval = 6
        periods_per_year = max(1, min(round(12 / avg_interval), 12))
        annual_factor = periods_per_year / len(post)
        tot = float(post["total_distribution"].sum(skipna=True)) * annual_factor
        exc_raw = post["excess_distribution"].sum(skipna=True)
        exc = float(exc_raw) * annual_factor if pd.notna(exc_raw) else 0.0
    else:
        tot = win["total_distribution"].sum(skipna=True)
        exc = win["excess_distribution"].sum(skipna=True)

    return (float(tot) if pd.notna(tot) else None,
            float(exc) if pd.notna(exc) else None)


# ===========================================================================
# 📋 ダッシュボード
# ===========================================================================
def render_dashboard(df, divs):
    st.subheader("📋 サマリ")
    uses = sorted(df["use_primary"].dropna().unique().tolist())
    c_sort, c_pick, c_avg = st.columns([1.1, 2.2, 1.5])
    with c_sort:
        sort_key = st.selectbox("並び替え", ["利回り%", "乖離%", "リーマン比%",
                                          "時価総額", "出来高", "コードNo"])
    with c_pick:
        pick = st.pills("主用途で絞り込み（クリックでON/OFF）", uses, selection_mode="multi",
                        default=uses)
        only_no_excess = st.checkbox("利益超過分配金なしのみ", value=False,
                                     help="直近10期で利益超過分配金が一度も無い銘柄だけ表示")
    with c_avg:
        ex = df[~is_infra(df)]
        avg = ex["yield_total"].mean()
        st.markdown(
            f'<div style="background:#eef3fb;border-radius:10px;padding:12px 16px;text-align:center;margin-top:26px">'
            f'<div style="font-size:13px;color:#111">📊 J-REIT全体 平均利回り<br>'
            f'<span style="font-size:11px;color:#555">（インフラファンド除く・{len(ex)}銘柄）</span></div>'
            f'<div style="font-size:1.7em;font-weight:700;color:#111;margin-top:2px">{avg:.2f}%</div></div>',
            unsafe_allow_html=True)
    if not pick:
        st.info("主用途を1つ以上選択してください（ボタンをクリックでON）。")
        return

    # 乖離率の基準（種類＝平均/中央値, 期間＝任意）＋ スポンサー逆引き検索
    d1, d2, d3, d4 = st.columns([1, 1, 1, 3])
    dev_stat = d1.selectbox("乖離の基準", ["平均", "中央値"],
                            help="価格と「直近◯期間の平均/中央値」の乖離率を表示します")
    dev_num = d2.number_input("期間", min_value=1, max_value=9999, value=200, step=1)
    dev_unit = d3.selectbox("単位", ["日", "週", "月", "年"])
    sponsor_q = d4.text_input("スポンサーで逆引き検索", placeholder="例: 三井不動産 / KKR / 三菱",
                              help="スポンサー名（部分一致）で銘柄を絞り込み")
    dev_rows = int(dev_num) * UNIT_ROWS[dev_unit]
    dev_ser = deviation_series(dev_stat, dev_rows)   # code -> 乖離%

    view = df[df["use_primary"].isin(pick)].copy()
    if only_no_excess:
        view = view[~(view["exc10"].fillna(-1) > 0)]   # 利益超過なし（0 or データ無し）のみ
    if sponsor_q.strip():
        q = sponsor_q.strip()
        sp_col = view["sponsor"] if "sponsor" in view.columns else pd.Series("", index=view.index)
        prev_col = view["sponsor_prev"] if "sponsor_prev" in view.columns else pd.Series("", index=view.index)
        hit = sp_col.fillna("").str.contains(q, case=False) | prev_col.fillna("").str.contains(q, case=False)
        view = view[hit]
        st.caption(f"🔎 スポンサー「{q}」に該当: {len(view)} 銘柄")
    view["dev_sel_pct"] = view["code"].map(dev_ser)

    def exc_disp(v):
        """利益超過が分配金に占める割合。あり=「X.X%」/ なし=「なし」/ データ無し=「—」。"""
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return "—"
        return f"{v:.1f}%" if v > 0 else "なし"

    def period_disp(v):
        return f"{int(v)}期" if pd.notna(v) else "—"

    def sponsor_disp(row):
        """変更があれば「変更前 → 現在」を併記。"""
        cur = row.get("sponsor")
        prev = row.get("sponsor_prev")
        cur = cur if (cur and pd.notna(cur)) else "—"
        if prev and pd.notna(prev) and str(prev) != str(cur):
            return f"{prev} → {cur}"
        return cur

    g = lambda c: view[c] if c in view.columns else pd.Series([None] * len(view), index=view.index)
    summary = pd.DataFrame({
        "コード": view["code"], "名称": view["name"], "主用途": view["use_label"],
        "タイプ": view["type_jp"], "上場期": g("period_no").map(period_disp),
        "利回り%": view["yield_total"].round(2),
        "価格": view["latest_price"], "出来高": view["volume"],
        "時価総額(億円)": view["mktcap_oku"].round(0),
        "スポンサー": view.apply(sponsor_disp, axis=1),
        "NAV倍率": view["nav_ratio"].round(2),
        "乖離%": view["dev_sel_pct"].round(1),
        "リーマン比%": view["lehman_ratio_pct"].round(1),
        "利益超過(6期)": view["exc6"].map(exc_disp), "利益超過(10期)": view["exc10"].map(exc_disp),
        "Jリート": view["code"],   # japan-reit 該当銘柄ページへのリンク用
        "_primary": view["use_primary"], "_types": view["use_types"],
        "_assets": view.apply(asset_map, axis=1),
    })
    sort_map = {"利回り%": ("利回り%", False), "乖離%": ("乖離%", True),
                "リーマン比%": ("リーマン比%", True), "時価総額": ("時価総額(億円)", False),
                "出来高": ("出来高", False), "コードNo": ("コード", True)}
    col, asc = sort_map[sort_key]
    summary = summary.sort_values(col, ascending=asc, na_position="last").reset_index(drop=True)
    primaries = summary.pop("_primary")
    types_list = summary.pop("_types")
    assets_list = summary.pop("_assets")

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

    def use_bg(assets):
        """主用途セルの背景。運用比率に応じてセル内を帯状に色分け（多い順に左→右、ハードストップ）。"""
        items = [(t, float(p)) for t, p in (assets or {}).items()
                 if pd.notna(p) and float(p) > 0 and t in ASSET_COLOR]
        if not items:
            return ASSET_COLOR["その他"]
        items.sort(key=lambda x: -x[1])
        total = sum(p for _, p in items) or 1.0
        cum = 0.0
        stops = []
        for t, p in items:
            a = cum / total * 100
            cum += p
            b = cum / total * 100
            stops.append(f"{ASSET_COLOR[t]} {a:.2f}% {b:.2f}%")   # 同色2点でハードな帯（グラデなし）
        return "linear-gradient(90deg, " + ", ".join(stops) + ")"

    # 数値列を文字列へ整形（NA は「—」）
    num_fmt = {
        "利回り%": "{:.2f}", "価格": "{:,.0f}", "出来高": "{:,.0f}",
        "時価総額(億円)": "{:,.0f}", "NAV倍率": "{:.2f}",
        "乖離%": "{:+.1f}", "リーマン比%": "{:.1f}",
    }
    for c, spec in num_fmt.items():
        summary[c] = summary[c].map(lambda v, s=spec: "—" if pd.isna(v) else s.format(v))

    # st.dataframe(canvas) は CSS グラデーション背景を描画できないため、HTMLテーブルで描画する。
    cols = list(summary.columns)
    right_cols = {"利回り%", "価格", "出来高", "時価総額(億円)", "NAV倍率",
                  "乖離%", "リーマン比%"}
    center_cols = {"タイプ", "上場期", "利益超過(6期)", "利益超過(10期)", "Jリート"}
    head = "".join(
        f'<th style="position:sticky;top:0;background:#eef1f4;color:{FONT};padding:7px 10px;'
        f'white-space:nowrap;border-bottom:2px solid #c8ccd0;text-align:center">{c}</th>'
        for c in cols)
    rows_html = []
    for i in range(len(summary)):
        r = summary.iloc[i]
        code_v = str(r["コード"])
        row_bg = "#f4f6f8" if i % 2 == 1 else "#ffffff"
        tds = []
        for c in cols:
            v = r[c]
            if c == "主用途":
                tds.append(
                    f'<td style="background:{use_bg(assets_list.iloc[i])};color:{FONT};font-weight:700;'
                    f'text-align:center;white-space:nowrap;padding:5px 12px;border-bottom:1px solid #eee">{v}</td>')
            elif c == "タイプ":
                tb, _ = type_style(v)
                tds.append(
                    f'<td style="background:{tb};color:{FONT};font-weight:700;text-align:center;'
                    f'white-space:nowrap;padding:5px 12px;border-bottom:1px solid #eee">{v}</td>')
            elif c == "コード":
                # クリックで下の「個別銘柄」に飛ぶ（?code= をセットして #detail へスクロール）
                tds.append(
                    f'<td style="background:{row_bg};text-align:left;white-space:nowrap;padding:5px 12px;border-bottom:1px solid #eee">'
                    f'<a href="?code={code_v}#detail" target="_self" '
                    f'style="color:#1f6feb;font-weight:700;text-decoration:none">{v}</a></td>')
            elif c == "Jリート":
                tds.append(
                    f'<td style="background:{row_bg};text-align:center;white-space:nowrap;padding:5px 12px;border-bottom:1px solid #eee">'
                    f'<a href="https://www.japan-reit.com/meigara/{code_v}/" target="_blank" '
                    f'rel="noopener" style="color:#1f6feb;text-decoration:none">japan-reit ↗</a></td>')
            else:
                align = "right" if c in right_cols else ("center" if c in center_cols else "left")
                tds.append(
                    f'<td style="background:{row_bg};color:{FONT};text-align:{align};white-space:nowrap;'
                    f'padding:5px 12px;border-bottom:1px solid #eee">{v}</td>')
        rows_html.append("<tr>" + "".join(tds) + "</tr>")
    table_html = (
        '<div style="max-height:460px;overflow:auto;border:1px solid #e0e0e0;border-radius:8px">'
        '<table style="border-collapse:collapse;font-size:13px;width:100%">'
        f'<thead><tr>{head}</tr></thead><tbody>{"".join(rows_html)}</tbody></table></div>')
    st.markdown(table_html, unsafe_allow_html=True)

    legend = "　".join(
        f'<span style="background:{c};color:{FONT};padding:2px 8px;border-radius:4px;font-size:12px">{k}</span>'
        for k, c in ASSET_COLOR.items())
    st.markdown("主用途セルは運用比率で色分け（多い順に左→右）: " + legend, unsafe_allow_html=True)
    st.caption(f"「乖離%」= 価格と直近{dev_num}{dev_unit}（{dev_rows}営業日）の{dev_stat}との乖離率")
    st.caption("※ スポンサー・上場期・変更有無は japan-reit.com からの自動取得（変更有無は説明文ベースの推定で見落とし得ます）")
    st.caption(f"表示 {len(summary)} / 全 {len(df)} 銘柄　／　詳細は下の「個別銘柄」で選択")

    # ===== 個別 =====
    st.markdown('<div id="detail"></div>', unsafe_allow_html=True)   # コード列クリックのスクロール先
    st.subheader("🔎 個別銘柄")
    labels, l2c, c2l = label_maps(df)
    # サマリのコードをクリックすると ?code= が付く → 該当銘柄を選択（消費して以後の手動選択を妨げない）
    qcode = st.query_params.get("code")
    if qcode:
        if qcode in c2l:
            st.session_state["detail_code"] = qcode
        del st.query_params["code"]
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
    per = row.get("period_no")
    sp = row.get("sponsor")
    prev = row.get("sponsor_prev")
    per_s = f"{int(per)}期" if pd.notna(per) else "—"
    sp_s = sp if (sp and pd.notna(sp)) else "—"
    if prev and pd.notna(prev) and str(prev) != str(sp_s):
        sp_s = f"{prev} → {sp_s}（スポンサー変更）"
    st.caption(f"上場期: {per_s}　／　スポンサー: {sp_s}")

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

    # EDINET由来（含み損益/LTV/NOI）。未取込なら「—」。
    m3 = st.columns(3)
    m3[0].metric("含み益率", fmt(row.get("unrealized_gain_pct"), 1, "%"))
    m3[1].metric("NOI利回り", fmt(row.get("noi_yield_pct"), 2, "%"))
    m3[2].metric("LTV（有利子負債比率）", fmt(row.get("ltv_pct"), 1, "%"))
    if pd.isna(row.get("unrealized_gain_pct")):
        st.caption("含み益率/NOI利回り/LTV は未取得です。")
    else:
        st.caption("含み益率・NOI利回り・LTV は japan-reit.com の最新ランキング値です。")

    cL, cR = st.columns(2)
    with cL:
        st.markdown("**用途別ポートフォリオ構成**")
        amap = asset_map(row)
        if amap:
            donut_chart(amap, inside_labels=True)
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


def donut_chart(amap: dict, height=300, legend=True, inside_labels=False):
    order = list(PIE_COLORS.keys())
    pdf = pd.DataFrame({"用途": list(amap.keys()), "比率": list(amap.values())})
    pdf["ラベル"] = pdf["比率"].map(lambda v: f"{v:.0f}%" if v >= 7 else "")  # 小さいスライスは省略
    # 外半径を高さ基準で固定（列幅が狭くてもリングが潰れない）。凡例ぶんの余白も確保。
    outer = max(40.0, height / 2 - 12)
    inner = outer * 0.55
    leg = alt.Legend(title=None) if legend else None
    base = alt.Chart(pdf).encode(
        theta=alt.Theta("比率:Q", stack=True),
        order=alt.Order("比率:Q", sort="descending"))
    arc = base.mark_arc(innerRadius=inner, outerRadius=outer).encode(
        color=alt.Color("用途:N",
                        scale=alt.Scale(domain=order, range=[PIE_COLORS[k] for k in order]),
                        legend=leg),
        tooltip=[alt.Tooltip("用途:N"), alt.Tooltip("比率:Q", format=".1f")])
    layers = [arc]
    if inside_labels:
        # 構成比率を着色エリア内に黒文字で表記
        txt = base.mark_text(radius=(inner + outer) / 2, fontSize=12, fontWeight="bold",
                             fill="#111111").encode(text=alt.Text("ラベル:N"))
        layers.append(txt)
    st.altair_chart(alt.layer(*layers).properties(height=height), use_container_width=True)


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
        ("含み益率 %", lambda c: rows[c].get("unrealized_gain_pct"), "high"),
        ("NOI利回り %", lambda c: rows[c].get("noi_yield_pct"), "high"),
        ("LTV %", lambda c: rows[c].get("ltv_pct"), "low"),
    ]

    headers = [f"{c} {rows[c]['name']}" for c in codes]
    raw = {}      # 数値（ハイライト判定用）
    disp = {}     # 表示文字列
    for label, acc, _ in specs:
        raw[label] = [acc(c) for c in codes]
    fmt_map = {"利回り %": 2, "価格 円": 0, "時価総額 億円": 0, "NAV倍率": 2, "物件数": 0,
               "200日乖離 %": 1, "6年平均乖離 %": 1, "リーマン比 %": 1, "年間分配金 円/口": 0,
               "含み益率 %": 1, "NOI利回り %": 2, "LTV %": 1}
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

    styled = comp.style.apply(hl, axis=1).apply(color_use, axis=1).set_properties(**{"color": FONT})
    st.dataframe(styled, use_container_width=True, height=520)
    st.caption("黄色 = その指標のベスト値（利回り/物件数/リーマン比は高い方、NAV倍率/乖離は低い方）。")

    # 用途構成を並べて表示（構成比率は円グラフ内に表記）
    st.markdown("**用途別構成**")
    pcols = st.columns(len(codes))
    for col, c in zip(pcols, codes):
        with col:
            st.caption(f"{c} {rows[c]['name']}")
            amap = asset_map(rows[c])
            if amap:
                donut_chart(amap, height=210, legend=False, inside_labels=True)
                top = sorted(amap.items(), key=lambda x: -x[1])[:3]
                st.caption("　".join(k for k, _ in top))   # 用途名（比率は円内に表示）
            else:
                st.caption("構成データなし")


# ===========================================================================
# 💼 マイポートフォリオ
# ===========================================================================
def parse_bulk(text: str) -> pd.DataFrame | None:
    """貼り付けCSV/TSV → [コード,口数,取得単価]。区切りはカンマ/タブ。ヘッダ行や順序差は吸収。"""
    rows = []
    for line in str(text).strip().splitlines():
        cells = [c.strip() for c in re.split(r"[\t,]", line.strip())]
        if not any(cells):
            continue
        codes = [c for c in cells if re.fullmatch(r"\d{4}", c)]
        if not codes:           # コード4桁が無い行（ヘッダ等）はスキップ
            continue
        code = codes[0]
        rest = [c for c in cells if c != code]
        nums = []
        for c in rest:
            cc = re.sub(r"[^\d.]", "", c)
            nums.append(float(cc) if cc not in ("", ".") else None)
        units = nums[0] if len(nums) >= 1 and nums[0] else 1.0
        cost = nums[1] if len(nums) >= 2 else None
        rows.append({"コード": code, "口数": units, "取得単価": cost})
    return pd.DataFrame(rows) if rows else None


def sheets_csv_url(url: str) -> str:
    """Google Sheets の編集/共有URLを CSV エクスポートURLへ変換（公開シート向け）。"""
    m = re.search(r"/spreadsheets/d/([\w-]+)", url)
    if not m:
        return url
    sid = m.group(1)
    g = re.search(r"[#&?]gid=(\d+)", url)
    gid = g.group(1) if g else "0"
    return f"https://docs.google.com/spreadsheets/d/{sid}/export?format=csv&gid={gid}"


def render_portfolio(df, divs):
    st.subheader("💼 マイポートフォリオ")
    st.caption("保有銘柄（コード・口数・取得単価）を入力すると、全体の利回り・分配金見込み・含み益・用途構成を集計します。")

    _, l2c, c2l = label_maps(df)
    PF_COLS = ["コード", "口数", "取得単価"]
    if "pf" not in st.session_state:
        loaded = None
        # ログイン中のユーザーが居る時だけKVを参照（未ログイン時に共有キーを使わない安全策）
        if cloud_store.enabled() and cloud_store.current_user():
            try:
                loaded = cloud_store.load()      # 保存済みデータ / [] / None
            except Exception as e:  # noqa
                st.warning(f"クラウド読込に失敗（セッションのみで継続）: {e}")
        if loaded:                                # 保存済みデータあり
            pf = pd.DataFrame(loaded)
            for c in PF_COLS:
                if c not in pf.columns:
                    pf[c] = np.nan
            st.session_state["pf"] = pf[PF_COLS]
        elif loaded == []:                        # クラウド有効・初回（空）
            st.session_state["pf"] = pd.DataFrame(
                {"コード": pd.Series(dtype=str),
                 "口数": pd.Series(dtype=float),
                 "取得単価": pd.Series(dtype=float)})
        else:                                     # クラウド無効（ローカル等）→ 従来の例示
            st.session_state["pf"] = pd.DataFrame(
                {"コード": ["8985", "8960"], "口数": [1, 1], "取得単価": [np.nan, np.nan]})
    st.session_state.setdefault("pf_ver", 0)

    # 保存状態の表示
    if cloud_store.enabled():
        _u = cloud_store.current_user()
        if _u and _u != "solo":
            st.caption(f"☁️ クラウド保存: 有効（{_u} 専用・端末間で自動同期）")
        elif _u == "solo":
            st.caption("☁️ クラウド保存: 有効（単独利用モード・全デバイスで自動同期）")
        else:
            st.caption("☁️ クラウド保存: ログイン待ち（Googleログインすると有効・現在はこのセッションのみ）")
    else:
        st.caption("💾 保存先未設定（このセッションのみ・タブを閉じると消えます）")

    with st.expander("📥 一括入力（CSV貼り付け / ファイル / Google Sheets）"):
        st.caption("形式: 各行「コード, 口数, 取得単価」。ExcelやGoogleスプレッドシートからコピペ可（タブ区切りも可）。ヘッダ行・列順の違いは自動調整。取得単価は空欄可。")
        paste = st.text_area("① 貼り付け（CSV/TSV）", height=120,
                             placeholder="8985, 2, 70000\n8960, 1, 158000\n3492, 5")
        up = st.file_uploader("② CSVファイル", type=["csv", "tsv", "txt"])
        gs = st.text_input("③ Google スプレッドシートURL（共有/公開のもの）",
                           placeholder="https://docs.google.com/spreadsheets/d/.../edit#gid=0")
        mode = st.radio("反映方法", ["置き換え", "追加"], horizontal=True, index=0)
        if st.button("読み込む", type="primary"):
            new = None
            try:
                if paste.strip():
                    new = parse_bulk(paste)
                elif up is not None:
                    new = parse_bulk(up.getvalue().decode("utf-8", "ignore"))
                elif gs.strip():
                    new = parse_bulk(pd.read_csv(sheets_csv_url(gs)).to_csv(index=False))
                else:
                    st.warning("いずれかに入力してください。")
            except Exception as e:  # noqa
                st.error(f"読み込み失敗: {e}")
            if new is not None and not new.empty:
                if mode == "追加":
                    new = pd.concat([st.session_state["pf"], new], ignore_index=True)
                st.session_state["pf"] = new.reset_index(drop=True)
                st.session_state["pf_ver"] += 1
                st.success(f"{len(new)} 行を読み込みました。")
                st.rerun()
            elif new is not None:
                st.warning("有効な銘柄行が見つかりませんでした（4桁コードを含む行が必要）。")

    edited = st.data_editor(
        st.session_state["pf"], num_rows="dynamic", use_container_width=True,
        key=f"pf_editor_{st.session_state['pf_ver']}",
        column_config={
            "コード": st.column_config.TextColumn("コード", help="4桁の証券コード", required=True),
            "口数": st.column_config.NumberColumn("口数", min_value=0, step=1, default=1),
            "取得単価": st.column_config.NumberColumn("取得単価(円/口)", help="含み益の計算用。空欄可", min_value=0),
        })

    # 変更を検知してクラウドへ自動保存（書込回数を抑えるため内容ハッシュで差分判定）
    if cloud_store.enabled() and cloud_store.current_user():
        rows = json.loads(edited.to_json(orient="records"))  # NaN→null に正規化
        h = hashlib.md5(json.dumps(rows, sort_keys=True).encode()).hexdigest()
        if st.session_state.get("pf_saved_hash") != h:
            try:
                cloud_store.save(rows)
                st.session_state["pf_saved_hash"] = h
            except Exception as e:  # noqa
                st.warning(f"クラウド保存に失敗: {e}")

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
        # 利益超過分配金を除く基本分配金（利回り計算はこちらベース）
        base_pu = None
        if annual_pu is not None:
            base_pu = annual_pu - (excess_pu or 0.0)
        mval = price * units if price is not None else None
        holds.append({
            "code": code, "name": rec["name"], "units": units, "price": price,
            "cost": cost, "value": mval,
            "acq": (cost * units) if cost is not None else None,
            "gain": ((price - cost) * units) if (price is not None and cost is not None) else None,
            "gain_pct": ((price - cost) / cost * 100) if (price is not None and cost is not None and cost > 0) else None,
            "annual_pu": annual_pu,
            "excess_pu": excess_pu,
            "base_pu": base_pu,                                               # 利益超過除き
            "base_income": (base_pu * units) if base_pu is not None else None,  # 利益超過除き×口数
            "annual_income": (annual_pu * units) if annual_pu is not None else None,
            "annual_excess": (excess_pu * units) if excess_pu is not None else None,
            "yield_on_cost": (base_pu / cost * 100) if (base_pu and cost) else None,
            "yield_on_value": (base_pu / price * 100) if (base_pu and price) else None,
            "yield": rec["yield_total"], "use_primary": rec["use_primary"],
            "asset_pct": asset_map(rec),
            "fund_ug_pct": rec.get("unrealized_gain_pct"),
        })
    if not holds:
        st.info("有効な保有銘柄がありません（コードがDBに無い等）。")
        return

    tot_val = sum(h["value"] for h in holds if h["value"] is not None)
    tot_acq = sum(h["acq"] for h in holds if h["acq"] is not None)
    tot_gain = sum(h["gain"] for h in holds if h["gain"] is not None)
    has_cost = any(h["gain"] is not None for h in holds)
    tot_income = sum(h["base_income"] for h in holds if h["base_income"] is not None)  # 利益超過除き
    tot_excess = sum(h["annual_excess"] for h in holds if h["annual_excess"] is not None)
    pf_yield = (tot_income / tot_val * 100) if tot_val else None
    pf_yield_on_cost = (tot_income / tot_acq * 100) if (tot_acq and tot_income) else None

    m = st.columns(5)
    m[0].metric("評価額合計", fmt(tot_val, 0, " 円"))
    m[1].metric("評価額ベース利回り", fmt(pf_yield, 2, "%"), help="年間分配金（利益超過除く）÷ 評価額合計")
    m[2].metric("取得価格ベース利回り", fmt(pf_yield_on_cost, 2, "%"), help="年間分配金（利益超過除く）÷ 取得額合計")
    m[3].metric("年間分配金（利益超過除く）", fmt(tot_income, 0, " 円"), help="直近実績の利益超過分配金を除く年間分配金")
    m[4].metric("含み益", fmt(tot_gain, 0, " 円") if has_cost else "—",
                f"取得額 {fmt(tot_acq,0)} 円" if has_cost else "取得単価未入力",
                delta_color="normal")
    if tot_excess:
        tot_total = sum(h["annual_income"] for h in holds if h["annual_income"] is not None)
        st.caption(f"利益超過分配（年間・推定）: {fmt(tot_excess,0)} 円"
                   f"（分配金合計 {fmt(tot_total,0)} 円 の {tot_excess / tot_total * 100:.1f}%）")

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

    # 用途構成（評価額加重 + 分配金加重）の2種グラフ
    st.markdown("**運用物件タイプ**")
    # 評価額ベース
    agg_val = {}
    wsum_val = 0.0
    for h in holds:
        if h["value"] is None:
            continue
        for ja, v in h["asset_pct"].items():
            agg_val[ja] = agg_val.get(ja, 0.0) + float(v) / 100.0 * h["value"]
        wsum_val += h["value"]
    amap_val = {k: v / wsum_val * 100 for k, v in agg_val.items() if wsum_val and v > 0}
    # 分配金ベース（利益超過除く）
    agg_dist = {}
    wsum_dist = 0.0
    for h in holds:
        if h["base_income"] is None:
            continue
        for ja, v in h["asset_pct"].items():
            agg_dist[ja] = agg_dist.get(ja, 0.0) + float(v) / 100.0 * h["base_income"]
        wsum_dist += h["base_income"]
    amap_dist = {k: v / wsum_dist * 100 for k, v in agg_dist.items() if wsum_dist and v > 0}

    gc1, gc2 = st.columns(2)
    with gc1:
        st.caption("評価額ベース")
        if amap_val:
            donut_chart(amap_val, height=220, inside_labels=True)
            st.caption("　".join(f"{k} {v:.1f}%" for k, v in sorted(amap_val.items(), key=lambda x: -x[1])))
        else:
            st.caption("構成データなし")
    with gc2:
        st.caption("分配金ベース（利益超過分配金除く）")
        if amap_dist:
            donut_chart(amap_dist, height=220, inside_labels=True)
            st.caption("　".join(f"{k} {v:.1f}%" for k, v in sorted(amap_dist.items(), key=lambda x: -x[1])))
        else:
            st.caption("構成データなし")

    # 保有明細（HTMLテーブル → 並び替え後も交互行着色が崩れない）
    st.markdown("**保有明細**")
    det_rows = [{
        "コード": h["code"], "名称": h["name"], "口数": fmt(h["units"], 0),
        "評価額(円)": fmt(h["value"], 0),
        "評価損益率": fmt_goshya(h["gain_pct"], "%") if h["gain_pct"] is not None else "—",
        "評価損益(円)": fmt(h["gain"], 0) if h["gain"] is not None else "—",
        "年間分配金(円/口)※": fmt(h["base_pu"], 0) if h["base_pu"] is not None else "—",
        "年間分配金合計(円)": fmt(h["base_income"], 0) if h["base_income"] is not None else "—",
        "取得利回り": fmt(h["yield_on_cost"], 2, "%") if h["yield_on_cost"] is not None else "—",
        "評価利回り": fmt(h["yield_on_value"], 2, "%") if h["yield_on_value"] is not None else "—",
        "含み益率(ファンド)": fmt(h["fund_ug_pct"], 1, "%"),
    } for h in holds]
    if det_rows:
        det_cols = list(det_rows[0].keys())
        det_right = {"口数", "評価額(円)", "評価損益(円)", "年間分配金(円/口)※", "年間分配金合計(円)"}
        det_center = {"取得利回り", "評価利回り", "評価損益率", "含み益率(ファンド)"}
        det_head = "".join(
            f'<th style="position:sticky;top:0;background:#eef1f4;color:{FONT};padding:7px 10px;'
            f'white-space:nowrap;border-bottom:2px solid #c8ccd0;text-align:center">{c}</th>'
            for c in det_cols)
        det_html_rows = []
        for pos, row in enumerate(det_rows):
            row_bg = "#f0f0f0" if pos % 2 == 1 else "#ffffff"
            tds = []
            for c in det_cols:
                v = row[c]
                align = "right" if c in det_right else ("center" if c in det_center else "left")
                tds.append(
                    f'<td style="background:{row_bg};color:{FONT};text-align:{align};white-space:nowrap;'
                    f'padding:5px 12px;border-bottom:1px solid #eee">{v}</td>')
            det_html_rows.append("<tr>" + "".join(tds) + "</tr>")
        det_table_html = (
            '<div style="max-height:420px;overflow:auto;border:1px solid #e0e0e0;border-radius:8px">'
            '<table style="border-collapse:collapse;font-size:13px;width:100%">'
            f'<thead><tr>{det_head}</tr></thead><tbody>{"".join(det_html_rows)}</tbody></table></div>')
        st.markdown(det_table_html, unsafe_allow_html=True)
    st.caption("※ 年間分配金は直近12ヶ月の実績（利益超過分配金を除く）。投資口分割を自動検出してスプリット後の期を年換算。取得/評価利回りも同ベース。")


# ===========================================================================
# エントリ
# ===========================================================================
def main():
    if not DB.exists():
        st.title("🏢 J-REIT 分析ダッシュボード")
        st.error("data/jreit.db がありません。"); return
    df, divs, runs, reits = build_frame()
    if df is None:
        st.title("🏢 J-REIT 分析ダッシュボード")
        st.warning("データが空です。"); return

    ts = "—"
    if runs is not None and not runs.empty:
        ts = runs.sort_values("finished_at").iloc[-1].get("finished_at", "—")
    # タイトル＋メタ情報を1行のヘッダにまとめて余白を最適化
    st.markdown(
        '<div style="display:flex;align-items:baseline;gap:16px;flex-wrap:wrap;'
        'margin:0 0 6px">'
        '<span style="font-size:2rem;font-weight:800;color:#1f2937">🏢 J-REIT 分析ダッシュボード</span>'
        f'<span style="font-size:12px;color:#8a909a">最終更新 {ts}　・　銘柄 {len(reits)}　・　キャッシュ参照のみ</span>'
        '</div>', unsafe_allow_html=True)

    pages = ["📋 ダッシュボード", "⚖️ 銘柄比較", "💼 マイポートフォリオ"]
    page = st.segmented_control("画面", pages, default=pages[0], label_visibility="collapsed")
    page = page or pages[0]
    st.divider()
    if page == "📋 ダッシュボード":
        render_dashboard(df, divs)
    elif page == "⚖️ 銘柄比較":
        render_comparison(df, divs)
    else:
        render_portfolio(df, divs)


if __name__ == "__main__":
    main()
