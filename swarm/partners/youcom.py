"""You.com: live web grounding for entity classes (POST /v1/search with highlights)."""
from pydantic import BaseModel, Field

from crewai.tools import BaseTool

from swarm.env import need
from swarm.log import log
from swarm.ratelimit import request_with_backoff

SEARCH_URL = "https://ydc-index.io/v1/search"
_cache: dict[str, str] = {}


def search(query: str, count: int = 4) -> list[dict]:
    resp = request_with_backoff(
        "POST",
        SEARCH_URL,
        headers={"X-API-Key": need("YDC_API_KEY"), "Content-Type": "application/json"},
        # extraction is an object: https://docs.you.com/api-reference/search/v1-search
        json={"query": query, "count": count, "extraction": {"extraction_mode": "highlights"}},
    )
    resp.raise_for_status()
    out = []
    for r in resp.json().get("results", {}).get("web", [])[:count]:
        highlights = (r.get("contents") or {}).get("highlights") or r.get("snippets") or [r.get("description", "")]
        out.append({"url": r.get("url"), "title": r.get("title"), "highlights": [h[:400] for h in highlights[:2]]})
    return out


class SearchInput(BaseModel):
    query: str = Field(..., description="Natural-language web search query")


class YouSearchTool(BaseTool):
    name: str = "you_web_search"
    description: str = (
        "Search the live web with You.com. Returns titles, URLs and the passages that answer the query. "
        "Use it to ground what an entity class is and which response doctrine applies."
    )
    args_schema: type[BaseModel] = SearchInput

    def _run(self, query: str) -> str:
        if query in _cache:
            return _cache[query]
        try:
            results = search(query)
        except Exception as e:  # report to the agent, keep the run alive
            log.warn("youcom_error", query=query, error=str(e)[:200])
            return f"You.com search failed: {e}"
        text = "\n".join(f"- {r['title']} <{r['url']}>: {' '.join(r['highlights'])}" for r in results) or "no results"
        _cache[query] = text
        log.info("youcom_search", query=query, results=len(results))
        return text
