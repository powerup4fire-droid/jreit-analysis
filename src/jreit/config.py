"""config.yaml 読込（プロジェクトルート基準でパス解決）。"""
from __future__ import annotations
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[2]   # jreit-analysis/


class Config:
    def __init__(self, data: dict):
        self._d = data

    @property
    def validation_codes(self) -> list[str]:
        return [str(c) for c in self._d.get("validation_codes", [])]

    @property
    def net(self) -> dict:
        return self._d.get("network", {})

    @property
    def stock(self) -> dict:
        return self._d.get("stock", {})

    @property
    def dividends(self) -> dict:
        return self._d.get("dividends", {})

    @property
    def sources(self) -> dict:
        return self._d.get("sources", {})

    def path(self, key: str) -> Path:
        return ROOT / self._d["paths"][key]


def load_config(path: str | Path | None = None) -> Config:
    p = Path(path) if path else ROOT / "config.yaml"
    return Config(yaml.safe_load(p.read_text(encoding="utf-8")))
