import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class Announcement:
    event_id: str
    event_type: str
    occurred_at: str
    content: str
    banner_year: int | None = None


def ordinal(number: int) -> str:
    remainder_100 = number % 100

    if 11 <= remainder_100 <= 13:
        suffix = "th"
    else:
        suffix = {
            1: "st",
            2: "nd",
            3: "rd",
        }.get(number % 10, "th")

    return f"{number}{suffix}"


def normalize_pool_type(value: Any) -> str:
    text = str(value or "").strip().replace("_", " ")

    if not text:
        return "Lottery"

    return text.title()


def format_lottery_announcement(
    lottery_pick: dict[str, Any],
) -> Announcement:
    run_id = str(lottery_pick["run_id"])
    reveal_order = int(lottery_pick["reveal_order"])
    pick_number = int(lottery_pick["pick_number"])
    team_name = str(
        lottery_pick.get("team_name")
        or lottery_pick["team_key"]
    )
    pool_type = normalize_pool_type(
        lottery_pick.get("pool_type")
    )
    revealed_at = str(
        lottery_pick.get("revealed_at_utc") or ""
    )

    content = (
        f"\U0001F3B0 **NFFL Draft Lottery \u2014 Pick {pick_number}**\n"
        f"**{team_name}** has drawn the "
        f"**{ordinal(pick_number)} selection**.\n"
        f"*{pool_type} pool*"
    )

    return Announcement(
        event_id=f"lottery:{run_id}:{reveal_order}",
        event_type="LOTTERY_REVEAL",
        occurred_at=revealed_at,
        content=content,
    )


def format_draft_start_announcement(
    draft: dict[str, Any],
) -> Announcement:
    draft_key = str(draft["draft_key"])
    season_year = 2026
    occurred_at = str(
        draft.get("updated_at_utc") or ""
    )

    content = "\n".join(
        [
            (
                f"\U0001F3C8 **THE {season_year} NFFL DRAFT "
                "IS OFFICIALLY UNDERWAY!**"
            ),
            (
                "The clock is running and the road to the "
                "championship starts now."
            ),
            (
                "Good luck, managers \u2014 make every pick count."
            ),
        ]
    )

    return Announcement(
        event_id=f"draft_start:{draft_key}",
        event_type="DRAFT_START",
        occurred_at=occurred_at,
        content=content,
        banner_year=season_year,
    )


def manager_display(
    team_key: str,
    team_name: str,
    manager_mentions: dict[str, str] | None = None,
) -> str:
    mentions = manager_mentions or {}
    discord_user_id = mentions.get(team_key, "").strip()

    if discord_user_id:
        return (
            f"<@{discord_user_id}> \u2014 "
            f"**{team_name}**"
        )

    return f"**{team_name}**"


def format_draft_pick_announcement(
    draft_key: str,
    selection: dict[str, Any],
    next_pick: dict[str, Any] | None,
    manager_mentions: dict[str, str] | None = None,
    *,
    season_year: int | None = None,
    is_final_pick: bool = False,
) -> Announcement:
    pick_id = str(selection["pick_id"])
    team_name = str(
        selection.get("selecting_team_name")
        or selection["selecting_team_key"]
    )
    player_name = str(
        selection.get("selected_player_name")
        or selection["yahoo_player_key"]
    )
    position = str(
        selection.get("selected_primary_position")
        or ""
    ).strip()
    pick_kind = str(
        selection.get("pick_kind") or ""
    ).strip().upper()
    selected_at = str(
        selection.get("selected_at_utc") or ""
    )

    player_display = player_name

    if position:
        player_display += f" ({position})"

    if pick_kind == "QO":
        action_icon = "\U0001F512"
        action_prefix = ""
        action_word = "retains"
    elif pick_kind == "POACH":
        action_icon = "\U0001F95A"
        action_prefix = "POACH! "
        action_word = "poaches"
    else:
        action_icon = "\U0001F3C8"
        action_prefix = ""
        action_word = "selects"

    headline = (
        f"{action_icon} **{action_prefix}{pick_id} "
        f"\u2014 {team_name} "
        f"{action_word} {player_display}**"
    )

    if is_final_pick:
        lines = [
            "\U0001F3AF **MR. IRRELEVANT**",
            headline,
        ]

        if season_year is not None:
            lines.extend(
                [
                    "",
                    (
                        f"The final selection of the "
                        f"{int(season_year)} NFFL Draft."
                    ),
                ]
            )
    else:
        lines = [headline]

        if pick_kind == "POACH":
            lines.append(
                "\U0001F373 **Another team\u2019s "
                "QO just got cracked.**"
            )

        if next_pick is not None:
            next_team_key = str(
                next_pick["current_owner_team_key"]
            )
            next_team_name = str(
                next_pick["current_owner_team_name"]
            )
            next_pick_id = str(next_pick["pick_id"])

            lines.extend(
                [
                    "",
                    "\u23F1\uFE0F **Up next:** "
                    + manager_display(
                        next_team_key,
                        next_team_name,
                        manager_mentions,
                    ),
                    f"**On the clock at {next_pick_id}.**",
                ]
            )

    return Announcement(
        event_id=(
            f"selection:{draft_key}:{pick_id}:"
            f"{selected_at}"
        ),
        event_type="DRAFT_SELECTION",
        occurred_at=selected_at,
        content="\n".join(lines),
    )


