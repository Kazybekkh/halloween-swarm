"""Phase 0: one minimal call per partner. Prints PASS/FAIL/SKIP, never a key.

Run: .venv/bin/python scripts/verify_partners.py
"""
import asyncio
import json
import os
import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from swarm.env import node_bin_dir, one_mcp_env  # noqa: E402  (loads .env)

TIMEOUT = 30.0
CONTROL_TICK = Path(__file__).resolve().parent.parent / "fixtures" / "control-tick.json"


def key(name: str) -> str | None:
    return os.environ.get(name, "").strip() or None


def short(text: str, n: int = 200) -> str:
    return " ".join(text.split())[:n]


def check_youcom() -> tuple[str, str]:
    k = key("YDC_API_KEY")
    if not k:
        return "SKIP", "YDC_API_KEY empty"
    r = httpx.post(
        "https://ydc-index.io/v1/search",
        headers={"X-API-Key": k, "Content-Type": "application/json"},
        # extraction is an object (https://docs.you.com/api-reference/search/v1-search)
        json={"query": "AWS Builder Loft San Francisco", "count": 3, "extraction": {"extraction_mode": "highlights"}},
        timeout=TIMEOUT,
    )
    if r.status_code != 200:
        return "FAIL", f"HTTP {r.status_code}: {short(r.text)}"
    web = r.json().get("results", {}).get("web", [])
    return "PASS", f"HTTP 200, {len(web)} web results, remaining={r.headers.get('x-ratelimit-remaining')}"


def check_parasail() -> tuple[str, str]:
    k = key("PARASAIL_API_KEY")
    if not k:
        return "SKIP", "PARASAIL_API_KEY empty"
    r = httpx.get("https://api.parasail.io/v1/models", headers={"Authorization": f"Bearer {k}"}, timeout=TIMEOUT)
    if r.status_code != 200:
        return "FAIL", f"HTTP {r.status_code}: {short(r.text)}"
    ids = [m["id"] for m in r.json().get("data", [])]
    has = "parasail-glm-53-flash" in ids
    return ("PASS" if has else "FAIL"), f"{len(ids)} models, parasail-glm-53-flash listed={has}"


def check_openai_decisions() -> tuple[str, str]:
    k = key("OPENAI_API_KEY")
    if not k:
        return "SKIP", "OPENAI_API_KEY empty"
    tick = json.loads(CONTROL_TICK.read_text())
    r = httpx.post(
        "https://api.openai.com/v1/decisions",
        headers={"Authorization": f"Bearer {k}", "Content-Type": "application/json"},
        # request shape from https://developers.openai.com/api/docs/guides/decisions
        json={
            "model": "gpt-6-luna",
            "input": [{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Evaluate this facility sensor tick."},
                    {"type": "input_json", "data": tick},
                ],
            }],
            "questions": [{
                "type": "predicate",
                "name": "genuine_breach",
                "instructions": "Is this a genuine paranormal breach requiring action, not a harmless control reading?",
            }],
        },
        timeout=TIMEOUT,
    )
    if r.status_code != 200:
        return "FAIL", f"HTTP {r.status_code}: {short(r.text, 300)}"
    return "PASS", f"HTTP 200: {short(r.text, 300)}"


async def check_one() -> tuple[str, str]:
    if not key("ONE_API_KEY"):
        return "SKIP", "ONE_API_KEY empty"
    if not node_bin_dir():
        return "FAIL", "no Node 22.23.4+, 24.22.1+ or 26.12.1+ found (nvm or PATH)"
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    import tempfile

    params = StdioServerParameters(command="npx", args=["-y", "@withone/mcp"], env=one_mcp_env())
    errlog = tempfile.TemporaryFile("w+")
    async with stdio_client(params, errlog=errlog) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool("list_one_integrations", {})
    text = "".join(getattr(c, "text", "") for c in res.content)
    if res.isError:
        return "FAIL", short(text)
    errlog.seek(0)
    if "401" in errlog.read():
        # @withone/mcp swallows the vault 401 and returns an empty list
        return "FAIL", "One API rejected ONE_API_KEY (401): bad or revoked key, or wrong live/test environment"
    platforms = sorted(set(re.findall(r'"platform"\s*:\s*"([^"]+)"', text)))
    return "PASS", f"connected platforms: {platforms or 'none'}; discord connected={'discord' in platforms}"


async def check_scenario() -> tuple[str, str]:
    url = key("SCENARIO_MCP")
    if not url:
        return "SKIP", "SCENARIO_MCP empty"
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
    names = [t.name for t in tools.tools]
    want = {"hack26-get-tick", "hack26-submit-containment", "hack26-get-scoreboard"}
    return ("PASS" if want <= set(names) else "FAIL"), f"tools: {names}"


def run(name, fn):
    try:
        status, detail = asyncio.run(fn()) if asyncio.iscoroutinefunction(fn) else fn()
    except Exception as e:  # report, keep going
        status, detail = "FAIL", f"{type(e).__name__}: {short(str(e))}"
    print(f"{status:4}  {name:22} {detail}")
    return status


if __name__ == "__main__":
    results = [
        run("You.com search", check_youcom),
        run("Parasail /v1/models", check_parasail),
        run("One list_integrations", check_one),
        run("OpenAI Decisions", check_openai_decisions),
        run("Scenario MCP tools", check_scenario),
    ]
    sys.exit(0 if all(r == "PASS" for r in results) else 1)
