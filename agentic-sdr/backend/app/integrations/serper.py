import httpx

from app.config import settings


class SerperNotConfigured(Exception):
    pass


def search(query: str, num: int = 5, timeout: float = 20.0) -> str:
    """Return organic results as '- title: snippet' lines."""
    if not settings.SERPER_API_KEY:
        raise SerperNotConfigured("SERPER_API_KEY is not set")
    resp = httpx.post(
        "https://google.serper.dev/search",
        headers={"X-API-KEY": settings.SERPER_API_KEY},
        json={"q": query, "num": num},
        timeout=timeout,
    )
    resp.raise_for_status()
    items = resp.json().get("organic", [])
    return "\n".join(f"- {i.get('title', '')}: {i.get('snippet', '')}" for i in items)
