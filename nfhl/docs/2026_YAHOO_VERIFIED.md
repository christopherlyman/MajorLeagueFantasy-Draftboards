# NFHL 2026 — Verified Yahoo Configuration

Status: VERIFIED from Yahoo Fantasy API on 2026-08-16.

## Identity

- League: NFHL — National Fantasy Hockey League
- Sport: NHL / Fantasy Hockey
- Season: 2026
- Yahoo League ID: 10961
- Yahoo League Key: 477.l.10961
- Persistent URL: https://hockey.fantasysports.yahoo.com/league/nfhl2025
- Target team count: 14
- League type: Private
- Scoring: Head-to-Head Points
- Current state: Predraft

## Roll Call

Current Yahoo team membership is provisional during roll call.

Application development MUST NOT require all 14 teams to exist.

The final production draft order / lottery / draft-start workflow MUST
refuse to initialize unless the expected 14-team field is complete.

## Features

This is a pure redraft league.

Disabled / nonexistent:

- Keepers
- Contracts
- Qualifying Offers (QO)
- Franchise Tags (FT)
- Prospect Tags
- Poaching
- Contract history/control-rights workflows

These features must not merely be hidden in NFHL. They must not be
dependencies of the NFHL draft workflow.

## Roster

Starting:

- C: 2
- LW: 2
- RW: 2
- F: 1
- D: 4
- Util: 1
- G: 2

Bench:

- BN: 4

Inactive/reserve:

- IR+: 2
- NA: 1

Normal active + bench roster size: 18.

Working DraftBoard assumption:
- 18 draft rounds
- IR+ and NA are not counted as normal draft rounds

This remains a design assumption until separately proven against the
actual intended slow-draft workflow.

## Scoring

Skaters:

- Goals: 4
- Assists: 2.5
- Penalty Minutes: 0.2
- Powerplay Points: 1
- Shorthanded Points: 1.25
- Shots on Goal: 0.25
- Hits: 0.5
- Blocks: 0.5

Goalies:

- Wins: 3
- Goals Against: -1
- Saves: 0.25
- Shutouts: 2.5

## Other Yahoo Settings

- Yahoo draft type: self
- Yahoo live pick time: 60 seconds
- Draft-pick trading: enabled
- Weekly adds: 7
- Waiver type: rolling
- FAAB: disabled
- Playoff teams: 6
- Public viewing: enabled

## Data Rules

- Yahoo player key is authoritative player identity.
- League-scoped data must use league_key + season_year.
- Current Yahoo team membership is provisional until roll call is complete.
