"""データモデル（dataclass）。欠損は全て None。"""
from __future__ import annotations
from dataclasses import dataclass, field

ASSET_KEYS = ["office", "residential", "logistics", "retail", "hotel", "healthcare", "other"]


@dataclass
class Reit:
    code: str
    name: str | None = None
    reit_type: str | None = None
    yield_total: float | None = None
    yield_ex_excess: float | None = None
    num_properties: int | None = None
    asset: dict[str, float | None] = field(default_factory=lambda: {k: None for k in ASSET_KEYS})
    asset_estimated: bool = False
    source_url: str | None = None


@dataclass
class StockMetrics:
    code: str
    price_asof: str | None = None
    latest_price: float | None = None
    volume: int | None = None
    market_cap: float | None = None
    nav_ratio: float | None = None
    dev_mean_6y_pct: float | None = None
    dev_median_6y_pct: float | None = None
    ma: dict[str, float | None] = field(default_factory=dict)   # 25d,75d,200d,75w,200w,75m,200m
    lehman_min: float | None = None
    lehman_ratio_pct: float | None = None
    price_source: str | None = None


@dataclass
class Dividend:
    code: str
    period_label: str
    report_date: str | None = None
    total_distribution: float | None = None
    excess_distribution: float | None = None
    excess_present: bool = False
    excess_ratio_pct: float | None = None
    pdf_url: str | None = None
    pdf_sha256: str | None = None
    parse_status: str = "n/a"
