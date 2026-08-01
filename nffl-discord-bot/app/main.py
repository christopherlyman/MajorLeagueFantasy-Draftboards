import argparse
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from event_engine import (
    Announcement,
    DeliveryState,
    announcement_as_dict,
    format_draft_pick_announcement,
    format_draft_start_announcement,
    format_lottery_announcement,
)


@dataclass(frozen=True)
class Settings:
    discord_token: str
    discord_guild_id: str
    discord_channel_id: str
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str
    draft_key: str
    send_enabled: bool
    poll_seconds: int
    manager_map_path: str
    delivery_state_path: str


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()

    if not value:
        raise RuntimeError(
            f"Required environment variable is missing: {name}"
        )

    return value


def load_settings() -> Settings:
    return Settings(
        discord_token=required_env("DISCORD_TOKEN"),
        discord_guild_id=required_env(
            "DISCORD_GUILD_ID"
        ),
        discord_channel_id=required_env(
            "DISCORD_CHANNEL_ID"
        ),
        db_host=required_env("DB_HOST"),
        db_port=int(os.getenv("DB_PORT", "5432")),
        db_name=required_env("DB_NAME"),
        db_user=required_env("DB_USER"),
        db_password=required_env("DB_PASSWORD"),
        draft_key=required_env("DRAFT_KEY"),
        send_enabled=(
            os.getenv(
                "SEND_ENABLED",
                "false",
            ).strip().lower()
            == "true"
        ),
        poll_seconds=max(
            5,
            int(os.getenv("POLL_SECONDS", "15")),
        ),
        manager_map_path=(
            os.getenv(
                "MANAGER_MAP_PATH",
                "/data/manager_map.json",
            ).strip()
            or "/data/manager_map.json"
        ),
        delivery_state_path=(
            os.getenv(
                "DELIVERY_STATE_PATH",
                "/data/delivery_state.json",
            ).strip()
            or "/data/delivery_state.json"
        ),
    )


def connect(settings: Settings) -> psycopg.Connection:
    return psycopg.connect(
        host=settings.db_host,
        port=settings.db_port,
        dbname=settings.db_name,
        user=settings.db_user,
        password=settings.db_password,
        connect_timeout=10,
        row_factory=dict_row,
    )


def run_database_check(settings: Settings) -> None:
    query = """
        SELECT
            current_user AS database_user,
            (
                SELECT COUNT(*)
                FROM nffl.v_draft_board_current
                WHERE draft_key = %s
            ) AS board_rows,
            has_table_privilege(
                current_user,
                'nffl.draft_selection',
                'SELECT'
            ) AS can_select,
            has_table_privilege(
                current_user,
                'nffl.draft_selection',
                'INSERT'
            ) AS can_insert,
            has_table_privilege(
                current_user,
                'nffl.draft_selection',
                'UPDATE'
            ) AS can_update,
            has_table_privilege(
                current_user,
                'nffl.draft_selection',
                'DELETE'
            ) AS can_delete
    """

    with connect(settings) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                query,
                (settings.draft_key,),
            )

            result = cursor.fetchone()

    if result is None:
        raise RuntimeError(
            "Database validation returned no result."
        )

    if result["database_user"] != settings.db_user:
        raise RuntimeError(
            "Unexpected database identity: "
            f"{result['database_user']}"
        )

    if result["board_rows"] != 192:
        raise RuntimeError(
            "Expected 192 DraftBoard rows, "
            f"found {result['board_rows']}."
        )

    if not result["can_select"]:
        raise RuntimeError(
            "The bot cannot read draft selections."
        )

    if (
        result["can_insert"]
        or result["can_update"]
        or result["can_delete"]
    ):
        raise RuntimeError(
            "The bot unexpectedly has draft write access."
        )

    print("DATABASE_CONNECTION=PASS")
    print(
        f"database_user={result['database_user']}"
    )
    print(f"board_rows={result['board_rows']}")
    print("DRAFT_READ_ACCESS=PASS")
    print("DRAFT_WRITE_ACCESS=DENIED")


def fetch_one(
    cursor: psycopg.Cursor,
    query: str,
    parameters: tuple[Any, ...],
) -> dict[str, Any] | None:
    cursor.execute(query, parameters)
    return cursor.fetchone()


def fetch_all(
    cursor: psycopg.Cursor,
    query: str,
    parameters: tuple[Any, ...],
) -> list[dict[str, Any]]:
    cursor.execute(query, parameters)
    return list(cursor.fetchall())


