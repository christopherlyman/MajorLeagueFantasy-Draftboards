# MajorLeagueFantasy-Draftboards

Multi-league fantasy sports DraftBoard portfolio supporting baseball, football, and hockey leagues.

## Repository Scope

This repository currently contains:

- `shared/` — shared DraftBoard code, scripts, and canonical architecture documentation.
- `mlf/` — MLF fantasy baseball league-specific code, runtime files, SQL, and operational documentation.
- `milf/` — MiLF fantasy baseball league-specific configuration, runtime files, and operational documentation.
- `nffl/` — NFFL fantasy football DraftBoard, including contract/keeper and draft-management functionality.
- `nfhl/` — NFHL fantasy hockey redraft DraftBoard, including roll call, draft-order lottery, manager access, Auto-Pick foundations, live draft workflow, and slow-draft clock management.

## Design Approach

The portfolio uses a multi-league architecture in which league applications remain operationally independent while proven generic DraftBoard patterns can be reused selectively.

League-specific rules stay within their appropriate league implementation. Shared components are used where behavior is genuinely common across leagues.

## Current Focus

Current development includes:

- multi-league DraftBoard architecture
- deterministic, proof-first operational workflows
- season and offseason preparation
- draft-order and lottery workflows
- manager access and commissioner controls
- live and slow-draft operations
- league-specific keeper, contract, redraft, and roster rules
- Yahoo Fantasy Sports ingestion and reconciliation

## NFHL

NFHL is the fantasy hockey implementation.

The 2026 DraftBoard is a pure redraft workflow with:

- 14 Yahoo teams
- snake draft ordering
- 18 draft rounds
- commissioner-controlled draft lifecycle
- 24-hour slow-draft pick windows
- manager gateway links
- Auto-Pick support
- late-pick handling without rewinding the active clock
- Yahoo-backed team and player ingestion

NFHL remains independent from NFFL production. Generic mechanics may be adapted between projects, but league-specific football contract and keeper concepts do not belong in NFHL.

## Important Notes

- This repository is public for visibility and reference.
- No open-source license is provided at this time.
- All rights are reserved unless and until a license is added.
- Operational secrets, private league data, generated runtime state, backups, and local environment files are intentionally excluded from version control.

## Documentation

Recommended starting points include:

- `shared/docs/0_CoreCanonicalGuide.md`
- `shared/docs/8_Multi-League_Target_Architecture.md`
- `mlf/docs/10_MLF_Next_Season_Prep.md`
- `milf/docs/10_MiLF_Next_Season_Prep.md`
- `nffl/docs/`
- `nfhl/docs/`

## Status

This project is under active development. Internal workflows, scripts, SQL objects, and runbooks may change as the multi-league model continues to evolve.
