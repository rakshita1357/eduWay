"""Fetches a job posting URL and extracts the job description text.

Job boards vary wildly (JS-rendered SPAs, anti-bot walls, wildly different
markup), so this is best-effort: it tries a plain HTTP GET + heuristic
text extraction, and returns None if it can't get something that looks
like a real JD, so the caller can fall back to asking the user to paste
the JD manually instead of feeding the pipeline garbage.
"""
import logging
import re
import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("uvicorn.error")

MAX_JD_CHARS = 8000
MIN_JD_CHARS = 200  # below this, we don't trust the extraction

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

# Common containers job boards use for the actual description body
_LIKELY_SELECTORS = [
    {"attrs": {"class": re.compile(r"job.?description", re.I)}},
    {"attrs": {"id": re.compile(r"job.?description", re.I)}},
    {"attrs": {"class": re.compile(r"description", re.I)}},
    {"name": "article"},
    {"name": "main"},
]


async def fetch_job_description(url: str) -> str | None:
    try:
        async with httpx.AsyncClient(headers=_HEADERS, timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
    except Exception as e:
        logger.warning(f"Failed to fetch job URL '{url}': {e}")
        return None

    try:
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "noscript"]):
            tag.decompose()

        candidate_text = ""
        for sel in _LIKELY_SELECTORS:
            node = soup.find(**sel)
            if node:
                text = node.get_text(separator="\n", strip=True)
                if len(text) > len(candidate_text):
                    candidate_text = text

        if len(candidate_text) < MIN_JD_CHARS:
            # Fall back to whole-page text
            candidate_text = soup.get_text(separator="\n", strip=True)

        candidate_text = re.sub(r"\n{3,}", "\n\n", candidate_text).strip()

        if len(candidate_text) < MIN_JD_CHARS:
            logger.warning(f"Extracted JD text too short ({len(candidate_text)} chars) for url {url}")
            return None

        return candidate_text[:MAX_JD_CHARS]
    except Exception as e:
        logger.warning(f"Failed to parse job page '{url}': {e}")
        return None