def build_event_snapshot(
    settings: Settings,
) -> dict[str, Any]:
    with connect(settings) as connection:
        with connection.cursor() as cursor:
            draft = fetch_one(
                cursor,
                """
                SELECT
                    draft_key,
                    draft_label,
                    status,
                    updated_at_utc
                FROM nffl.draft
                WHERE draft_key = %s
                """,
                (settings.draft_key,),
            )

            active_lottery = fetch_one(
                cursor,
                """
                SELECT
                    run_id,
                    status,
                    created_at_utc,
                    completed_at_utc,
                    applied_at_utc
                FROM nffl.draft_order_lottery_run
                WHERE draft_key = %s
                  AND status <> 'VOID'
                ORDER BY created_at_utc DESC
                LIMIT 1
                """,
                (settings.draft_key,),
            )

            revealed_lottery_picks = []

            if active_lottery is not None:
                revealed_lottery_picks = fetch_all(
                    cursor,
                    """
                    SELECT
                        pick.run_id,
                        pick.reveal_order,
                        pick.pick_number,
                        pick.team_key,
                        team.team_name,
                        pick.pool_type,
                        pick.revealed_at_utc
                    FROM nffl.draft_order_lottery_pick pick
                    LEFT JOIN nffl.team team
                      ON team.team_key = pick.team_key
                    WHERE pick.run_id = %s
                      AND pick.is_revealed IS TRUE
                    ORDER BY pick.reveal_order
                    """,
                    (active_lottery["run_id"],),
                )

            completed_selections = fetch_all(
                cursor,
                """
                SELECT
                    selection.pick_id,
                    board.round_number,
                    board.round_label,
                    board.slot_number,
                    selection.selecting_team_key,
                    selecting_team.team_name
                        AS selecting_team_name,
                    selection.yahoo_player_key,
                    board.selected_player_name,
                    board.selected_primary_position,
                    selection.pick_kind,
                    selection.selected_at_utc
                FROM nffl.draft_selection selection
                JOIN nffl.v_draft_board_current board
                  ON board.draft_key =
                     selection.draft_key
                 AND board.pick_id =
                     selection.pick_id
                LEFT JOIN nffl.team selecting_team
                  ON selecting_team.team_key =
                     selection.selecting_team_key
                WHERE selection.draft_key = %s
                ORDER BY
                    selection.selected_at_utc,
                    selection.pick_id
                """,
                (settings.draft_key,),
            )

            board_order = fetch_all(
                cursor,
                """
                SELECT
                    pick_id,
                    round_number,
                    round_label,
                    slot_number,
                    pick_type,
                    current_owner_team_key,
                    current_owner_team_name
                FROM nffl.v_draft_board_current
                WHERE draft_key = %s
                ORDER BY
                    round_number,
                    slot_number
                """,
                (settings.draft_key,),
            )

            next_pick = fetch_one(
                cursor,
                """
                SELECT
                    board.pick_id,
                    board.round_number,
                    board.round_label,
                    board.slot_number,
                    board.pick_type,
                    board.current_owner_team_key,
                    board.current_owner_team_name
                FROM nffl.v_draft_board_current board
                WHERE board.draft_key = %s
                  AND (
                        board.placeholder_source = 'QO'
                        OR (
                            board.yahoo_player_key IS NULL
                            AND board.placeholder_source IS NULL
                        )
                  )
                  AND NOT EXISTS (
                        SELECT 1
                        FROM nffl.draft_selection selection
                        WHERE selection.draft_key =
                              board.draft_key
                          AND selection.pick_id =
                              board.pick_id
                  )
                ORDER BY
                    board.round_number,
                    board.slot_number
                LIMIT 1
                """,
                (settings.draft_key,),
            )

    return {
        "draft": draft,
        "active_lottery": active_lottery,
        "revealed_lottery_picks":
            revealed_lottery_picks,
        "completed_draft_selections":
            completed_selections,
        "board_order": board_order,
        "next_pick": next_pick,
    }


def print_json(value: Any) -> None:
    print(
        json.dumps(
            value,
            indent=2,
            default=str,
            ensure_ascii=False,
        )
    )


def require_dry_run(settings: Settings) -> None:
    if settings.send_enabled:
        raise RuntimeError(
            "SEND_ENABLED must remain false "
            "during database-only validation."
        )


def run_check() -> None:
    settings = load_settings()
    require_dry_run(settings)

    print("ENVIRONMENT_CONFIG=PASS")
    print("send_enabled=false")

    run_database_check(settings)

    print("DISCORD_CONTACT=SKIPPED")
    print("NFFL_BOT_SCAFFOLD_CHECK=PASS")


