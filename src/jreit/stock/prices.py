"""価格取得: yfinance(code+'.T') 主、stooq フォールバック、parquetキャッシュ。
返り値は tz-naive DatetimeIndex / columns=[close, volume] の DataFrame（昇順）。"""
from __future__ import annotations
import time
from pathlib import Path
import pandas as pd
from loguru import logger


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """index→tz-naive 日付、close/volume の2列に正規化、昇順、欠損行除去。"""
    if df is None or df.empty:
        return pd.DataFrame(columns=["close", "volume"])
    df = df.copy()
    df.index = pd.to_datetime(df.index)
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_localize(None)
    df.index = df.index.normalize()
    out = pd.DataFrame(index=df.index)
    out["close"] = pd.to_numeric(df.get("Close", df.get("close")), errors="coerce")
    out["volume"] = pd.to_numeric(df.get("Volume", df.get("volume")), errors="coerce")
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out.dropna(subset=["close"])


def _from_yfinance(code: str, period: str) -> pd.DataFrame:
    import yfinance as yf
    ticker = f"{code}.T"                     # 仕様: 日本REITは ".T" 付与
    logger.info(f"yfinance fetch {ticker} period={period}")
    df = yf.Ticker(ticker).history(period=period, auto_adjust=False)
    return _normalize(df)


def _from_stooq(code: str) -> pd.DataFrame:
    """stooq の日次CSVを直接取得（pandas_datareader不使用）。
    例: https://stooq.com/q/d/l/?s=8960.jp&i=d → Date,Open,High,Low,Close,Volume"""
    import io
    import requests
    sym = f"{code.lower()}.jp"
    url = f"https://stooq.com/q/d/l/?s={sym}&i=d"
    logger.info(f"stooq CSV fallback {url}")
    r = requests.get(url, timeout=40, headers={"User-Agent": "jreit-analysis/0.1"})
    r.raise_for_status()
    txt = r.text.strip()
    if not txt or txt.lower().startswith("no data") or "," not in txt.splitlines()[0]:
        raise ValueError(f"stooq returned no data for {sym}")
    df = pd.read_csv(io.StringIO(txt))
    if "Date" not in df.columns or "Close" not in df.columns:
        raise ValueError(f"stooq unexpected format: {df.columns.tolist()}")
    df = df.set_index("Date")
    return _normalize(df)


def get_prices(code: str, period: str, cache_dir: Path, min_sleep: float = 2.0) -> tuple[pd.DataFrame, str]:
    """(df, source) を返す。全滅時は (空df, 'none')。生データは parquet に保存（再現性）。"""
    cache = cache_dir / f"{code}.parquet"
    df, source = pd.DataFrame(), "none"
    # 1) yfinance
    try:
        df = _from_yfinance(code, period)
        if not df.empty:
            source = "yfinance"
    except Exception as e:  # noqa
        logger.warning(f"{code}: yfinance failed: {e}")
    time.sleep(min_sleep)
    # 2) stooq
    if df.empty:
        try:
            df = _from_stooq(code)
            if not df.empty:
                source = "stooq"
        except Exception as e:  # noqa
            logger.warning(f"{code}: stooq failed: {e}")
        time.sleep(min_sleep)
    # 3) 直近キャッシュ
    if df.empty and cache.exists():
        try:
            df = pd.read_parquet(cache)
            source = "cache"
            logger.warning(f"{code}: using cached prices")
        except Exception as e:  # noqa
            logger.warning(f"{code}: cache read failed: {e}")
    # 保存（生データ）
    if not df.empty and source in ("yfinance", "stooq"):
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            df.to_parquet(cache)
        except Exception as e:  # noqa
            logger.warning(f"{code}: parquet save failed: {e}")
    return df, source


def fetch_info(code: str) -> dict:
    """market_cap / nav(p/b) などの補助情報。取れなければ {}。"""
    import yfinance as yf
    out = {}
    try:
        fi = yf.Ticker(f"{code}.T").fast_info
        out["market_cap"] = getattr(fi, "market_cap", None)
        out["last_volume"] = getattr(fi, "last_volume", None)
    except Exception as e:  # noqa
        logger.debug(f"{code}: fast_info failed: {e}")
    try:
        info = yf.Ticker(f"{code}.T").info
        out["market_cap"] = out.get("market_cap") or info.get("marketCap")
        out["nav_ratio"] = info.get("priceToBook")     # P/B ≒ NAV倍率の代理
    except Exception as e:  # noqa
        logger.debug(f"{code}: info failed: {e}")
    return out
