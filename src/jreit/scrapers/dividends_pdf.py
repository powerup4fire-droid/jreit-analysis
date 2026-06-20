"""決算短信PDFから 1口当たり分配金 / 利益超過分配金 を抽出。
一次ソース: japan-reit.com の /meigara/{code}/release/決算/{year}（PDFは永続Azure blob）。
PDFは表形式のため pdfplumber.extract_tables() で構造抽出（数値ファクトのみ利用）。"""
from __future__ import annotations
import datetime as dt
import hashlib
import re
from pathlib import Path
from urllib.parse import quote
from bs4 import BeautifulSoup
from loguru import logger
from ..models import Reit, Dividend
from ..http import HttpClient

_PERIOD = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月期")
_NUM = re.compile(r"\d{1,3}(?:,\d{3})*")
_AZURE = "azure-api.net"
LOOKBACK_YEARS = 7   # 半期決算で10期=約5年。余裕を持って遡る


def _first_num(cell: str | None) -> int | None:
    if not cell:
        return None
    nums = _NUM.findall(cell)
    return int(nums[0].replace(",", "")) if nums else None


def discover_japanreit(client: HttpClient, meigara_base: str, code: str, max_n: int) -> list[dict]:
    """release/決算/{year} を新しい年から走査し、決算短信PDF {period_label, pdf_url} を収集。"""
    out: list[dict] = []
    seen: set[str] = set()
    this_year = dt.date.today().year
    enc = quote("決算")
    for year in range(this_year, this_year - LOOKBACK_YEARS, -1):
        url = f"{meigara_base}{code}/release/{enc}/{year}"
        try:
            resp = client.get(url)
        except Exception as e:  # noqa
            logger.warning(f"{code}: release {year} fetch failed: {e}")
            continue
        soup = BeautifulSoup(resp.text, "lxml")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            label = a.get_text(" ", strip=True)
            if _AZURE not in href or "決算短信" not in label:
                continue
            if href in seen:
                continue
            seen.add(href)
            pm = _PERIOD.search(label)
            period = f"{pm.group(1)}年{int(pm.group(2))}月期" if pm else label[:20]
            out.append({"period_label": period, "pdf_url": href})
            if len(out) >= max_n:
                return out
    if not out:
        logger.info(f"{code}: no 決算短信 PDF on japan-reit release pages")
    return out


def parse_pdf(path: Path) -> tuple[int | None, int | None, str]:
    """returns (total_distribution, excess_distribution, status)。
    基準分配金(利益超過含まない) と 1口当たり利益超過分配金 を表から取り total=base+excess。"""
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            for pg in pdf.pages[:3]:
                for tb in pg.extract_tables() or []:
                    if not tb or not tb[0]:
                        continue
                    # 全角/半角の「1」差異を吸収（"口当たり…"で判定）
                    header = [(c or "").replace("\n", "") for c in tb[0]]
                    hjoin = " ".join(header)
                    if "口当たり分配金" not in hjoin or "利益超過分配金" not in hjoin:
                        continue
                    bcol = ecol = None
                    for i, h in enumerate(header):
                        if "口当たり分配金" in h and "含まない" in h:
                            bcol = i
                        if "口当たり利益超過分配金" in h and "含まない" not in h:
                            ecol = i
                    if bcol is None or ecol is None:
                        continue
                    for row in tb[1:]:
                        bcell = row[bcol] if bcol < len(row) else None
                        base = _first_num(bcell)
                        if base is None:
                            continue
                        ecell = row[ecol] if ecol < len(row) else None
                        excess = _first_num(ecell) or 0   # － は0扱い
                        return base + excess, excess, "ok"
    except Exception as e:  # noqa
        logger.warning(f"pdf parse failed {path.name}: {e}")
        return None, None, "unreadable"
    return None, None, "regex_miss"


def analyze_dividends(client: HttpClient, reit: Reit, pdf_cache: Path, quarters: int,
                      meigara_base: str) -> list[Dividend]:
    reports = discover_japanreit(client, meigara_base, reit.code, quarters)
    results: list[Dividend] = []
    if not reports:
        results.append(Dividend(code=reit.code, period_label="latest", parse_status="download_fail"))
        return results
    for rep in reports:
        period = rep["period_label"]
        dest = pdf_cache / reit.code / f"{period}.pdf"
        d = Dividend(code=reit.code, period_label=period, pdf_url=rep["pdf_url"])
        if not (dest.exists() or client.download(rep["pdf_url"], dest)):
            d.parse_status = "download_fail"
            results.append(d); continue
        try:
            d.pdf_sha256 = hashlib.sha256(dest.read_bytes()).hexdigest()
        except Exception:  # noqa
            pass
        total, excess, status = parse_pdf(dest)
        d.total_distribution = float(total) if total is not None else None
        d.excess_distribution = float(excess) if excess is not None else None
        d.excess_present = bool(excess)
        if total and excess is not None and total:
            d.excess_ratio_pct = excess / total * 100.0
        d.parse_status = status
        results.append(d)
    return results
