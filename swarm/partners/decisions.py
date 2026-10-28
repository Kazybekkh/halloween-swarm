"""OpenAI Decisions API: fast per-sector breach probability, used as triage and as the veto.

Request shape: https://developers.openai.com/api/docs/guides/decisions
"""
import time

from swarm.config import CONFIG
from swarm.env import need
from swarm.log import log
from swarm.ratelimit import request_with_backoff

URL = "https://api.openai.com/v1/decisions"
PREDICATE = "Is this a genuine paranormal breach requiring action, not a harmless control reading?"


def breach_probabilities(tick: dict) -> dict:
    """Return {"probabilities": {sector: p}, "latency_ms": ..} or {"error": ..} if unreachable."""
    sectors = [s["sector"] for s in tick["sector_reports"]]
    questions = [
        {
            "type": "predicate",
            "name": f"sector_{i}",
            "instructions": f"Consider only sector '{name}' in the tick. {PREDICATE}",
        }
        for i, name in enumerate(sectors)
    ]
    body = {
        "model": CONFIG.decisions_model,
        "input": [{
            "role": "user",
            "content": [
                {"type": "input_text", "text": "Facility sensor tick from a paranormal containment simulation."},
                {"type": "input_json", "data": tick},
            ],
        }],
        "questions": questions,
    }
    started = time.monotonic()
    try:
        resp = request_with_backoff(
            "POST", URL, attempts=3, timeout=20.0,
            headers={"Authorization": f"Bearer {need('OPENAI_API_KEY')}", "Content-Type": "application/json"},
            json=body,
        )
        latency_ms = round((time.monotonic() - started) * 1000)
        if resp.status_code != 200:
            return {"error": f"HTTP {resp.status_code}: {resp.text[:200]}", "latency_ms": latency_ms}
        probs = {}
        for ans in resp.json().get("answers", []):
            idx = int(ans["name"].split("_")[1])
            # a refusal carries no probability; treat as unknown so neither triage nor veto trusts it
            probs[sectors[idx]] = ans.get("probability") if ans.get("type") == "predicate" else None
        return {"probabilities": probs, "latency_ms": latency_ms}
    except Exception as e:
        log.warn("decisions_error", error=str(e)[:200])
        return {"error": str(e)[:200], "latency_ms": round((time.monotonic() - started) * 1000)}
