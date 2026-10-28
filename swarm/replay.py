"""2D replay of a run log: a facility map that steps through each tick.

  python -m swarm.replay logs/run-<ts>.jsonl            writes logs/replay-<ts>.html
  python -m swarm.replay --share logs/run-<ts>.jsonl    writes logs/replay-<ts>-share.html (no document skeleton)

Every value on the page comes from the log. Older logs without raw readings are filled in from
logs/feed_queue.json and fixtures/*.json by tick id.
"""
import json
import sys
from pathlib import Path

from swarm.env import ROOT

TEMPLATE = Path(__file__).with_name("replay_template.html")


def _known_ticks() -> dict[int, dict]:
    known: dict[int, dict] = {}
    for f in sorted((ROOT / "fixtures").glob("*.json")):
        for t in json.loads(f.read_text()).get("ticks", []):
            known.setdefault(t["tick_id"], t)
    queue = ROOT / "logs" / "feed_queue.json"
    if queue.exists():
        for t in json.loads(queue.read_text()).get("ticks", {}).values():
            known[t["tick_id"]] = t  # live data wins over fixtures
    return known


def build(log_path: Path) -> dict:
    events = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
    known = _known_ticks()
    run = {"log": log_path.name, "mode": None, "team": None, "ticks": [], "scoreboard": None}
    cur: dict | None = None

    def sector(name: str) -> dict:
        for s in cur["sectors"]:
            if s["name"] == name:
                return s
        s = {"name": name, "reading": None, "rule": None, "p": None, "triaged": False,
             "scout": None, "decision": None, "gate": None, "final": None}
        cur["sectors"].append(s)
        return s

    for e in events:
        ev = e["event"]
        if ev == "run_start":
            run["mode"], run["team"] = e.get("mode"), e.get("team")
        elif ev == "scoreboard":
            run["scoreboard"] = e.get("board")
        elif ev == "tick_ingested":
            cur = {"tick_id": e["tick_id"], "t0": e["ts"], "max_agents": e.get("max_active_agents"),
                   "sectors": [], "llm_calls": [], "throttle_s": 0.0, "searches": 0, "grounding": [],
                   "timings": {}, "agents_per_chunk": [], "triage_ms": None, "decisions_error": None,
                   "tick_action": None, "submission": None, "escalations": [], "elapsed_s": None,
                   "grounding_reused": False}
            reports = e.get("sector_reports") or (known.get(e["tick_id"]) or {}).get("sector_reports") or []
            by_name = {r["sector"]: r for r in reports}
            for name in e.get("sectors", []):
                sector(name)["reading"] = by_name.get(name)
            run["ticks"].append(cur)
        elif cur is None:
            continue
        elif ev == "triage":
            cur["triage_ms"], cur["decisions_error"] = e.get("latency_ms"), e.get("error")
            for name, rule in (e.get("rules") or {}).items():
                s = sector(name)
                s["rule"] = rule
                s["p"] = (e.get("probabilities") or {}).get(name)
                s["triaged"] = name not in (e.get("to_crew") or [])
        elif ev == "llm_call":
            cur["llm_calls"].append({k: e.get(k) for k in ("role", "wait_ms", "call_ms")})
        elif ev == "parasail_throttle":
            cur["throttle_s"] += e.get("wait_s") or 0
        elif ev == "youcom_search":
            cur["searches"] += 1
        elif ev == "grounding_reused":
            cur["grounding_reused"] = True
        elif ev == "scouts":
            for name, rep in (e.get("reports") or {}).items():
                sector(name)["scout"] = rep
        elif ev == "grounding":
            cur["grounding"] = (e.get("facts") or {}).get("facts", [])
        elif ev == "commander":
            cur["timings"] = e.get("timings") or {}
            cur["agents_per_chunk"] = e.get("agents_per_chunk") or []
            for name, d in (e.get("decisions") or {}).items():
                sector(name)["decision"] = d
        elif ev == "gate":
            sector(e["sector"])["gate"] = {k: e.get(k) for k in ("proposed", "final", "why", "probability")}
        elif ev == "submission":
            cur["tick_action"], cur["submission"] = e.get("action"), e.get("result")
        elif ev in ("escalation_posted", "escalation_dry_run", "escalation_manual_post_needed"):
            cur.setdefault("_messages", []).append(e.get("message"))
        elif ev == "tick_done":
            cur["elapsed_s"] = e.get("elapsed_s")
            for name, a in (e.get("sector_actions") or {}).items():
                sector(name)["final"] = a.get("action")
            messages = cur.pop("_messages", [])  # a posted result carries no message; its log event does
            cur["escalations"] = [{"status": x.get("status"),
                                   "message": x.get("message") or (messages[i] if i < len(messages) else None)}
                                  for i, x in enumerate(e.get("escalations") or [])]
    return run


def render(log_path: Path, fragment: bool = False) -> str:
    """The replay page. fragment=True drops the document skeleton for hosts that add their own."""
    data = json.dumps(build(log_path)).replace("</", "<\\/")  # keep the data inert inside <script>
    page = TEMPLATE.read_text().replace("/*__RUN_DATA__*/null", data)
    if fragment:
        for tag in ("<!doctype html>", '<html lang="en">', "<head>", "</head>", "<body>", "</body>", "</html>",
                    '<meta charset="utf-8">', '<meta name="viewport" content="width=device-width, initial-scale=1">'):
            page = page.replace(tag, "", 1)
    return page.strip() + "\n"


def write(log_path: Path, fragment: bool = False) -> Path:
    stem = log_path.name.replace("run-", "replay-").replace(".jsonl", "")
    out = log_path.with_name(stem + ("-share" if fragment else "") + ".html")
    out.write_text(render(log_path, fragment))
    return out


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--share"]
    if len(args) != 1:
        sys.exit(__doc__)
    print(write(Path(args[0]).resolve(), fragment="--share" in sys.argv))
