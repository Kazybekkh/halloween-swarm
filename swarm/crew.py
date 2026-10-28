"""The reasoning swarm: scoped scouts, a You.com grounding agent, and a hierarchical commander.

Partial knowledge by construction: each scout sees only its own sector reading and recalls only from
its own memory scope /agent/scout-<sector>.
"""
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor

from crewai import Agent, Crew, Process, Task
from crewai.memory import Memory

from swarm.env import ROOT
from swarm.log import log
from swarm.models import CommanderDecision, Grounding, ScoutReport
from swarm.partners.parasail import parasail_embedder, parasail_llm
from swarm.partners.youcom import YouSearchTool

_memory: Memory | None = None


def timed(timings: dict, name: str, fn, *args):
    """Run fn and record its wall time in timings[name] (seconds)."""
    started = time.monotonic()
    try:
        return fn(*args)
    finally:
        timings[name] = round(time.monotonic() - started, 2)


def memory() -> Memory:
    """One store for the swarm. The flow writes; scouts only get read-only slices of their own scope."""
    global _memory
    if _memory is None:
        _memory = Memory(
            llm=parasail_llm(),
            embedder=parasail_embedder(),
            storage=str(ROOT / ".memory" / log.path.stem),  # one store per run: no readings leak across runs
            query_analysis_threshold=10_000,  # recall = embed + vector search, no extra LLM call
            consolidation_threshold=1.0,      # merge (an LLM call) only exact duplicates; readings carry a tick id
        )
    return _memory


def scope_path(sector: str) -> str:
    return f"/agent/scout-{re.sub(r'[^a-z0-9_-]', '-', sector.lower())}"


def remember_reading(tick_id: int, sector: dict) -> None:
    """Store this tick's reading in the sector's own scope. Explicit fields skip LLM analysis."""
    memory().remember(
        f"tick {tick_id}: " + json.dumps({k: sector.get(k) for k in
                                          ("sensors", "sighting", "civilian_status", "containment_state")}),
        scope=scope_path(sector["sector"]),
        categories=["sector-reading"],
        importance=0.6,
        source=f"tick-{tick_id}",
    )


class ScopedScout(Agent):
    """Agent whose kickoff respects a read-only memory.

    crewai 1.15.2 Agent.kickoff runs _save_kickoff_to_memory, which spends a full LLM call on
    extract_memories even when the memory is read-only and the save is then discarded
    (agent/core.py:1646). For a read-only slice we skip it: same answer, one Parasail call less.
    """

    def _save_kickoff_to_memory(self, messages, output_text: str) -> None:
        if getattr(self.memory, "read_only", False):
            return
        super()._save_kickoff_to_memory(messages, output_text)


def make_scout(sector: str) -> Agent:
    return ScopedScout(
        role=f"Scout for sector {sector}",
        goal=f"Score the paranormal threat in sector {sector} from its sensors and sighting alone",
        backstory=(
            f"You are stationed in {sector} and know nothing about other sectors. "
            "You read EMF (milligauss), temperature, motion events, the sighting and civilian status, "
            "compare them with your sector's earlier readings, and report a calibrated threat score. "
            "Normal rooms read single-digit EMF and room temperature; crowds of humans are not entities."
        ),
        llm=parasail_llm(response_format=ScoutReport),
        memory=memory().slice(scopes=[scope_path(sector)], read_only=True),
        allow_delegation=False,
        max_iter=2,
        verbose=False,
    )


def scout_prompt(tick_id: int, sector: dict, history: list[str]) -> str:
    return (
        f"Tick {tick_id}. Your sector reading:\n{json.dumps(sector, indent=1)}\n\n"
        f"Your sector's earlier readings (oldest may be missing):\n" + ("\n".join(history) or "none") + "\n\n"
        "Return threat_level 0-10, civilians_at_risk, recommended_action "
        "(observe = nothing actionable; contain = active entity, civilians not in immediate danger; "
        "evacuate = civilians in immediate danger) and one short sentence of reasoning."
    )


def run_scout(agent: Agent, tick_id: int, sector: dict) -> ScoutReport:
    scope = scope_path(sector["sector"])
    matches = agent.memory.recall(f"{sector['sector']} sensor readings", scope=scope, limit=3, depth="shallow")
    history = [m.record.content for m in matches if m.record.source != f"tick-{tick_id}"]
    out = agent.kickoff(scout_prompt(tick_id, sector, history), response_format=ScoutReport)
    report = out.pydantic if isinstance(out.pydantic, ScoutReport) else ScoutReport.model_validate_json(out.raw)
    return report


def make_grounder() -> Agent:
    return Agent(
        role="Field researcher",
        goal="Ground each sighted entity class in live web facts about what it is and how to respond",
        backstory=(
            "You never guess. For each entity class you run one web search, then report one short fact per "
            "class with its source URL. If search finds nothing useful, say so in the fact."
        ),
        llm=parasail_llm(),
        tools=[YouSearchTool()],
        allow_delegation=False,
        max_iter=4,
        verbose=False,
    )


_grounded: dict[str, list] = {}  # entity class -> facts; an entity class is grounded once per run


