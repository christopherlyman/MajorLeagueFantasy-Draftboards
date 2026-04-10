# MiLF Next-Season Prep

## Purpose
League-specific operating checklist for preparing MiLF for the next draft season.

## Canonical Boundaries
- Shared canonicals own shared truth.
- This runbook owns only MiLF-specific seasonal operating procedure.
- Use DB truth, then runtime truth, then application state, then UI.

## MiLF Prep Checklist

### 1) Team / Franchise Rollover
- Verify new season team/franchise rollover mapping.

### 2) Active League Profile
- Verify active league profile for the new season.
- Verify draft-order and league settings sanity.

### 3) Player Universe Refresh
- Refresh current Yahoo player-universe for the new season.

### 4) Commissioner / Predraft Checks
- Verify commissioner access and basic commissioner tool checks.
- Verify basic pre-draft board sanity checks.

### 5) Explicit Non-Checks
- Do not include contract carry-forward.
- Do not include prospect-tag carry-forward.
- Do not include qualifying-offer carry-forward.
- Do not include MLF-style predraft placeholder reconstruction.

## Verification Notes

### 1) Team / Franchise Rollover — VERIFIED for 2026
Proof command run from NAS shell:

```bash
docker exec -i mlf_postgres psql -U mlf -d mlf -P pager=off <<'SQL'
select league_key, season_year, count(*) as franchise_team_rows
from public.franchise_season_team
where season_year = 2026
  and league_key in ('469.l.41640','469.l.60688')
group by league_key, season_year
order by league_key;

select league_key, franchise_id, team_key, team_name
from public.franchise_season_team
where season_year = 2026
  and league_key in ('469.l.41640','469.l.60688')
order by league_key, franchise_id;
SQL
```

Verified MiLF result:
- `league_key = 469.l.60688`
- `season_year = 2026`
- `franchise_team_rows = 16`

Verified interpretation:
- 2026 MiLF rollover mapping exists in `public.franchise_season_team`.
- All 16 MiLF franchises have current-season `team_key` / `team_name` rows.
- This satisfies the DB-truth check for Step 1 before any runtime or UI validation.

### 2) Active League Profile — VERIFIED for 2026
Authoritative source: `public.league_profile`

Proof summary:
- Active DB row exists for `469.l.60688` with `is_active = true` and `profile_version = 1`.
- Draft mode = `offline`.
- Draft order mode = `snake`.
- Rounds total = `25`.
- Pick trades allowed = `true`.
- Qualifying offers = `false`.
- Commissioner tools = `true`.
- Keeper / contract / prospect-tag features are disabled.

Verified interpretation:
- MiLF Step 2 passes the DB-truth profile check for 2026.
- MiLF should remain a redraft workflow and must not inherit MLF control-rights prep steps.

## Open Questions
- None yet.