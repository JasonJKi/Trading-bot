"""GitHub adapter — code & repo search.

Open-source AI trading bots are gold for "what does production code actually look
like." We hit the public search API; an optional GITHUB_TOKEN raises rate limits
from 10 req/min unauthenticated to 30 req/min authenticated.
"""
from __future__ import annotations

import logging
from datetime import datetime

import httpx

from src.config import get_settings
from src.research.sources.base import DocumentRow, SourceAdapter, register

log = logging.getLogger(__name__)

GH_REPO_SEARCH = "https://api.github.com/search/repositories"


@register
class GitHubAdapter(SourceAdapter):
    id = "github"
    name = "GitHub"

    def __init__(self) -> None:
        self._token = get_settings().github_token

    def is_available(self) -> bool:
        return True  # public, but token highly recommended

    async def search(self, query: str, limit: int = 10) -> list[DocumentRow]:
        # Bias toward repos that are actually trading bots.
        q = f"{query} in:name,description,readme stars:>10"
        headers = {"Accept": "application/vnd.github+json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        rows: list[DocumentRow] = []
        try:
            async with httpx.AsyncClient(timeout=20, headers=headers) as c:
                r = await c.get(
                    GH_REPO_SEARCH,
                    params={"q": q, "sort": "stars", "order": "desc", "per_page": min(limit, 25)},
                )
                r.raise_for_status()
                items = r.json().get("items", [])
                for item in items:
                    full = item.get("full_name", "")
                    if not full:
                        continue
                    readme = await self._readme(c, full)
                    content_parts = [item.get("description") or "", readme]
                    rows.append(
                        DocumentRow(
                            source=self.id,
                            external_id=str(item.get("id")),
                            url=item.get("html_url", ""),
                            title=full,
                            content="\n\n---\n\n".join(p for p in content_parts if p)[:80_000],
                            author=item.get("owner", {}).get("login", ""),
                            published_at=_parse(item.get("pushed_at")),
                            score=float(item.get("stargazers_count", 0)),
                            meta={
                                "stars": item.get("stargazers_count"),
                                "forks": item.get("forks_count"),
                                "language": item.get("language"),
                                "topics": item.get("topics", []),
                            },
                        )
                    )
        except Exception:
            log.exception("github: search failed")
        log.info("github: %d repos for query=%r", len(rows), query)
        return rows

    async def _readme(self, client: httpx.AsyncClient, full_name: str) -> str:
        try:
            r = await client.get(f"https://api.github.com/repos/{full_name}/readme")
            r.raise_for_status()
            body = r.json()
            # README is base64-encoded.
            import base64
            content = body.get("content", "")
            if not content:
                return ""
            return base64.b64decode(content).decode("utf-8", errors="ignore")[:30_000]
        except Exception:
            return ""


def _parse(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None
