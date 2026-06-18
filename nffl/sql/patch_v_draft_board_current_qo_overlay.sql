CREATE OR REPLACE VIEW nffl.v_draft_board_current AS
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

    COALESCE(s.yahoo_player_key, qo.yahoo_player_key) AS yahoo_player_key,

    COALESCE(
        selected_player.full_name,
        qo_player.full_name
    ) AS selected_player_name,

    COALESCE(
        s.pick_kind,
        CASE
            WHEN qo.yahoo_player_key IS NOT NULL THEN 'QO_PLACEHOLDER'
            ELSE NULL
        END
    ) AS pick_kind,

    s.selected_at_utc

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
 AND qo_player.yahoo_player_key = qo.yahoo_player_key;