def format_draft_complete_announcement(
    draft_key: str,
    season_year: int,
    final_selection: dict[str, Any],
    total_picks: int,
) -> Announcement:
    selected_at = str(
        final_selection.get("selected_at_utc") or ""
    )

    content = "\n".join(
        [
            (
                f"\U0001F3C6 **THE {int(season_year)} "
                "NFFL DRAFT IS COMPLETE!**"
            ),
            (
                f"All {int(total_picks)} draft-board "
                "slots are settled."
            ),
            (
                "Good luck to every manager this season "
                "\u2014 may your sleepers hit, your stars stay "
                "healthy, and your waiver claims clear."
            ),
        ]
    )

    return Announcement(
        event_id=(
            f"draft_complete:{draft_key}:{selected_at}"
        ),
        event_type="DRAFT_COMPLETE",
        occurred_at=selected_at,
        content=content,
    )


class DeliveryState:
    VERSION = 2

    def __init__(self, path: Path):
        self.path = path
        self.seen_event_ids: set[str] = set()
        self.previewed_event_ids: set[str] = set()
        self.initialized = False

    def load(self) -> None:
        if not self.path.exists():
            return

        payload = json.loads(
            self.path.read_text(encoding="utf-8")
        )

        version = payload.get("version")

        if version not in (1, self.VERSION):
            raise RuntimeError(
                "Unsupported delivery-state version."
            )

        self.initialized = bool(
            payload.get("initialized", False)
        )
        self.seen_event_ids = set(
            payload.get("seen_event_ids", [])
        )

        if version == self.VERSION:
            self.previewed_event_ids = set(
                payload.get(
                    "previewed_event_ids",
                    [],
                )
            )

    def save(self) -> None:
        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        payload = {
            "version": self.VERSION,
            "initialized": self.initialized,
            "seen_event_ids": sorted(
                self.seen_event_ids
            ),
            "previewed_event_ids": sorted(
                self.previewed_event_ids
            ),
        }

        temporary_path = self.path.with_suffix(
            self.path.suffix + ".tmp"
        )

        temporary_path.write_text(
            json.dumps(
                payload,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        temporary_path.replace(self.path)

    def establish_baseline(
        self,
        announcements: Iterable[Announcement],
    ) -> None:
        for announcement in announcements:
            self.seen_event_ids.add(
                announcement.event_id
            )
            self.previewed_event_ids.discard(
                announcement.event_id
            )

        self.initialized = True
        self.save()

    def unseen(
        self,
        announcements: Iterable[Announcement],
    ) -> list[Announcement]:
        return [
            announcement
            for announcement in announcements
            if announcement.event_id
            not in self.seen_event_ids
        ]

    def unpreviewed(
        self,
        announcements: Iterable[Announcement],
    ) -> list[Announcement]:
        return [
            announcement
            for announcement in announcements
            if announcement.event_id
            not in self.seen_event_ids
            and announcement.event_id
            not in self.previewed_event_ids
        ]

    def mark_previewed(
        self,
        announcement: Announcement,
    ) -> None:
        if (
            announcement.event_id
            not in self.seen_event_ids
        ):
            self.previewed_event_ids.add(
                announcement.event_id
            )
            self.save()

    def mark_delivered(
        self,
        announcement: Announcement,
    ) -> None:
        self.seen_event_ids.add(
            announcement.event_id
        )
        self.previewed_event_ids.discard(
            announcement.event_id
        )
        self.save()


def announcement_as_dict(
    announcement: Announcement,
) -> dict[str, str]:
    return asdict(announcement)
