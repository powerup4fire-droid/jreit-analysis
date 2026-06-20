"""ネットワーク共通: 単一Session + retry(最大3) + 指数バックオフ + リクエスト後 sleep(>=2)。"""
from __future__ import annotations
import time
import requests
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type


class HttpClient:
    def __init__(self, user_agent: str, max_retries: int = 3,
                 min_sleep: float = 2.0, timeout: int = 30):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": user_agent})
        self.min_sleep = max(min_sleep, 2.0)   # 仕様: 最低2秒
        self.timeout = timeout
        self.max_retries = max(max_retries, 1)

    def get(self, url: str, **kw) -> requests.Response:
        @retry(
            stop=stop_after_attempt(self.max_retries),
            wait=wait_exponential(multiplier=1, min=2, max=30),
            retry=retry_if_exception_type((requests.RequestException,)),
            reraise=True,
        )
        def _do() -> requests.Response:
            logger.debug(f"GET {url}")
            r = self.s.get(url, timeout=self.timeout, **kw)
            r.raise_for_status()
            return r

        try:
            return _do()
        finally:
            time.sleep(self.min_sleep)   # レート制御（成功/失敗問わず）

    def download(self, url: str, dest, **kw) -> bool:
        try:
            r = self.get(url, **kw)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(r.content)
            return True
        except Exception as e:  # noqa
            logger.warning(f"download failed {url}: {e}")
            return False
