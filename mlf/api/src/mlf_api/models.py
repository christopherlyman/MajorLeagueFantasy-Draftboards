from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    database: str


class GatewayPrincipal(BaseModel):
    is_authenticated: bool
    role: Literal["public", "manager"]
    league_key: str
    season_year: int
    franchise_id: int | None
    team_key: str | None
    team_name: str | None
    display_name: str
    acting_as: str