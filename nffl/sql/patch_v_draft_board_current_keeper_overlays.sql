CREATE OR REPLACE FUNCTION nffl.current_qo_placeholders(
    p_league_key text,
    p_season_year integer,
    p_draft_key text
)
RETURNS TABLE (
    team_key text,
    qo_level integer,
    yahoo_player_key text
)
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    _state jsonb := '{}'::jsonb;
    _max_qo integer := 0;
    _event record;
    _found_team text;
    _found_level integer;
    _team_lvls jsonb;
    _shifted jsonb;
    _lvl_text text;
    _pkey text;
    _lvl integer;
    _new_level integer;
BEGIN
    SELECT COALESCE(MAX(q.qo_level), 0)
      INTO _max_qo
    FROM public.qualifying_offer q
    WHERE q.league_key = p_league_key
      AND q.season_year = p_season_year;

    SELECT COALESCE(
        jsonb_object_agg(team_qos.team_key, team_qos.levels),
        '{}'::jsonb
    )
      INTO _state
    FROM (
        SELECT
            q.team_key,
            jsonb_object_agg(q.qo_level::text, q.yahoo_player_key ORDER BY q.qo_level) AS levels
        FROM public.qualifying_offer q
        WHERE q.league_key = p_league_key
          AND q.season_year = p_season_year
        GROUP BY q.team_key
    ) team_qos;

    FOR _event IN
        SELECT
            p.round_number AS round_level,
            COALESCE(NULLIF(ds.selecting_team_key, ''), p.current_owner_team_key) AS owner_team_key,
            ds.yahoo_player_key AS selected_player_key,
            UPPER(ds.pick_kind) AS pick_kind
        FROM nffl.draft_selection ds
        JOIN nffl.draft_pick p
          ON p.draft_key = ds.draft_key
         AND p.pick_id = ds.pick_id
        WHERE ds.draft_key = p_draft_key
          AND p.pick_type = 'QO'
          AND p.round_number BETWEEN 1 AND _max_qo
          AND ds.yahoo_player_key IS NOT NULL
        ORDER BY p.round_number, p.slot_number
    LOOP
        _found_team := NULL;
        _found_level := NULL;

        SELECT t.key, l.key::integer
          INTO _found_team, _found_level
        FROM jsonb_each(_state) AS t(key, levels)
        CROSS JOIN LATERAL jsonb_each_text(t.levels) AS l(key, value)
        WHERE l.value = _event.selected_player_key
        ORDER BY l.key::integer
        LIMIT 1;

        IF _event.pick_kind = 'QO' THEN
            IF _found_team IS NOT NULL AND _found_team = _event.owner_team_key THEN
                _team_lvls := COALESCE(_state -> _event.owner_team_key, '{}'::jsonb);
                _team_lvls := _team_lvls - (_event.round_level::text);
                _team_lvls := _team_lvls - (_found_level::text);

                IF _found_level <> _event.round_level THEN
                    _shifted := '{}'::jsonb;
                    FOR _lvl_text, _pkey IN
                        SELECT l.key, l.value
                        FROM jsonb_each_text(_team_lvls) AS l(key, value)
                    LOOP
                        _lvl := _lvl_text::integer;
                        _new_level := CASE WHEN _lvl > _found_level THEN _lvl - 1 ELSE _lvl END;

                        IF _new_level BETWEEN 1 AND _max_qo THEN
                            _shifted := jsonb_set(_shifted, ARRAY[_new_level::text], to_jsonb(_pkey), true);
                        END IF;
                    END LOOP;
                    _team_lvls := _shifted;
                END IF;

                _state := jsonb_set(_state, ARRAY[_event.owner_team_key], _team_lvls, true);
            ELSE
                _team_lvls := COALESCE(_state -> _event.owner_team_key, '{}'::jsonb);
                _team_lvls := _team_lvls - (_event.round_level::text);
                _state := jsonb_set(_state, ARRAY[_event.owner_team_key], _team_lvls, true);
            END IF;

        ELSIF _event.pick_kind = 'FA' THEN
            _team_lvls := COALESCE(_state -> _event.owner_team_key, '{}'::jsonb);
            _team_lvls := _team_lvls - (_event.round_level::text);
            _state := jsonb_set(_state, ARRAY[_event.owner_team_key], _team_lvls, true);

        ELSIF _event.pick_kind = 'POACH' THEN
            _team_lvls := COALESCE(_state -> _event.owner_team_key, '{}'::jsonb);
            _team_lvls := _team_lvls - (_event.round_level::text);
            _state := jsonb_set(_state, ARRAY[_event.owner_team_key], _team_lvls, true);

            IF _found_team IS NOT NULL AND _found_level IS NOT NULL THEN
                _team_lvls := COALESCE(_state -> _found_team, '{}'::jsonb);
                _team_lvls := _team_lvls - (_found_level::text);

                _shifted := '{}'::jsonb;
                FOR _lvl_text, _pkey IN
                    SELECT l.key, l.value
                    FROM jsonb_each_text(_team_lvls) AS l(key, value)
                LOOP
                    _lvl := _lvl_text::integer;
                    _new_level := CASE WHEN _lvl > _found_level THEN _lvl - 1 ELSE _lvl END;

                    IF _new_level BETWEEN 1 AND _max_qo THEN
                        _shifted := jsonb_set(_shifted, ARRAY[_new_level::text], to_jsonb(_pkey), true);
                    END IF;
                END LOOP;

                _state := jsonb_set(_state, ARRAY[_found_team], _shifted, true);
            END IF;
        END IF;
    END LOOP;

    RETURN QUERY
    SELECT
        t.key::text AS team_key,
        l.key::integer AS qo_level,
        l.value::text AS yahoo_player_key
    FROM jsonb_each(_state) AS t(key, levels)
    CROSS JOIN LATERAL jsonb_each_text(t.levels) AS l(key, value)
    WHERE l.value IS NOT NULL
      AND l.value <> ''
    ORDER BY t.key, l.key::integer;
END;
$$;

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
),
live_qo AS (
    SELECT
        q.team_key,
        q.qo_level,
        q.yahoo_player_key
    FROM nffl.current_qo_placeholders(
        '470.l.84346',
        2026,
        'nffl_2026_preseason'
    ) q
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

LEFT JOIN live_qo qo
  ON qo.team_key = p.current_owner_team_key
 AND qo.qo_level = p.round_number
 AND p.pick_type = 'QO'
 AND s.yahoo_player_key IS NULL

LEFT JOIN nffl.player_universe qo_player
  ON qo_player.league_key = '470.l.84346'
 AND qo_player.season_year = 2026
 AND qo_player.yahoo_player_key = qo.yahoo_player_key

LEFT JOIN keeper_overlay ko
  ON ko.draft_key = p.draft_key
 AND ko.pick_id = p.pick_id
 AND s.yahoo_player_key IS NULL
 AND qo.yahoo_player_key IS NULL;
