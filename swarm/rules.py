"""Deterministic breach rule: count independent anomaly signals in one sector report."""
import re

from swarm.config import CONFIG


def civilians_count(status: str | None) -> int:
    """'4 pinned' -> 4, 'clear' -> 0, any other non-clear text -> 1."""
    if not status or status.strip().lower() == "clear":
        return 0
    m = re.search(r"\d+", status)
    return int(m.group()) if m else 1


def rule_check(sector: dict) -> dict:
    sensors = sector.get("sensors") or {}
    sighting = sector.get("sighting") or {}
    signals = []

    emf = sensors.get("emf_milligauss")
    if emf is not None and emf >= CONFIG.emf_min:
        signals.append(f"emf {emf} >= {CONFIG.emf_min}")
    temp = sensors.get("temp_c")
    if temp is not None and temp <= CONFIG.temp_max_c:
        signals.append(f"temp {temp}C <= {CONFIG.temp_max_c}C")

    state = (sector.get("containment_state") or "").lower()
    if state and state not in CONFIG.safe_containment_states:
        signals.append(f"containment_state {state}")

    civilians = civilians_count(sector.get("civilian_status"))
    if civilians:
        signals.append(f"civilians at risk: {sector.get('civilian_status')}")

    entity = (sighting.get("entity_class") or "").lower()
    conf = sighting.get("confidence") or 0
    if entity and not re.search(CONFIG.benign_entity_pattern, entity) and conf >= CONFIG.sighting_min_conf:
        signals.append(f"sighting {entity} conf {conf}")

    return {
        "passed": len(signals) >= CONFIG.rule_min_signals,
        "signals": signals,
        "civilians": civilians,
    }
