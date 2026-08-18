from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DraftReadiness:
    current_team_count: int
    target_team_count: int

    @property
    def production_ready(self) -> bool:
        return self.current_team_count == self.target_team_count

    @property
    def status(self) -> str:
        return "READY" if self.production_ready else "PREP"

    @property
    def teams_remaining(self) -> int:
        return max(self.target_team_count - self.current_team_count, 0)
