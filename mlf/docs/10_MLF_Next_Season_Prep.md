# MLF Next-Season Prep

## Purpose
League-specific operating checklist for preparing MLF for the next draft season.

## Canonical Boundaries
- Shared canonicals own shared truth.
- This runbook owns only MLF-specific seasonal operating procedure.
- Use DB truth, then runtime truth, then application state, then UI.

## MLF Prep Checklist

### 1) Team / Franchise Rollover
- Verify new season team/franchise rollover mapping.

### 2) Active League Profile
- Verify active league profile for the new season.
- Verify draft mode, order mode, rounds, and enabled features.

### 3) Player-Control Carry-Forward
- Review contract carry-forward / update state.
- Review prospect-tag carry-forward / update state.
- Review qualifying-offer carry-forward / update state.

### 3.1) Contracted-Player Waiver Disposition Audit
- Scan season transactions for contracted players who were dropped.
- Determine whether each dropped contracted player was claimed before clearing waivers or cleared waivers unclaimed.
- If claimed before clearing waivers and retained through season end, transfer the contract with the player.
- If cleared waivers unclaimed, void the contract.
- Generate a repeatable offseason contract reconciliation report from this audit.

### 4) Predraft / Draft Sanity
- Verify predraft placeholder reconstruction sanity.
- Verify draft-order and predraft rounds sanity under active MLF profile.
- Verify traded-pick behavior relevant to predraft display.

### 5) Player Universe Refresh
- Refresh current Yahoo player-universe for the new season.

### 6) Commissioner / Runtime Checks
- Verify commissioner tool checks before draft use.
- Verify runtime/deployment assumptions only from proven truth.

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

Verified MLF result:
- `league_key = 469.l.41640`
- `season_year = 2026`
- `franchise_team_rows = 16`

Verified interpretation:
- 2026 MLF rollover mapping exists in `public.franchise_season_team`.
- All 16 MLF franchises have current-season `team_key` / `team_name` rows.
- This satisfies the DB-truth check for Step 1 before any runtime or UI validation.

### 2) Active League Profile — VERIFIED for 2026
Authoritative source: `public.league_profile`

Proof summary:
- Active DB row exists for `469.l.41640` with `is_active = true` and `profile_version = 1`.
- Draft mode = `offline`.
- Draft order mode = `straight`.
- Rounds total = `25`.
- Pick trades allowed = `true`.
- Qualifying offers = `true`.
- Commissioner tools = `true`.
- Keeper / contract / prospect-tag features are enabled.

Verified interpretation:
- MLF Step 2 passes the DB-truth profile check for 2026.
- MLF later runbook steps must include contract / PT / QO validation because those features are enabled.

### 3) Player-Control Carry-Forward — VERIFIED for 2026
Authoritative sources: `public.contract`, `public.v_contracts_effective_current`, `public.prospect_tag`, `public.qualifying_offer`

Proof summary:
- Raw contract rows for MLF 2026 = `142`.
- Effective current contract rows for MLF 2026 = `135`.
- Prospect tag rows for MLF 2026 = `9`.
- Qualifying offer rows for MLF 2026 = `80`.

Verified interpretation:
- MLF contract carry-forward data exists and is populated for 2026.
- MLF effective-current contract view is populated for 2026.
- MLF prospect-tag carry-forward data exists and is populated for 2026.
- MLF qualifying-offer carry-forward data exists and is populated for 2026.
- Step 3 passes the DB-truth control-rights check for 2026.

### 3.1) Contracted-Player Waiver Disposition Audit — VERIFIED for 2025 historical season key
Authoritative sources: `public.yahoo_transaction_event`, `public.yahoo_team_map`, `public.contract_reconcile`

Verified execution context:
- Historical MLF 2025 Yahoo league key = `458.l.11506`.
- Do not run this audit against the next-season MLF key.

Proof summary:
- Reconciliation run completed successfully for `league_key = 458.l.11506`, `season_year = 2025`, `snapshot_id = 2026-02-02__post-renewal`.
- `contract_reconcile` rows written = `181`.
- `contract_voided = true` rows = `54`.
- `needs_review = true` rows = `1`.

Verified interpretation:
- The waiver-disposition audit is now proven as a repeatable offseason reconciliation step.
- Contracted players who cleared waivers or were later added from free agency are being identified for contract voiding.
- Waiver/trade edge cases are surfaced into `contract_reconcile` for manual review.
- This audit must run on the historical season league key before next-season contract carry-forward is finalized.

Current known manual review example:
- `Cole Ragans` flagged with review reason `trade_in_yahoo_but_sheet_missing_from/to`.

### 3.2) Contract Discrepancy Report Export — VERIFIED for 2025 historical season key
Authoritative source: `public.v_contract_discrepancies`

Verified execution context:
- Historical MLF 2025 Yahoo league key = `458.l.11506`.
- Snapshot used = `2026-02-02__post-renewal`.

Proof summary:
- Generic report script `mlf/scripts/report_contract_discrepancies.py` executed successfully.
- Output written for `league_key = 458.l.11506`, `season_year = 2025`.
- Export row count = `43`.
- This matches the previously proven generic discrepancy-view row count for the same historical context.

Verified interpretation:
- The discrepancy export step is now season-agnostic and repeatable.
- MLF offseason contract reconciliation no longer depends on the legacy 2025-only report script.
- Step 3.2 passes for the tested 2025 historical MLF context.

## Open Questions
- None yet.