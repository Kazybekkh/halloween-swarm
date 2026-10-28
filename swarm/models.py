"""Schemas shared by agents and the flow. Scout and commander outputs are schema-locked."""
from typing import Literal

from pydantic import BaseModel, Field

Action = Literal["contain", "evacuate", "observe"]
SEVERITY = {"observe": 0, "contain": 1, "evacuate": 2}


class ScoutReport(BaseModel):
    threat_level: int = Field(description="0 (nothing) to 10 (catastrophic)")
    civilians_at_risk: int = Field(description="Number of civilians in danger in this sector")
    recommended_action: Action
    reasoning: str = Field(description="One short sentence")


class GroundedFact(BaseModel):
    entity_class: str
    fact: str = Field(description="One short sentence relevant to choosing a response")
    url: str


class Grounding(BaseModel):
    facts: list[GroundedFact] = []


class SectorDecision(BaseModel):
    sector: str
    action: Action
    confidence: float = Field(description="0 to 1")
    rationale: str = Field(description="One line")


class CommanderDecision(BaseModel):
    decisions: list[SectorDecision]
