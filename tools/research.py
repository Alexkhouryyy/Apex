"""Web search and browsing tools.

Primary backend: Anthropic's built-in web_search tool (routes via Claude).
Fallback: ddgs (DuckDuckGo) for direct searches.
"""
import json
import requests
from bs4 import BeautifulSoup
import config
from agent import telemetry

_search_client = None


class SearchError(Exception):
    """Every search backend failed. Raised rather than returned: a caller that
    cannot tell 'no results' from 'search is broken' will synthesise an answer
    out of the error message."""


class FetchError(Exception):
    """A page could not be fetched. See fetch() vs browse()."""


def _get_client():
    global _search_client
    if _search_client is None:
        import anthropic
        _search_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _search_client


def search(query: str, num_results: int = None) -> list[dict]:
    """Search the web. Returns list of {title, url, snippet}."""
    num_results = num_results or config.MAX_SEARCH_RESULTS

    # Try Anthropic web_search first (works in restricted envs)
    try:
        return _search_via_anthropic(query, num_results)
    except Exception:
        pass

    # Fallback: ddgs
    try:
        from ddgs import DDGS
        results = DDGS().text(query, max_results=num_results)
        return [
            {"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")}
            for r in (results or [])
        ]
    except Exception as e:
        # Never return a synthetic result row here. It reads as a successful
        # search to every caller, and its error text ends up quoted back as
        # if it were a source.
        raise SearchError(f"all search backends failed: {e}") from e


def _search_via_anthropic(query: str, num_results: int) -> list[dict]:
    """Use Anthropic's web_search built-in tool to run a search."""
    client = _get_client()
    resp = telemetry.create(
        client,
        call_site="tools.research/web_search",
        model=config.PROACTIVE_MODEL,
        max_tokens=2048,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 1}],
        messages=[{"role": "user", "content": (
            f"Search for: {query}\n\n"
            f"Return the top {num_results} results as a JSON array with fields: title, url, snippet. "
            "Output ONLY the JSON array, nothing else."
        )}],
    )

    # Extract text from response
    text = ""
    for block in resp.content:
        if getattr(block, "type", "") == "text":
            text += block.text

    # Parse the JSON array from response
    start = text.find("[")
    end = text.rfind("]") + 1
    if start != -1 and end > start:
        return json.loads(text[start:end])

    # No JSON in the reply — that is a parse failure, not a result. Fall through
    # to the next backend rather than inventing a URL-less row.
    raise ValueError("web_search returned no parseable JSON array")


def fetch(url: str, max_chars: int = None) -> str:
    """Fetch a URL and return cleaned text content. Raises FetchError.

    Prefer this over browse() anywhere the content feeds a model: browse()
    reports failure as an ordinary string, which is indistinguishable from a
    page whose text happens to say 'Error fetching ...'.
    """
    max_chars = max_chars or config.MAX_PAGE_CONTENT_CHARS
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
            tag.decompose()

        text = soup.get_text(separator="\n", strip=True)
        lines = [l for l in text.splitlines() if l.strip()]
        content = "\n".join(lines)
    except Exception as e:
        raise FetchError(f"{url}: {e}") from e

    if not content.strip():
        raise FetchError(f"{url}: page had no readable text")
    return content[:max_chars] + ("..." if len(content) > max_chars else "")


def browse(url: str, max_chars: int = None) -> str:
    """Fetch a URL and return cleaned text, or an error string.

    Kept string-returning for the `web_browse` agent tool, where the model is
    the reader and an error message is a useful result.
    """
    try:
        return fetch(url, max_chars=max_chars)
    except FetchError as e:
        return f"Error fetching {e}"


def deep_research(topic: str) -> str:
    """Search + browse top results and compile a research summary."""
    results = search(topic, num_results=4)
    compiled = [f"Research on: {topic}\n"]

    for i, r in enumerate(results, 1):
        compiled.append(f"\n--- Source {i}: {r.get('title', '')} ---")
        compiled.append(f"URL: {r.get('url', '')}")
        compiled.append(f"Snippet: {r.get('snippet', '')}")
        if r.get("url"):
            try:
                compiled.append(f"Content:\n{fetch(r['url'], max_chars=2000)}")
            except FetchError as e:
                # Say the page is missing; never pass the error off as content.
                compiled.append(f"Content: [unavailable — {e}]")

    return "\n".join(compiled)
