"""Tunable thresholds and model settings. Every value can be overridden in .env."""
import os
from dataclasses import dataclass, field

from swarm import env  # noqa: F401  (loads .env)


def _f(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _s(name: str, default: str) -> str:
    return os.environ.get(name, "").strip() or default


@dataclass(frozen=True)
class Config:
    # Parasail (guide §3.3)
    parasail_base: str = "https://api.parasail.io/v1"
    parasail_model: str = field(default_factory=lambda: _s("PARASAIL_MODEL", "parasail-deepseek-v4-flash"))
    parasail_embed_model: str = field(default_factory=lambda: _s("PARASAIL_EMBED_MODEL", "parasail-bge-m3"))
    parasail_rpm: float = field(default_factory=lambda: _f("PARASAIL_RPM", 5))  # free tier is 5 RPM
    parasail_max_tokens: int = field(default_factory=lambda: int(_f("PARASAIL_MAX_TOKENS", 4000)))

    # Deterministic sensor rule check
    emf_min: float = field(default_factory=lambda: _f("RULE_EMF_MIN", 30))
    temp_max_c: float = field(default_factory=lambda: _f("RULE_TEMP_MAX_C", 5))
    sighting_min_conf: float = field(default_factory=lambda: _f("RULE_SIGHTING_MIN_CONF", 0.5))
    rule_min_signals: int = field(default_factory=lambda: int(_f("RULE_MIN_SIGNALS", 2)))
    benign_entity_pattern: str = field(
        default_factory=lambda: _s("RULE_BENIGN_ENTITY_PATTERN", r"human|crowd|staff|visitor|animal|maintenance|none")
    )
    safe_containment_states: tuple[str, ...] = field(
        default_factory=lambda: tuple(_s("RULE_SAFE_STATES", "holding,contained,secure").split(","))
    )

    # OpenAI Decisions (DESIGN.md)
    decisions_model: str = "gpt-6-luna"
    triage_skip_below: float = field(default_factory=lambda: _f("TRIAGE_SKIP_BELOW", 0.15))
    veto_min_prob: float = field(default_factory=lambda: _f("VETO_MIN_PROB", 0.5))


CONFIG = Config()
