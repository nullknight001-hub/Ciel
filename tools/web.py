from bs4 import BeautifulSoup
from ddgs import DDGS
import requests
from config import MAX_OUTPUT_CHARS


def web_search(query: str, max_results: int = 5) -> str:
    """Searches the live web for current information, docs, or news."""
    print(f"[Searching Web]: {query}")
    try:
        results = DDGS().text(query, max_results=max_results)
        if not results:
            return "No search results found."
        formatted = [
            f"Title: {r.get('title')}\nURL: {r.get('href')}\nSnippet: {r.get('body')}\n"
            for r in results
        ]
        return "\n---\n".join(formatted)[:MAX_OUTPUT_CHARS]
    except Exception as e:
        return f"Web search error: {e}"


def read_webpage(url: str) -> str:
    """Fetches a webpage URL and returns its clean readable text."""
    print(f"[Reading URL]: {url}")
    try:
        headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"}
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = " ".join(soup.get_text(separator=" ").split())
        if len(text) > MAX_OUTPUT_CHARS:
            return text[:MAX_OUTPUT_CHARS] + "\n... [TRUNCATED]"
        return text or "Page had no readable text."
    except Exception as e:
        return f"Error reading webpage: {e}"