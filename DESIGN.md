# Design decisions

## OpenAI Decisions API: triage before the crew, veto after it

Source for the request shape: https://developers.openai.com/api/docs/guides/decisions
(`POST /v1/decisions`, model `gpt-6-luna`, `input` with an `input_json` part, `questions` list).

Why: the CrewAI crew is the slow part of a tick (one scout per sector, a You.com grounding
agent, a Parasail commander; Parasail free tier is 5 RPM per the event guide §3.3).
Containment speed is scored. OpenAI reports about 150 ms per decision (vendor figure, not
independently measured), so we time it ourselves and log the latency per tick.

Per tick:

1. **Triage (before the crew).** One Decisions call per tick, one predicate question per
   sector ("Is this a genuine paranormal breach requiring action, not a harmless control
   reading?"), plus the deterministic sensor rule check.
   - If the rule check fails AND probability < `TRIAGE_SKIP_BELOW` (start 0.15) for a sector,
     that sector is submitted as `observe` immediately. No crew, no Discord post.
   - Every other sector goes to the crew. The cutoff is strict on purpose: a missed breach
     costs more than an extra crew run.
2. **Crew.** Parasail scouts, You.com grounding, Parasail commander. Parasail makes the
   decision.
3. **Veto (after the commander).** `contain` / `evacuate` only stand if the rule check passes
   AND probability >= `VETO_MIN_PROB` (start 0.5). Otherwise downgrade to `observe` and log why.
   Reuse the triage probability; do not call Decisions twice for the same tick.
4. If Decisions is unreachable, triage sends everything to the crew and the veto falls back
   to the rule check alone. This is logged per tick.

Both thresholds live in `.env` / config and are tuned in Phase 3 from scoreboard judgments.