def run_snapshot() -> None:
    settings = load_settings()
    require_dry_run(settings)

    print("ENVIRONMENT_CONFIG=PASS")
    print("send_enabled=false")

    run_database_check(settings)

    snapshot = build_event_snapshot(settings)

    print()
    print("=== ANNOUNCEMENT EVENT SNAPSHOT ===")
    print_json(snapshot)

    print()
    print("VOID_LOTTERY_RUNS=IGNORED")
    print("CONTRACT_PLACEHOLDERS=IGNORED")
    print("DISCORD_CONTACT=SKIPPED")
    print("NFFL_EVENT_SNAPSHOT=PASS")



def load_manager_mentions(
    path: str,
) -> dict[str, str]:
    payload = json.loads(
        Path(path).read_text(encoding="utf-8")
    )

    mapping: dict[str, str] = {}

    def add_mapping(
        team_key: Any,
        discord_user_id: Any,
    ) -> None:
        team = str(team_key or "").strip()
        user_id = str(discord_user_id or "").strip()

        if not team or not user_id:
            return

        if not user_id.isdigit():
            raise RuntimeError(
                "Manager-map Discord IDs must be numeric."
            )

        if not 17 <= len(user_id) <= 20:
            raise RuntimeError(
                "Manager-map Discord IDs must contain "
                "17 through 20 digits."
            )

        mapping[team] = user_id

    candidates: Any = payload

    if isinstance(payload, dict):
        candidates = (
            payload.get("teams")
            or payload.get("manager_map")
            or payload
        )

    if isinstance(candidates, dict):
        for team_key, value in candidates.items():
            if isinstance(value, dict):
                add_mapping(
                    value.get("team_key") or team_key,
                    value.get("discord_user_id")
                    or value.get("user_id"),
                )
            else:
                add_mapping(team_key, value)

    elif isinstance(candidates, list):
        for value in candidates:
            if not isinstance(value, dict):
                continue

            add_mapping(
                value.get("team_key"),
                value.get("discord_user_id")
                or value.get("user_id"),
            )

    else:
        raise RuntimeError(
            "Unsupported manager-map JSON structure."
        )

    if len(mapping) != 12:
        raise RuntimeError(
            "Expected 12 manager Discord mappings, "
            f"found {len(mapping)}."
        )

    return mapping


def build_announcements(
    settings: Settings,
    snapshot: dict[str, Any],
    manager_mentions: dict[str, str],
) -> list[Announcement]:
    announcements: list[Announcement] = []

    for lottery_pick in (
        snapshot.get("revealed_lottery_picks")
        or []
    ):
        announcements.append(
            format_lottery_announcement(
                lottery_pick
            )
        )

    draft = snapshot.get("draft") or {}
    draft_status = str(
        draft.get("status") or ""
    ).strip().lower()

    if draft_status == "active":
        announcements.append(
            format_draft_start_announcement(draft)
        )

        board_order = list(
            snapshot.get("board_order") or []
        )

        board_index = {
            str(row["pick_id"]): index
            for index, row in enumerate(board_order)
        }

        completed_selections = list(
            snapshot.get(
                "completed_draft_selections"
            )
            or []
        )

        for selection in completed_selections:
            pick_id = str(selection["pick_id"])

            if pick_id not in board_index:
                raise RuntimeError(
                    "Completed selection is missing from "
                    f"board order: {pick_id}"
                )

        completed_selections.sort(
            key=lambda selection: board_index[
                str(selection["pick_id"])
            ]
        )

        for selection_index, selection in enumerate(
            completed_selections
        ):
            if (
                selection_index + 1
                < len(completed_selections)
            ):
                next_completed = completed_selections[
                    selection_index + 1
                ]

                next_pick = board_order[
                    board_index[
                        str(next_completed["pick_id"])
                    ]
                ]
            else:
                next_pick = snapshot.get("next_pick")

            announcements.append(
                format_draft_pick_announcement(
                    settings.draft_key,
                    selection,
                    next_pick,
                    manager_mentions,
                )
            )

    priority = {
        "DRAFT_START": 0,
        "LOTTERY_REVEAL": 1,
        "DRAFT_SELECTION": 2,
    }

    announcements.sort(
        key=lambda item: (
            priority.get(item.event_type, 99),
            item.occurred_at or "",
            item.event_id,
        )
    )

    return announcements