def run_grounding(agent: Agent, entity_classes: list[str]) -> Grounding:
    todo = [e for e in entity_classes if e not in _grounded]
    if todo:
        prompt = (
            "Entity classes sighted this tick: " + ", ".join(todo) + ".\n"
            "For each, run ONE web search (for example '<entity class> paranormal entity containment response') "
            "and return one short cited fact that helps choose between observe, contain and evacuate."
        )
        out = agent.kickoff(prompt, response_format=Grounding)
        fresh = out.pydantic if isinstance(out.pydantic, Grounding) else Grounding.model_validate_json(out.raw)
        for e in todo:
            _grounded[e] = [f for f in fresh.facts if f.entity_class == e] or fresh.facts
    else:
        log.info("grounding_reused", entity_classes=entity_classes)
    seen, facts = set(), []
    for e in entity_classes:
        for f in _grounded.get(e, []):
            key = (f.entity_class, f.url, f.fact)
            if key not in seen:
                seen.add(key)
                facts.append(f)
    return Grounding(facts=facts)


def run_commander(tick: dict, sectors: list[dict], scouts: dict[str, Agent], grounder: Agent,
                  reports: dict[str, ScoutReport], grounding: Grounding) -> CommanderDecision:
    """Hierarchical crew: a Parasail manager weighs scout reports and grounding, may query a scout."""
    briefing = {
        "tick_id": tick["tick_id"],
        "civilian_status": {s["sector"]: s.get("civilian_status") for s in sectors},
        "scout_reports": {k: v.model_dump() for k, v in reports.items()},
        "grounding": grounding.model_dump()["facts"],
    }
    task = Task(
        description=(
            "You command the containment swarm. Decide an action for EVERY sector listed in scout_reports.\n"
            f"{json.dumps(briefing, indent=1)}\n\n"
            "Actions: observe (nothing actionable), contain (active entity, civilians not in immediate danger), "
            "evacuate (civilians in immediate danger). False alarms are penalised and so are lost civilians. "
            "The scout reports are already in front of you: delegate to a scout only if its report is missing "
            "or contradicts the grounding. Give a confidence 0-1 and a one-line rationale citing the evidence."
        ),
        expected_output="A decision (sector, action, confidence, rationale) for every sector in scout_reports.",
        output_pydantic=CommanderDecision,
    )
    crew = Crew(
        agents=[scouts[s["sector"]] for s in sectors] + [grounder],
        tasks=[task],
        process=Process.hierarchical,
        manager_llm=parasail_llm(),
        verbose=False,
    )
    out = crew.kickoff()
    return out.pydantic if isinstance(out.pydantic, CommanderDecision) else CommanderDecision.model_validate_json(out.raw)


def reason(tick: dict, sectors: list[dict], max_agents: int) -> dict:
    """Run scouts and grounding in parallel, then the commander. Chunk sectors to respect the agent budget.

    Active agents per chunk: k scouts + 1 grounder + 1 manager <= max_agents.
    """
    per_chunk = max(1, max_agents - 2)
    entity_classes = sorted({(s.get("sighting") or {}).get("entity_class") for s in sectors} - {None})
    grounder = make_grounder()
    result = {"scouts": {}, "grounding": None, "decisions": {}, "agents_per_chunk": [], "errors": [], "timings": {}}
    t = result["timings"]

    with ThreadPoolExecutor(max_workers=per_chunk + 1) as pool:
        grounding_future = pool.submit(timed, t, "grounding", run_grounding, grounder, entity_classes)
        chunks = [sectors[i:i + per_chunk] for i in range(0, len(sectors), per_chunk)]
        grounding = None
        for chunk in chunks:
            scouts = {s["sector"]: make_scout(s["sector"]) for s in chunk}
            result["agents_per_chunk"].append(len(scouts) + 2)
            futures = {s["sector"]: pool.submit(timed, t, f"scout:{s['sector']}", run_scout, scouts[s["sector"]],
                                                tick["tick_id"], s) for s in chunk}
            reports = {}
            for name, fut in futures.items():
                try:
                    reports[name] = fut.result()
                except Exception as e:
                    result["errors"].append(f"scout {name}: {type(e).__name__}: {str(e)[:200]}")
            result["scouts"].update({k: v.model_dump() for k, v in reports.items()})

            if grounding is None:
                try:
                    grounding = grounding_future.result()
                except Exception as e:
                    result["errors"].append(f"grounding: {type(e).__name__}: {str(e)[:200]}")
                    grounding = Grounding()
                result["grounding"] = grounding.model_dump()

            for attempt in (1, 2):  # retry once: Parasail can return an empty completion
                try:
                    decision = timed(t, f"commander:attempt{attempt}", run_commander,
                                     tick, chunk, scouts, grounder, reports, grounding)
                    for d in decision.decisions:
                        result["decisions"][d.sector] = {**d.model_dump(), "source": "commander",
                                                         "attempt": attempt}
                    break
                except Exception as e:
                    err = f"{type(e).__name__}: {str(e)[:200]}"
                    empty = "None or empty" in str(e)
                    result["errors"].append(f"commander attempt {attempt}: {err}")
                    if not empty or attempt == 2:
                        break
                    log.warn("commander_retry", tick_id=tick["tick_id"], error=err)

            # any sector the commander missed falls back to its own scout's recommendation
            for s in chunk:
                name = s["sector"]
                if name not in result["decisions"] and name in reports:
                    r = reports[name]
                    result["decisions"][name] = {"sector": name, "action": r.recommended_action,
                                                 "confidence": r.threat_level / 10, "rationale": r.reasoning,
                                                 "source": "scout_fallback"}
    for err in result["errors"]:
        log.warn("reasoning_error", tick_id=tick["tick_id"], error=err)
    return result
