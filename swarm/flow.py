"""Deterministic per-tick loop as a CrewAI Flow:

ingest -> triage (rule + Decisions) -> [reasoning crew] -> gate (rule + Decisions veto) -> submit -> escalate
"""
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Literal

from crewai.flow import Flow, listen, or_, router, start
from pydantic import BaseModel

from swarm import crew
from swarm.config import CONFIG
from swarm.env import team_handle
from swarm.log import log
from swarm.models import SEVERITY
from swarm.partners import decisions, one, scenario
from swarm.rules import rule_check


class TickState(BaseModel):
    tick: dict[str, Any] = {}
    dry_run: bool = False          # fixture mode: never submit, never post
    started: float = 0.0
    rules: dict[str, Any] = {}
    triage: dict[str, Any] = {}
    to_reason: list[str] = []
    reasoning: dict[str, Any] = {}
    sector_actions: dict[str, Any] = {}
    tick_action: str = "observe"
    submission: dict[str, Any] = {}
    escalations: list[dict[str, Any]] = []


_BACKGROUND = ThreadPoolExecutor(max_workers=1, thread_name_prefix="memory-writes")


class TickFlow(Flow[TickState]):
    @start()
    def ingest(self):
        tick = self.state.tick
        self.state.started = time.monotonic()
        budget = tick.get("swarm_budget", {})
        log.info("tick_ingested", tick_id=tick["tick_id"], sectors=[s["sector"] for s in tick["sector_reports"]],
                 max_active_agents=budget.get("max_active_agents"), sector_reports=tick["sector_reports"])
        for s in tick["sector_reports"]:
            self.state.rules[s["sector"]] = rule_check(s)
        # scouts recall only earlier ticks, so this tick's readings can be stored while triage runs
        self._memory_writes = _BACKGROUND.submit(
            lambda: [crew.remember_reading(tick["tick_id"], s) for s in tick["sector_reports"]])

    @router(ingest)
    def triage(self) -> Literal["needs_crew", "all_clear"]:
        tick = self.state.tick
        result = decisions.breach_probabilities(tick)
        self.state.triage = result
        probs = result.get("probabilities", {})
        for s in tick["sector_reports"]:
            name = s["sector"]
            p = probs.get(name)
            clear = (not self.state.rules[name]["passed"]) and p is not None and p < CONFIG.triage_skip_below
            if clear:
                self.state.sector_actions[name] = {
                    "action": "observe", "source": "triage",
                    "why": f"rule failed ({len(self.state.rules[name]['signals'])} signals) and p={p:.2f} < "
                           f"{CONFIG.triage_skip_below}",
                }
            else:
                self.state.to_reason.append(name)
        log.info("triage", tick_id=tick["tick_id"], latency_ms=result.get("latency_ms"),
                 error=result.get("error"), probabilities=probs, rules=self.state.rules,
                 to_crew=self.state.to_reason)
        return "needs_crew" if self.state.to_reason else "all_clear"

    @listen("needs_crew")
    def run_swarm(self):
        tick = self.state.tick
        sectors = [s for s in tick["sector_reports"] if s["sector"] in self.state.to_reason]
        max_agents = tick.get("swarm_budget", {}).get("max_active_agents", 8)
        with ThreadPoolExecutor(max_workers=1) as pool:  # keep crews off the flow's event-loop thread
            self.state.reasoning = pool.submit(crew.reason, tick, sectors, max_agents).result()
        log.info("scouts", tick_id=tick["tick_id"], reports=self.state.reasoning["scouts"])
        log.info("grounding", tick_id=tick["tick_id"], facts=self.state.reasoning["grounding"])
        log.info("commander", tick_id=tick["tick_id"], decisions=self.state.reasoning["decisions"],
                 agents_per_chunk=self.state.reasoning["agents_per_chunk"], max_active_agents=max_agents,
                 timings=self.state.reasoning["timings"])

    @listen(or_(run_swarm, "all_clear"))
    def gate(self):
        """False-alarm gate: contain/evacuate stand only if the rule passes AND Decisions p >= veto bar."""
        probs = self.state.triage.get("probabilities", {})
        decisions_down = "error" in self.state.triage
        for name, d in self.state.reasoning.get("decisions", {}).items():
            rule_ok = self.state.rules[name]["passed"]
            p = probs.get(name)
            proposed = d["action"]
            final, why = proposed, "allowed"
            if proposed != "observe":
                if not rule_ok:
                    final, why = "observe", f"vetoed: rule check failed {self.state.rules[name]['signals']}"
                elif decisions_down:
                    why = "allowed on rule check alone: Decisions unreachable"
                elif p is None or p < CONFIG.veto_min_prob:
                    final, why = "observe", f"vetoed: Decisions p={p} < {CONFIG.veto_min_prob}"
            self.state.sector_actions[name] = {**d, "proposed": proposed, "action": final, "gate": why}
            log.info("gate", tick_id=self.state.tick["tick_id"], sector=name, proposed=proposed, final=final,
                     rule_passed=rule_ok, probability=p, why=why)
        # a sector that went to the crew but got no decision at all stays safe
        for name in self.state.to_reason:
            self.state.sector_actions.setdefault(name, {"action": "observe", "source": "no_decision"})
        self.state.tick_action = max((a["action"] for a in self.state.sector_actions.values()),
                                     key=SEVERITY.__getitem__, default="observe")

    @listen(gate)
    def submit(self):
        tick_id = self.state.tick["tick_id"]
        if self.state.dry_run:
            self.state.submission = {"status": "dry_run", "action": self.state.tick_action}
        else:
            try:
                self.state.submission = scenario.submit(tick_id, self.state.tick_action)
            except Exception as e:
                self.state.submission = {"status": "error", "error": f"{type(e).__name__}: {str(e)[:200]}"}
        log.info("submission", tick_id=tick_id, action=self.state.tick_action, result=self.state.submission,
                 elapsed_s=round(time.monotonic() - self.state.started, 1))

    @listen(submit)
    def escalate(self):
        for name, a in self.state.sector_actions.items():
            if a["action"] not in ("contain", "evacuate"):
                continue
            message = f"{team_handle()} · {name} · {a['action']} · {a.get('rationale', '')}".strip()
            self.state.escalations.append(one.escalate(message, dry_run=self.state.dry_run))
        self._memory_writes.result()  # the next tick's scouts must see this tick's readings
        log.info("tick_done", tick_id=self.state.tick["tick_id"], action=self.state.tick_action,
                 sector_actions=self.state.sector_actions, escalations=self.state.escalations,
                 elapsed_s=round(time.monotonic() - self.state.started, 1))


def run_tick(tick: dict, dry_run: bool) -> TickState:
    flow = TickFlow()
    flow.kickoff(inputs={"tick": tick, "dry_run": dry_run})
    return flow.state