def send_discord_announcement(
    settings: Settings,
    announcement: Announcement,
) -> str:
    if not settings.send_enabled:
        raise RuntimeError(
            "Discord sending is disabled."
        )

    payload = json.dumps(
        {
            "content": announcement.content,
            "allowed_mentions": {
                "parse": ["users"],
                "replied_user": False,
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")

    request = urllib.request.Request(
        (
            "https://discord.com/api/v10/channels/"
            f"{settings.discord_channel_id}/messages"
        ),
        data=payload,
        headers={
            "Authorization": (
                f"Bot {settings.discord_token}"
            ),
            "Content-Type": "application/json",
            "User-Agent": "NFFL-Discord-Bot/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=20,
        ) as response:
            response_payload = json.loads(
                response.read().decode("utf-8")
            )

    except urllib.error.HTTPError as error:
        error_body = error.read().decode(
            "utf-8",
            errors="replace",
        )

        raise RuntimeError(
            "Discord message request failed: "
            f"HTTP {error.code}: "
            f"{error_body[:500]}"
        ) from error

    message_id = str(
        response_payload.get("id") or ""
    )
    channel_id = str(
        response_payload.get("channel_id") or ""
    )

    if not message_id:
        raise RuntimeError(
            "Discord did not return a message ID."
        )

    if channel_id != settings.discord_channel_id:
        raise RuntimeError(
            "Discord returned an unexpected channel ID."
        )

    print(
        "DISCORD_MESSAGE_SENT "
        f"event_id={announcement.event_id} "
        f"message_id={message_id}"
    )

    return message_id


def run_runtime() -> None:
    settings = load_settings()

    run_database_check(settings)

    manager_mentions = load_manager_mentions(
        settings.manager_map_path
    )

    delivery_state = DeliveryState(
        Path(settings.delivery_state_path)
    )
    delivery_state.load()

    print("NFFL_BOT_RUNTIME_STARTED")
    print(
        "send_enabled="
        f"{str(settings.send_enabled).lower()}"
    )
    print(f"poll_seconds={settings.poll_seconds}")
    print(
        "manager_map_count="
        f"{len(manager_mentions)}"
    )
    print(
        "delivery_state_path="
        f"{settings.delivery_state_path}"
    )

    if settings.send_enabled:
        print("DISCORD_SEND_PATH=ENABLED")
    else:
        print("DISCORD_SEND_PATH=DISABLED")

    poll_number = 0
    heartbeat_interval = max(
        1,
        60 // settings.poll_seconds,
    )

    while True:
        poll_number += 1
        poll_started = time.monotonic()

        try:
            snapshot = build_event_snapshot(settings)

            announcements = build_announcements(
                settings,
                snapshot,
                manager_mentions,
            )

            if not delivery_state.initialized:
                delivery_state.establish_baseline(
                    announcements
                )

                print(
                    "BASELINE_ESTABLISHED "
                    f"event_count={len(announcements)}"
                )

            else:
                pending = delivery_state.unseen(
                    announcements
                )
                processed_count = 0

                if settings.send_enabled:
                    for announcement in pending:
                        send_discord_announcement(
                            settings,
                            announcement,
                        )

                        delivery_state.mark_delivered(
                            announcement
                        )
                        processed_count += 1

                else:
                    previews = (
                        delivery_state.unpreviewed(
                            announcements
                        )
                    )

                    for announcement in previews:
                        print(
                            "PROPOSED_ANNOUNCEMENT_BEGIN"
                        )
                        print_json(
                            announcement_as_dict(
                                announcement
                            )
                        )
                        print(
                            "PROPOSED_ANNOUNCEMENT_END"
                        )

                        delivery_state.mark_previewed(
                            announcement
                        )
                        processed_count += 1

                remaining_pending = len(
                    delivery_state.unseen(
                        announcements
                    )
                )

                if (
                    processed_count
                    or poll_number == 1
                    or poll_number
                    % heartbeat_interval
                    == 0
                ):
                    draft = snapshot.get("draft") or {}

                    print(
                        "POLL_OK "
                        f"poll={poll_number} "
                        "draft_status="
                        f"{draft.get('status')} "
                        "known_events="
                        f"{len(announcements)} "
                        "processed_events="
                        f"{processed_count} "
                        "pending_events="
                        f"{remaining_pending}"
                    )

        except KeyboardInterrupt:
            raise

        except Exception as error:
            print(
                "POLL_ERROR "
                f"type={type(error).__name__} "
                f"message={error}"
            )

        elapsed = time.monotonic() - poll_started

        time.sleep(
            max(
                0.0,
                settings.poll_seconds - elapsed,
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="NFFL Discord Bot"
    )

    mode = parser.add_mutually_exclusive_group(
        required=True
    )

    mode.add_argument(
        "--check",
        action="store_true",
    )

    mode.add_argument(
        "--snapshot",
        action="store_true",
    )

    mode.add_argument(
        "--run",
        action="store_true",
    )

    arguments = parser.parse_args()

    if arguments.check:
        run_check()
    elif arguments.snapshot:
        run_snapshot()
    elif arguments.run:
        run_runtime()


if __name__ == "__main__":
    main()
