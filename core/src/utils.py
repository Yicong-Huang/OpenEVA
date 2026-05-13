"""Shared utility functions for the Eva backend."""


def repo_from_pr_url(url: str) -> str:
    """Extract 'owner/repo' from a GitHub PR URL.

    >>> repo_from_pr_url("https://github.com/acme/widgets/pull/100")
    'acme/widgets'
    >>> repo_from_pr_url("")
    ''
    """
    if not url:
        return ""
    return url.replace("https://github.com/", "").split("/pull/")[0]


def pr_number_from_url(url: str):
    """Extract PR number from a GitHub PR URL. Returns int or None.

    >>> pr_number_from_url("https://github.com/acme/widgets/pull/100")
    100
    >>> pr_number_from_url("not-a-url")
    """
    if url and "/pull/" in url:
        try:
            return int(url.split("/pull/")[-1].split("/")[0].split("?")[0])
        except (ValueError, IndexError):
            pass
    return None


def clamp_int(value, lo: int, hi: int) -> int:
    """Clamp `value` to the inclusive range [lo, hi].

    Used by route handlers to defend `?limit=` query params against
    hostile / typo'd inputs (e.g. `?limit=999999` blowing up a DB
    query, or `?limit=-1` returning a degenerate empty result).
    Coerces non-int inputs through `int()`; non-numeric strings raise
    `ValueError` so the route surfaces FastAPI's 422 instead of
    silently doing the wrong thing.

    >>> clamp_int(50, 1, 100)
    50
    >>> clamp_int(99999, 1, 100)
    100
    >>> clamp_int(-5, 1, 100)
    1
    >>> clamp_int("25", 1, 100)
    25
    """
    if lo > hi:
        raise ValueError(f"clamp_int: lo ({lo}) must be <= hi ({hi})")
    n = int(value)
    if n < lo:
        return lo
    if n > hi:
        return hi
    return n
