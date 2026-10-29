# halloween-swarm — Track 1, The Exorcism Protocol

A supervised CrewAI swarm that pulls ticks from the scenario MCP, decides `contain | evacuate | observe`,
submits each decision, and escalates real breaches to Discord `#loft-incident` through One.

## Live result

Team `team-hollow-lantern-08`, scored by the scenario feed on 2026-10-28:

| Tick | Submitted | Expected | Verdict | False alarm | Discord |
|---|---|---|---|---|---|
| 900 (control) | observe | observe | correct, 1 | no | none |
| 142 | evacuate | evacuate | correct, 1 | no | posted via One |
| 143 | evacuate | evacuate | correct, 1 | no | posted via One |

Scoreboard after the run: 3 / 3, 0 false alarms. Watch it: open [docs/replay-live.html](docs/replay-live.html)
in a browser (a pixel-art replay of that live run; space plays, arrow keys step).
It runs with one command. CrewAI runs the swarm, Parasail makes every decision, You.com grounds it, and One takes the action. Three for three, zero false alarms. Thanks.

## Run

Needs Python 3.12 (CrewAI does not support 3.14) and Node 22.23.4+, 24.22.1+ or 26.12.1+ on `PATH`
(One's MCP server runs locally with `npx -y @withone/mcp`).

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                                # fill in TEAM_HANDLE and the four API keys
python scripts/verify_partners.py                   # one minimal call per partner, prints PASS/FAIL/SKIP

python main.py --fixture fixtures/control-tick.json     # offline: must observe, no Discord post
python main.py --fixture fixtures/outbreak-level-5.json # offline: must contain or evacuate
python main.py                                          # live: control-tick, then outbreak-level-5
```

Fixture runs never submit and never post to Discord; they log the message that would be sent.
A live run submits each tick once (the feed accepts one answer per tick) and posts real breaches to `#loft-incident`.

Every run writes:

- `logs/run-<ts>.jsonl`: one JSON object per event (scout reports, grounding, commander decision, gate, judgment, timings).
- `logs/replay-<ts>.html`: the 2D replay of that run. Rebuild it from any log with `python -m swarm.replay logs/run-<ts>.jsonl`.
- `logs/feed_queue.json`: every tick pulled from the live feed. The feed cursor only moves forward, so a tick that was
  pulled but not submitted (a crash, a network drop) is replayed and submitted on the next run.
- `.memory/run-<ts>/`: the scouts' scoped memory for that run.

## Architecture

```
            scenario MCP (MCPServerHTTP, streamable)            TickFlow (CrewAI Flow, one per tick)
                 hack26-get-tick ───────────────► ingest ── store each sector reading in /agent/scout-<sector>
                                                     │      deterministic rule check per sector
                                                     ▼
                         OpenAI Decisions ◄──── triage (router)
                         gpt-6-luna, ~0.3 s          │  rule failed AND p < TRIAGE_SKIP_BELOW → observe, no crew
                         one predicate per sector    │
                                     ┌───────────────┴─────────── needs_crew ───────────┐
                                     │ all_clear                                        ▼
                                     │                 ┌──────── reasoning (parallel) ──────────┐
                                     │                 │ scout × sector   Parasail, schema-locked │
                                     │                 │   read-only memory slice of own scope   │
                                     │                 │ field researcher Parasail + You.com     │
                                     │                 │   search (highlights), cited facts      │
                                     │                 └──────────────────┬──────────────────────┘
                                     │                                    ▼
                                     │                 commander: hierarchical Crew, Parasail manager_llm
                                     │                 → per sector action, confidence, rationale
                                     ▼                                    ▼
                                   gate: contain/evacuate stand only if rule passes AND p >= VETO_MIN_PROB
                                     ▼
                         hack26-submit-containment (one action per tick = most severe sector)
                                     ▼
                         One: find_one_actions → execute_one_action (channelId path var) → #loft-incident
                              retry once, then log the exact message to logs/manual_posts.txt
```

Agent budget: per chunk of sectors, `k scouts + 1 researcher + 1 manager <= swarm_budget.max_active_agents`;
sectors beyond that run in further chunks. Every Parasail call passes a global 5-RPM limiter
(`PARASAIL_RPM`), and every HTTP call backs off on 429/5xx using `Retry-After` / `X-RateLimit-Reset`.

## Why each partner is irreplaceable

| Partner | Role | Without it |
|---|---|---|
| CrewAI | Flow for the deterministic loop; hierarchical Crew, scoped Memory, MCP client for the feed | no swarm: one model, no partial knowledge, no supervision |
| Parasail | Every judgement: scouts, researcher, commander (`parasail-deepseek-v4-flash`: no hidden reasoning, faster than glm-53-flash), memory embeddings (`parasail-bge-m3`) | no decisions at all |
| You.com | Live web grounding of entity classes with cited highlights | the commander guesses what an entity is |
| One | The real-world action: posting escalations to Discord | breaches stay in a log nobody reads |
| OpenAI Decisions | Fast triage (skip the crew on clear non-breaches) and false-alarm veto | slow ticks and more false alarms |

## Tuning

All thresholds live in `.env` (see `.env.example` and `swarm/config.py`): `TRIAGE_SKIP_BELOW`, `VETO_MIN_PROB`,
`RULE_EMF_MIN`, `RULE_TEMP_MAX_C`, `RULE_SIGHTING_MIN_CONF`, `RULE_MIN_SIGNALS`, `PARASAIL_MODEL`, `PARASAIL_RPM`.
Design notes: [DESIGN.md](DESIGN.md).
