"""One: post escalations to Discord #loft-incident through the local @withone/mcp server.

Flow (guide §4 and one-hackathon SKILL.md): list_one_integrations -> find_one_actions with the intent
alone -> execute_one_action with channelId as a PATH variable. Retry once, then fall back to a manual post.
"""
import json
import re
import os
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from swarm.env import ROOT, need, one_mcp_env
from swarm.log import log
from swarm.partners import run_async

MANUAL_POSTS = ROOT / "logs" / "manual_posts.txt"
INTENT = "send a message to a channel"


def _text(result) -> str:
    return "".join(getattr(c, "text", "") for c in result.content)


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from _walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v)


def _json(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


async def _post(content: str) -> dict:
    params = StdioServerParameters(command="npx", args=["-y", "@withone/mcp"], env=one_mcp_env())
    with open(os.devnull, "w") as errlog:
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as s:
                await s.initialize()

                integrations = _json(_text(await s.call_tool("list_one_integrations", {})))
                conn = next((d for d in _walk(integrations)
                             if d.get("platform") == "discord" and (d.get("key") or d.get("connectionKey"))), None)
                if not conn:
                    raise RuntimeError("no Discord connection visible to this ONE_API_KEY")
                connection_key = conn.get("key") or conn.get("connectionKey")

                found_text = _text(await s.call_tool("find_one_actions", {
                    "requests": [{"platform": "discord", "intent": INTENT}],
                    "task": "post an incident alert to a channel",
                }))
                # find_one_actions answers in Markdown: "### Create a Message ... · `POST /channels/{{channelId}}/
                # messages` · actionId: `conn_mod_def::...`". Prefer the channel-messages POST; JSON as a fallback.
                m = (re.search(r"POST /channels/\{\{channelId\}\}/messages`[^\n]*?actionId: `([^`]+)`", found_text)
                     or re.search(r"actionId: `([^`]+)`", found_text))
                action = {"actionId": m.group(1)} if m else next(
                    (d for d in _walk(_json(found_text)) if d.get("actionId")), None)
                if not action:
                    raise RuntimeError(f"find_one_actions returned no actionId: {found_text[:200]}")

                res = await s.call_tool("execute_one_action", {
                    "platform": "discord",
                    "actionId": action["actionId"],
                    "connectionKey": connection_key,
                    "pathVariables": {"channelId": need("DISCORD_CHANNEL_ID")},  # path variable, never a body field
                    "data": {"content": content},
                })
                text = _text(res)
                if res.isError:
                    raise RuntimeError(text[:300])
                discord_error = platform_error(text)
                if discord_error:
                    raise RuntimeError(discord_error)
                return {"action_id": action["actionId"], "response": text[:300]}


def platform_error(text: str) -> str | None:
    """The platform's own failure inside a successful One call, or None.

    One passes the platform's response through unchanged (one-hackathon SKILL.md, "Errors"), so a call that
    One itself completes can still carry Discord's rejection, e.g.
    {"responseData": {"message": "401: Unauthorized"}, "status": 401, ...}.
    """
    data = _json(text)
    if not isinstance(data, dict):
        return None
    status = data.get("status")
    if isinstance(status, str) and status.isdigit():
        status = int(status)
    if isinstance(status, int) and status >= 400:
        body = data.get("responseData")
        detail = body.get("message") if isinstance(body, dict) else body
        return f"Discord rejected the post: HTTP {status}: {str(detail)[:150]}"
    return None


def escalate(content: str, dry_run: bool = False) -> dict:
    if dry_run:
        log.info("escalation_dry_run", message=content)
        return {"status": "dry_run", "message": content}
    last_error = None
    for attempt in (1, 2):  # one retry, then manual fallback
        try:
            result = run_async(_post(content))
            log.info("escalation_posted", attempt=attempt, message=content)
            return {"status": "posted", "attempt": attempt, "message": content, **result}
        except Exception as e:
            last_error = f"{type(e).__name__}: {str(e)[:200]}"
            log.warn("escalation_failed", attempt=attempt, error=last_error)
    MANUAL_POSTS.parent.mkdir(exist_ok=True)
    with MANUAL_POSTS.open("a") as fh:
        fh.write(content + "\n")
    log.warn("escalation_manual_post_needed", message=content, file=str(MANUAL_POSTS))
    return {"status": "manual", "error": last_error, "message": content}
