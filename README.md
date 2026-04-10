# MajorLeagueFantasy-Draftboards

Multi-league fantasy baseball DraftBoard portfolio with:

- shared DraftBoard code
- MLF league-specific overlays and runbooks
- MiLF league-specific overlays and runbooks
- supporting scripts for data loading, reconciliation, and offseason prep

## Repository Scope

This repository currently contains:

- shared/ — shared application code, shared scripts, and shared canonical docs
- mlf/ — MLF-specific code, runtime files, SQL, and league-specific docs
- milf/ — MiLF-specific config, runtime files, and league-specific docs

## Current Focus

This repository is currently focused on:

- multi-league DraftBoard structure
- season-prep runbooks
- MLF contract reconciliation and discrepancy reporting
- deterministic, proof-first operational workflows

## Important Notes

- This repository is public for visibility and reference.
- No open-source license is provided at this time.
- All rights are reserved unless and until a license is added.
- Operational secrets, private league data, and local environment files are intentionally excluded from version control.

## Documentation

Recommended starting points:

- shared/docs/0_CoreCanonicalGuide.md
- shared/docs/8_Multi-League_Target_Architecture.md
- mlf/docs/10_MLF_Next_Season_Prep.md
- milf/docs/10_MiLF_Next_Season_Prep.md

## Status

This project is under active development. Internal workflows, scripts, SQL objects, and runbooks may change as the multi-league model is refined.