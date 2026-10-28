"""Exorcism Protocol swarm.

  python main.py                                   live: pull ticks from the scenario MCP, submit, escalate
  python main.py --scenario outbreak-level-5       live, one feed only
  python main.py --fixture fixtures/control-tick.json   offline dry run: no submission, no Discord post
"""
import argparse
import json
import os
import sys
import time

os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

from swarm.env import team_handle  # noqa: E402  (loads .env)
from swarm.flow import run_tick  # noqa: E402
from swarm.log import LOG_DIR, log  # noqa: E402
from swarm import replay  # noqa: E402
from swarm.partners import scenario  # noqa: E402

SCENARIOS = ["control-tick", "outbreak-level-5"]


def summarise(state) -> dict:
    return {"tick_id": state.tick["tick_id"], "action": state.tick_action,
            "sectors": {k: v["action"] for k, v in state.sector_actions.items()},
            "escalations": [e["status"] for e in state.escalations], "submission": state.submission}


def run_fixture(path: str) -> list[dict]:
    data = json.load(open(path))
    log.info("run_start", mode="fixture", file=path, scenario=data.get("scenario"))
    return [summarise(run_tick(tick, dry_run=True)) for tick in data["ticks"]]


QUEUE = LOG_DIR / "feed_queue.json"  # every fetched tick, so a crash never loses one (the cursor only moves forward)


def load_queue() -> dict:
    try:
        return json.loads(QUEUE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"ticks": {}, "submitted": []}


def save_queue(q: dict) -> None:
    QUEUE.write_text(json.dumps(q, indent=1))


def process(q: dict, key: str, out: list) -> None:
    state = run_tick(q["ticks"][key], dry_run=False)
    out.append(summarise(state))
    if state.submission.get("status") != "error":
        q["submitted"].append(key)
        save_queue(q)


def run_live(scenarios: list[str], max_ticks: int) -> list[dict]:
    log.info("run_start", mode="live", team=team_handle(), scenarios=scenarios)
    q, out = load_queue(), []

    # replay ticks fetched earlier but never submitted (a late submission still counts, guide §10)
    for key in [k for k in q["ticks"] if k not in q["submitted"]]:
        log.info("replay_queued_tick", key=key)
        process(q, key, out)

    for name in scenarios:
        for _ in range(max_ticks):
            resp = scenario.get_tick(name)
            tick = resp.get("tick", resp)  # the feed wraps it: {"team", "scenario", "tick": {...}, "exhausted"}
            if tick.get("tick_id") is None:
                log.warn("bad_tick", scenario=name, response=resp)
                break
            key = f"{name}:{tick['tick_id']}"
            if key not in q["ticks"]:
                q["ticks"][key] = tick
                save_queue(q)
            already = key in q["submitted"]
            if not already:
                process(q, key, out)
            if resp.get("exhausted") or already:  # an exhausted feed repeats its last tick
                log.info("feed_exhausted", scenario=name, last_tick=tick["tick_id"], exhausted=resp.get("exhausted"))
                break
            time.sleep(1)  # never hammer the feed
    board = scenario.scoreboard()
    log.info("scoreboard", board=board)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fixture", help="run offline on a fixture file (dry run)")
    ap.add_argument("--scenario", choices=SCENARIOS, help="live: only this feed (default: all)")
    ap.add_argument("--max-ticks", type=int, default=50, help="live: safety cap per feed")
    args = ap.parse_args()

    results = run_fixture(args.fixture) if args.fixture else run_live(
        [args.scenario] if args.scenario else SCENARIOS, args.max_ticks)
    print(json.dumps(results, indent=2, default=str))
    print(f"log: {log.path}", file=sys.stderr)
    try:
        print(f"replay: {replay.write(log.path)}", file=sys.stderr)  # 2D replay of this run for the demo
    except Exception as e:  # a replay problem must never fail the run
        print(f"replay not written: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
