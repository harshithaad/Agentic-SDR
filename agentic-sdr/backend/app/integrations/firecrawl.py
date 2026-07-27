import httpx

from app.config import settings


class FirecrawlNotConfigured(Exception):
    pass


def scrape(url: str, timeout: float = 30.0) -> str:
    """Return page content as markdown; empty string when the page yields nothing."""
    if not settings.FIRECRAWL_API_KEY:
        raise FirecrawlNotConfigured("FIRECRAWL_API_KEY is not set")
    resp = httpx.post(
        "https://api.firecrawl.dev/v1/scrape",
        headers={"Authorization": f"Bearer {settings.FIRECRAWL_API_KEY}"},
        json={"url": url, "formats": ["markdown"]},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json().get("data", {}).get("markdown", "") or ""
