import random
import time
from typing import Any, Dict, Optional

import requests


class DiscordAPIError(RuntimeError):
    """Raised when a Discord REST request fails after retry handling."""

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


def _retry_delay(response: requests.Response, attempt: int) -> float:
    """Prefer Discord's Retry-After header, then use bounded exponential backoff."""
    retry_after = response.headers.get("Retry-After")
    try:
        if retry_after is not None:
            return min(max(float(retry_after), 0.5), 60.0)
    except (TypeError, ValueError):
        pass

    # 0.75, 1.5, 3, 6, 12... with a little jitter.
    return min(60.0, (0.75 * (2 ** attempt)) + random.uniform(0, 0.25))


def discord_request(
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    json: Any = None,
    timeout: float = 15,
    max_retries: int = 5,
) -> requests.Response:
    """Make a raw Discord REST request without hammering a global 429.

    This is intentionally for raw ``requests`` calls only. discord.py already
    has its own endpoint/global rate-limit handling; duplicating a second
    limiter around discord.py would make the bot slower and can interfere with
    its internal bucket management.

    For HTTP 429 responses, Discord's Retry-After value is honored. Global
    429s are therefore allowed to pause the raw request instead of immediately
    retrying and making the global limit worse.
    """
    last_response = None

    for attempt in range(max_retries + 1):
        try:
            response = requests.request(
                method,
                url,
                headers=headers,
                json=json,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            if attempt >= max_retries:
                raise DiscordAPIError(f"Discord request failed: {exc}") from exc
            time.sleep(min(8.0, 0.75 * (2 ** attempt) + random.uniform(0, 0.25)))
            continue

        last_response = response

        if response.status_code != 429:
            return response

        # Discord explicitly supplies Retry-After for rate limits. Do not
        # blindly retry immediately, especially when X-RateLimit-Scope=global.
        delay = _retry_delay(response, attempt)
        scope = response.headers.get("X-RateLimit-Scope", "unknown")
        print(
            f"[DiscordAPI] 429 rate limit ({scope}); waiting {delay:.2f}s "
            f"before retry {attempt + 1}/{max_retries}"
        )
        if attempt >= max_retries:
            break
        time.sleep(delay)

    status = last_response.status_code if last_response is not None else None
    raise DiscordAPIError(
        f"Discord API remained rate-limited after {max_retries} retries.",
        status=status,
    )
