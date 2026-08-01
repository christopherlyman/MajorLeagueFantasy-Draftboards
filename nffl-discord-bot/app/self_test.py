from pathlib import Path
from tempfile import TemporaryDirectory

from event_engine import (
    DeliveryState,
    format_draft_pick_announcement,
    format_lottery_announcement,
    ordinal,
)


def assert_equal(
    actual,
    expected,
    description: str,
) -> None:
    if actual != expected:
        raise AssertionError(
            f"{description}: expected {expected!r}, "
            f"found {actual!r}"
        )


assert_equal(ordinal(1), "1st", "ordinal 1")
assert_equal(ordinal(2), "2nd", "ordinal 2")
assert_equal(ordinal(3), "3rd", "ordinal 3")
assert_equal(ordinal(4), "4th", "ordinal 4")
assert_equal(ordinal(11), "11th", "ordinal 11")
assert_equal(ordinal(12), "12th", "ordinal 12")
assert_equal(ordinal(13), "13th", "ordinal 13")
assert_equal(ordinal(21), "21st", "ordinal 21")

historical_lottery = format_lottery_announcement(
    {
        "run_id": "historical-run",
        "reveal_order": 1,
        "pick_number": 12,
        "team_key": "team-12",
        "team_name": "Historical Team",
        "pool_type": "CHAMPION",
        "revealed_at_utc": "2026-07-01T00:00:00+00:00",
    }
)

new_lottery = format_lottery_announcement(
    {
        "run_id": "live-run",
        "reveal_order": 1,
        "pick_number": 12,
        "team_key": "team-live",
        "team_name": "Party On Wayne",
        "pool_type": "CHAMPION",
        "revealed_at_utc": "2026-07-25T01:05:00+00:00",
    }
)

new_selection = format_draft_pick_announcement(
    draft_key="nffl_2026_preseason",
    selection={
        "pick_id": "QO1-01",
        "selecting_team_key": "team-one",
        "selecting_team_name": "Buccaneer Blitzkrieg",
        "yahoo_player_key": "470.p.12345",
        "selected_player_name": "Example Quarterback",
        "selected_primary_position": "QB",
        "selected_at_utc": "2026-07-26T01:00:00+00:00",
    },
    next_pick={
        "pick_id": "QO1-02",
        "current_owner_team_key": "team-two",
        "current_owner_team_name": "Skol",
    },
    manager_mentions={
        "team-two": "123456789012345678",
    },
)

if "Party On Wayne" not in new_lottery.content:
    raise AssertionError(
        "Lottery message is missing the team name."
    )

if "12th selection" not in new_lottery.content:
    raise AssertionError(
        "Lottery message is missing the ordinal pick."
    )

if "Example Quarterback (QB)" not in (
    new_selection.content
):
    raise AssertionError(
        "Draft message is missing player details."
    )

if "<@123456789012345678>" not in (
    new_selection.content
):
    raise AssertionError(
        "Draft message is missing the manager mention."
    )

if "QO1-02" not in new_selection.content:
    raise AssertionError(
        "Draft message is missing the next pick."
    )

with TemporaryDirectory() as temporary_directory:
    state_path = (
        Path(temporary_directory) / "state.json"
    )

    state = DeliveryState(state_path)
    state.load()

    if state.initialized:
        raise AssertionError(
            "New state should not be initialized."
        )

    state.establish_baseline(
        [historical_lottery]
    )

    if state.unseen([historical_lottery]):
        raise AssertionError(
            "Baseline event was incorrectly unseen."
        )

    unseen = state.unseen(
        [
            historical_lottery,
            new_lottery,
            new_selection,
        ]
    )

    assert_equal(
        len(unseen),
        2,
        "new announcement count",
    )

    for announcement in unseen:
        state.mark_delivered(announcement)

    reloaded = DeliveryState(state_path)
    reloaded.load()

    if reloaded.unseen(
        [
            historical_lottery,
            new_lottery,
            new_selection,
        ]
    ):
        raise AssertionError(
            "Delivered events were rediscovered."
        )

print("ORDINAL_FORMATTING=PASS")
print("LOTTERY_MESSAGE_FORMAT=PASS")
print("DRAFT_MESSAGE_FORMAT=PASS")
print("MANAGER_MENTION_FORMAT=PASS")
print("FIRST_START_BASELINE=PASS")
print("DUPLICATE_SUPPRESSION=PASS")
print()
print("=== SIMULATED LOTTERY ANNOUNCEMENT ===")
print(new_lottery.content)
print()
print("=== SIMULATED DRAFT ANNOUNCEMENT ===")
print(new_selection.content)
print()
print("DISCORD_CONTACT=SKIPPED")
print("EVENT_ENGINE_SELF_TEST=PASS")
