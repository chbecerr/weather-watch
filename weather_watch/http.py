"""Tiny JSON-over-HTTP helper (stdlib only)."""

from __future__ import annotations

import gzip
import json
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "weather-watch/0.1"


def get_json(url: str, params: dict | None = None, retries: int = 3, timeout: float = 20.0):
    if params:
        url = f"{url}?{urllib.parse.urlencode(params, safe=',')}"
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
    )
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip" or body[:2] == b"\x1f\x8b":
                    body = gzip.decompress(body)
                return json.loads(body)
        except urllib.error.HTTPError as err:
            # 4xx won't get better by retrying.
            if 400 <= err.code < 500 and err.code != 429:
                raise
            last_err = err
        except (urllib.error.URLError, TimeoutError) as err:
            last_err = err
        time.sleep(1.5 * (attempt + 1))
    assert last_err is not None
    raise last_err
