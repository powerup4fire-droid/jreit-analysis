"""価格系の計算（pandasのみ）。履歴不足は該当値のみ None（例外で落とさない）。"""
from __future__ import annotations
import pandas as pd
from loguru import logger


def _safe_last_sma(series: pd.Series, window: int) -> float | None:
    """window本以上あれば最新SMA、なければ None（Insufficient Data）。"""
    s = series.dropna()
    if len(s) < window:
        return None
    return float(s.rolling(window).mean().iloc[-1])


def compute_metrics(df: pd.DataFrame, deviation_years: int,
                    lehman_start: str, lehman_end: str) -> dict:
    """df: index=tz-naive date, columns=[close, volume] 昇順。"""
    res: dict = {
        "latest_price": None, "volume": None, "price_asof": None,
        "dev_mean_6y_pct": None, "dev_median_6y_pct": None,
        "ma": {k: None for k in ["25d", "75d", "200d", "75w", "200w", "75m", "200m"]},
        "lehman_min": None, "lehman_ratio_pct": None,
    }
    if df is None or df.empty:
        return res

    close = df["close"].dropna()
    if close.empty:
        return res
    latest = float(close.iloc[-1])
    res["latest_price"] = latest
    res["price_asof"] = close.index[-1].strftime("%Y-%m-%d")
    if "volume" in df and df["volume"].notna().any():
        v = df["volume"].dropna()
        res["volume"] = int(v.iloc[-1]) if not v.empty else None

    # 6年平均/中央値乖離
    cutoff = close.index.max() - pd.DateOffset(years=deviation_years)
    win = close[close.index >= cutoff]
    if len(win) > 1:
        mean_, median_ = float(win.mean()), float(win.median())
        if mean_:
            res["dev_mean_6y_pct"] = (latest - mean_) / mean_ * 100.0
        if median_:
            res["dev_median_6y_pct"] = (latest - median_) / median_ * 100.0

    # 日次MA
    res["ma"]["25d"] = _safe_last_sma(close, 25)
    res["ma"]["75d"] = _safe_last_sma(close, 75)
    res["ma"]["200d"] = _safe_last_sma(close, 200)
    # 週次MA（週末終値）
    wk = close.resample("W-FRI").last().dropna()
    res["ma"]["75w"] = _safe_last_sma(wk, 75)
    res["ma"]["200w"] = _safe_last_sma(wk, 200)
    # 月次MA（月末終値）
    mo = close.resample("ME").last().dropna()
    res["ma"]["75m"] = _safe_last_sma(mo, 75)
    res["ma"]["200m"] = _safe_last_sma(mo, 200)

    # リーマン比: 期間min / latest * 100
    leh = close[(close.index >= pd.Timestamp(lehman_start)) & (close.index <= pd.Timestamp(lehman_end))]
    if not leh.empty and latest:
        res["lehman_min"] = float(leh.min())
        res["lehman_ratio_pct"] = float(leh.min()) / latest * 100.0
    else:
        logger.debug("Lehman period: insufficient data -> NULL")

    return res
