
CREATE OR REPLACE VIEW nffl.v_draft_board_current AS
WITH standard_slot_targets AS (
    SELECT
        p.draft_key,
        p.pick_id,
        p.current_owner_team_key AS team_key,
        row_number() OVER (
            PARTITION BY p.draft_key, p.current_owner_team_key
            ORDER BY p.round_number DESC, p.slot_number
        ) AS keeper_slot_ordinal
    FROM nffl.draft_pick p
    WHERE p.draft_key = 'nffl_2026_preseason'
      AND p.pick_type = 'STANDARD'
),
keeper_sources AS (
    SELECT
        c.league_key,
        c.season_year,
        c.team_key,
        c.yahoo_player_key,
        pu.full_name,
        nullif(split_part(regexp_replace(pu.eligible_positions::text, '[{}\[\]\" ]', '', 'g'), ',', 1), '') AS primary_position,
        c.contract_years_remaining,
        'CONTRACT'::text AS placeholder_source,
        20 AS source_sort
    FROM nffl.contract c
    JOIN nffl.player_universe pu
      ON pu.league_key = c.league_key
     AND pu.season_year = c.season_year
     AND pu.yahoo_player_key = c.yahoo_player_key
    WHERE c.league_key = '470.l.84346'
      AND c.season_year = 2026
      AND c.status = 'active'

    UNION ALL

    SELECT
        d.league_key,
        d.season_year,
        d.team_key,
        d.yahoo_player_key,
        pu.full_name,
        nullif(split_part(regexp_replace(pu.eligible_positions::text, '[{}\[\]\" ]', '', 'g'), ',', 1), '') AS primary_position,
        NULL::integer AS contract_years_remaining,
        'FT'::text AS placeholder_source,
        10 AS source_sort
    FROM nffl.offseason_keeper_decision d
    JOIN nffl.player_universe pu
      ON pu.league_key = d.league_key
     AND pu.season_year = d.season_year
     AND pu.yahoo_player_key = d.yahoo_player_key
    JOIN nffl.league_visibility_state v
      ON v.league_key = d.league_key
     AND v.season_year = d.season_year
     AND v.qoft_revealed = true
    WHERE d.league_key = '470.l.84346'
      AND d.season_year = 2026
      AND d.decision_type = 'FT'
),
keeper_ranked AS (
    SELECT
        ks.*,
        row_number() OVER (
            PARTITION BY ks.league_key, ks.season_year, ks.team_key
            ORDER BY
                ks.source_sort,
                ks.contract_years_remaining DESC NULLS LAST,
                ks.full_name
        ) AS keeper_slot_ordinal
    FROM keeper_sources ks
),
keeper_overlay AS (
    SELECT
        st.draft_key,
        st.pick_id,
        kr.team_key,
        kr.yahoo_player_key,
        kr.full_name,
        kr.primary_position,
        kr.contract_years_remaining,
        kr.placeholder_source
    FROM standard_slot_targets st
    JOIN keeper_ranked kr
      ON kr.team_key = st.team_key
     AND kr.keeper_slot_ordinal = st.keeper_slot_ordinal
)
SELECT
    p.draft_key,
    p.pick_id,
    p.round_number,
    p.slot_number,
    p.round_label,
    p.pick_type,
    p.column_team_key,
    col_team.team_name AS column_team_name,
    p.current_owner_team_key,
    owner_team.team_name AS current_owner_team_name,
    p.traded_flag,
    p.ownership_note,

    COALESCE(
        s.yahoo_player_key,
        qo.yahoo_player_key,
        ko.yahoo_player_key
    ) AS yahoo_player_key,

    COALESCE(
        selected_player.full_name,
        qo_player.full_name,
        ko.full_name
    ) AS selected_player_name,

    COALESCE(
        s.pick_kind,
        CASE
            WHEN qo.yahoo_player_key IS NOT NULL THEN 'QO_PLACEHOLDER'
            WHEN ko.placeholder_source = 'FT' THEN 'FT_PLACEHOLDER'
            WHEN ko.placeholder_source = 'CONTRACT' THEN 'CONTRACT_PLACEHOLDER'
            ELSE NULL
        END
    ) AS pick_kind,

    s.selected_at_utc,

    COALESCE(
        nullif(split_part(regexp_replace(selected_player.eligible_positions::text, '[{}\[\]\" ]', '', 'g'), ',', 1), ''),
        nullif(split_part(regexp_replace(qo_player.eligible_positions::text, '[{}\[\]\" ]', '', 'g'), ',', 1), ''),
        ko.primary_position
    ) AS selected_primary_position,

    CASE
        WHEN qo.yahoo_player_key IS NOT NULL THEN 'QO'
        WHEN ko.placeholder_source IS NOT NULL THEN ko.placeholder_source
        WHEN s.yahoo_player_key IS NOT NULL THEN 'DRAFT_SELECTION'
        ELSE NULL
    END AS placeholder_source,

    ko.contract_years_remaining

FROM nffl.draft_pick p

LEFT JOIN nffl.team col_team
  ON col_team.league_key = '470.l.84346'
 AND col_team.season_year = 2026
 AND col_team.team_key = p.column_team_key

LEFT JOIN nffl.team owner_team
  ON owner_team.league_key = '470.l.84346'
 AND owner_team.season_year = 2026
 AND owner_team.team_key = p.current_owner_team_key

LEFT JOIN nffl.draft_selection s
  ON s.draft_key = p.draft_key
 AND s.pick_id = p.pick_id

LEFT JOIN nffl.player_universe selected_player
  ON selected_player.league_key = '470.l.84346'
 AND selected_player.season_year = 2026
 AND selected_player.yahoo_player_key = s.yahoo_player_key

LEFT JOIN public.qualifying_offer qo
  ON qo.league_key = '470.l.84346'
 AND qo.season_year = 2026
 AND qo.team_key = p.current_owner_team_key
 AND qo.qo_level = p.round_number
 AND p.pick_type = 'QO'
 AND s.yahoo_player_key IS NULL

LEFT JOIN nffl.player_universe qo_player
  ON qo_player.league_key = qo.league_key
 AND qo_player.season_year = qo.season_year
 AND qo_player.yahoo_player_key = qo.yahoo_player_key

LEFT JOIN keeper_overlay ko
  ON ko.draft_key = p.draft_key
 AND ko.pick_id = p.pick_id
 AND s.yahoo_player_key IS NULL
 AND qo.yahoo_player_key IS NULL;
