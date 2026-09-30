from __future__ import annotations

from datetime import datetime
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


class DraftPickSubmitRequest(BaseModel):
    pick_id: str
    expected_owner_team_key: str
    yahoo_player_key: str
    expected_pick_kind: Literal["FA", "QO", "POACH"] | None = None


class DraftPickSubmitResponse(BaseModel):
    result_status: str
    executed_pick_id: str
    selecting_team_key: str
    selected_player_key: str
    selected_pick_kind: str
    next_pick_id: str | None
    selected_at_utc: datetime | None


class CommissionerPrincipal(BaseModel):
    is_authenticated: bool
    role: Literal["public", "commissioner"]
    league_key: str
    season_year: int
    display_name: str
    acting_as: str


class ManagerGatewayLink(BaseModel):
    franchise_id: int
    team_key: str
    team_name: str
    owner_name: str | None
    is_active: bool
    claim_count: int
    last_claimed_at_utc: str | None
    manager_url: str
class DraftOrderSlot(BaseModel):
    slot_number: int
    team_key: str
    team_name: str


class CommissionerDraftOrderState(BaseModel):
    draft_key: str
    status: str
    manager_count: int
    draft_order_mode: str
    first_standard_round: int
    selection_count: int
    can_rebase: bool
    lock_reason: str | None
    slots: list[DraftOrderSlot]
