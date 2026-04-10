create or replace view public.v_contracts_with_team_key as
select
  r.snapshot_id,
  r.season_year,
  m.league_key,
  r.player_name,
  r.owner_name as sheet_owner_name,
  m.team_key,
  m.team_name as yahoo_team_name,
  p.years_remaining,
  p.contract_label,
  p.event_flags,
  r.raw_value
from public.contracts_cell_raw r
join public.contracts_cell_parsed p
  on p.snapshot_id = r.snapshot_id
 and p.owner_name = r.owner_name
 and p.sheet_name = r.sheet_name
 and p.player_name = r.player_name
 and p.season_year = r.season_year
join public.yahoo_team_map m
  on m.season_year = r.season_year
 and m.owner_name = r.owner_name
where r.raw_value is not null
  and btrim(r.raw_value) <> ''
  and r.player_name <> all (array['total contracts','years of control']);

create or replace view public.v_contract_discrepancies as
with sheet as (
    select
      snapshot_id,
      season_year,
      league_key,
      player_name,
      sheet_owner_name,
      team_key as sheet_team_key,
      yahoo_team_name as sheet_team_name,
      years_remaining as sheet_years_remaining,
      contract_label,
      event_flags,
      raw_value
    from public.v_contracts_with_team_key
),
rec as (
    select
      snapshot_id,
      league_key,
      season_year,
      player_name,
      starting_owner_name,
      starting_team_key,
      ending_owner_name,
      ending_team_key,
      contract_voided,
      void_reason,
      voided_ts_epoch,
      last_event_ts_epoch,
      last_event_source_type,
      last_event_action_type,
      last_event_txn_key,
      needs_review,
      review_reason,
      years_remaining_season,
      years_remaining_next,
      has_contract_next
    from public.contract_reconcile
),
joined as (
    select
      s.snapshot_id,
      s.season_year,
      s.league_key,
      s.player_name,
      s.sheet_owner_name,
      s.sheet_team_key,
      s.sheet_team_name,
      s.sheet_years_remaining,
      s.contract_label,
      s.event_flags,
      s.raw_value,
      r.starting_owner_name,
      r.starting_team_key,
      r.ending_owner_name,
      r.ending_team_key,
      r.contract_voided,
      r.void_reason,
      to_timestamp(r.voided_ts_epoch::double precision) as voided_ts,
      to_timestamp(r.last_event_ts_epoch::double precision) as last_event_ts,
      r.last_event_source_type,
      r.last_event_action_type,
      r.last_event_txn_key,
      r.needs_review,
      r.review_reason,
      r.years_remaining_season,
      r.years_remaining_next,
      r.has_contract_next,
      case
        when r.contract_voided = true then 'CONTRACT_SHOULD_BE_VOIDED'
        when r.contract_voided = false
         and r.ending_team_key is not null
         and r.starting_team_key is not null
         and r.ending_team_key <> r.starting_team_key
         and r.last_event_source_type = 'waivers'
         and r.last_event_action_type = 'add'
          then 'WAIVER_TRANSFER_MISSING_IN_SHEET'
        when r.needs_review = true then 'NEEDS_REVIEW'
        else null
      end as discrepancy_type,
      case
        when r.contract_voided = true
          then 'Remove/void contract carryover for next season (cleared waivers -> FA).'
        when r.contract_voided = false
         and r.ending_team_key <> r.starting_team_key
         and r.last_event_source_type = 'waivers'
         and r.last_event_action_type = 'add'
          then 'Update contract owner to ending_owner_name (waiver claim retains contract).'
        when r.needs_review = true
          then 'Manual review (trade mismatch, unmapped team, or other rule edge).'
        else null
      end as recommended_action
    from sheet s
    left join rec r
      on r.snapshot_id = s.snapshot_id
     and r.league_key = s.league_key
     and r.season_year = s.season_year
     and r.player_name = s.player_name
)
select
  snapshot_id,
  season_year,
  league_key,
  player_name,
  sheet_owner_name,
  sheet_team_key,
  sheet_team_name,
  sheet_years_remaining,
  contract_label,
  event_flags,
  raw_value,
  starting_owner_name,
  starting_team_key,
  ending_owner_name,
  ending_team_key,
  contract_voided,
  void_reason,
  voided_ts,
  last_event_ts,
  last_event_source_type,
  last_event_action_type,
  last_event_txn_key,
  needs_review,
  review_reason,
  years_remaining_season,
  years_remaining_next,
  has_contract_next,
  discrepancy_type,
  recommended_action
from joined
where discrepancy_type is not null;