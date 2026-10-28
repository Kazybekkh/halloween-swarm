"""Scenario feed over MCP. MCPServerHTTP(streamable=True), never the https:// string shorthand
(guide §4: the shorthand silently resolves to zero tools inside an event loop)."""
import json
import os

from crewai.mcp import MCPServerHTTP
from crewai.mcp.client import MCPClient
from crewai.mcp.transports.http import HTTPTransport

from swarm.env import team_handle
from swarm.partners import run_async


def scenario_server() -> MCPServerHTTP:
    return MCPServerHTTP(url=os.environ["SCENARIO_MCP"], streamable=True, headers={})


async def _call(tool: str, args: dict) -> dict:
    cfg = scenario_server()
    client = MCPClient(HTTPTransport(url=cfg.url, headers=cfg.headers, streamable=cfg.streamable))
    async with client:
        text = await client.call_tool(tool, args)
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return {"raw": text}


def call(tool: str, args: dict | None = None) -> dict:
    return run_async(_call(tool, args or {}))


def _team_args() -> dict:
    args = {"team": team_handle()}
    if os.environ.get("TEAM_TOKEN"):
        args["token"] = os.environ["TEAM_TOKEN"]
    return args


def get_tick(scenario: str) -> dict:
    return call("hack26-get-tick", {**_team_args(), "scenario": scenario})


def submit(tick_id: int, action: str) -> dict:
    return call("hack26-submit-containment", {**_team_args(), "tick_id": tick_id, "action": action})


def scoreboard() -> dict:
    return call("hack26-get-scoreboard")


def list_tools() -> list[str]:
    async def go():
        cfg = scenario_server()
        async with MCPClient(HTTPTransport(url=cfg.url, headers=cfg.headers, streamable=cfg.streamable)) as c:
            return [t["name"] for t in await c.list_tools()]
    return run_async(go())
