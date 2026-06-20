"""loguru による構造化ログ。コンソール＋ファイル(data/logs/pipeline.log)。"""
from __future__ import annotations
from pathlib import Path
import sys
from loguru import logger

_configured = False


def setup_logging(log_path: Path) -> "logger":
    global _configured
    if _configured:
        return logger
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.remove()
    fmt = "{time:YYYY-MM-DD HH:mm:ss} | {level: <7} | {extra} | {message}"
    logger.add(sys.stderr, level="INFO", format=fmt)
    logger.add(log_path, level="DEBUG", format=fmt, rotation="5 MB", retention=5, encoding="utf-8")
    _configured = True
    return logger
