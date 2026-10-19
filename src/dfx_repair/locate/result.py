"""Localization result types shared by all locators (rules, LLM, baselines)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Location:
    """A source range relative to the app root; start == end for a single line."""

    file: str
    start_line: int
    end_line: int
    role: str = "root_cause"

    @classmethod
    def at(cls, file: str, line: int, role: str = "root_cause") -> Location:
        return cls(file, line, line, role)


@dataclass(frozen=True)
class LocateResult:
    """Output of a locator. root_cause_locations is ordered; the first one is the main answer."""

    case_id: str
    method: str
    crash_location: Location | None
    root_cause_locations: tuple[Location, ...]
    evidence: tuple[str, ...] = field(default_factory=tuple)
    assumptions: tuple[str, ...] = field(default_factory=tuple)
    score: float = 0.0  # heuristic score, not a probability
    source_hash: str = ""

    @property
    def primary(self) -> Location | None:
        return self.root_cause_locations[0] if self.root_cause_locations else None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> LocateResult:
        crash = data.get("crash_location")
        return cls(
            case_id=data["case_id"],
            method=data["method"],
            crash_location=Location(**crash) if crash else None,
            root_cause_locations=tuple(Location(**loc) for loc in data["root_cause_locations"]),
            evidence=tuple(data.get("evidence", ())),
            assumptions=tuple(data.get("assumptions", ())),
            score=data.get("score", 0.0),
            source_hash=data.get("source_hash", ""),
        )
