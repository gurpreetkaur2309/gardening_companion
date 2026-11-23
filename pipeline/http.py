"""HTTP client with an on-disk cache, a polite request rate, and retries.

Re-running the pipeline should cost nothing: every response (including 404s) is
cached by URL + params.  Wikimedia asks API clients to identify themselves with
contact details, hence the User-Agent.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

DEFAULT_CONTACT = "gurpreetkhanuja2309@gmail.com"


class CachedHttp:
    def __init__(
        self,
        cache_dir: Path,
        *,
        contact: str | None = None,
        min_interval: float = 0.05,
        timeout: float = 20.0,
        session: requests.Session | None = None,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.min_interval = min_interval
        self.timeout = timeout
        self.requests_made = 0
        self.cache_hits = 0
        self._last_request = 0.0
        self._lock = threading.Lock()

        contact = contact or os.getenv("PIPELINE_CONTACT") or DEFAULT_CONTACT
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = f"gardening-companion-pipeline/0.1 ({contact})"
        retry = Retry(
            total=4,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
            respect_retry_after_header=True,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))

    def _cache_path(self, url: str, params: dict[str, Any] | None) -> Path:
        key = json.dumps([url, sorted((params or {}).items())], default=str)
        return self.cache_dir / f"{hashlib.sha1(key.encode()).hexdigest()}.json"

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Return the decoded JSON body, or ``None`` for 404 / unusable responses."""
        path = self._cache_path(url, params)
        if path.exists():
            with self._lock:
                self.cache_hits += 1
            return json.loads(path.read_text())["body"]

        # Reserve the next request slot under the lock, then sleep outside it,
        # so threads stagger their requests instead of bursting.
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._last_request + self.min_interval)
            self._last_request = slot
            self.requests_made += 1
        if slot > now:
            time.sleep(slot - now)

        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
        except requests.RequestException as exc:
            # Transient: do not cache, so the next run tries again.
            logger.warning("GET %s failed: %s", url, exc)
            return None

        if response.status_code == 404:
            body = None
        elif response.ok:
            try:
                body = response.json()
            except ValueError:
                body = None
        else:
            logger.warning("GET %s -> HTTP %s", url, response.status_code)
            return None

        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"url": url, "body": body}))
        tmp.replace(path)  # atomic: a killed run never leaves a half-written entry
        return body
