import { getPool } from "./db";

export type BoardRow = {
  draft_key: string;
  pick_id: string | number;
  round_number: number;
  slot_number: number;
  round_label: string;
  pick_type: string;
  column_team_key: string;
  column_team_name: string;
  current_owner_team_key: string;
  current_owner_team_name: string;
  traded_flag: boolean;
  ownership_note: string | null;
  yahoo_player_key: string | null;
  selected_player_name: string | null;
  pick_kind: string | null;
  selected_at_utc: Date | null;
  selected_primary_position: string | null;
  placeholder_source: string | null;
  contract_years_remaining: number | null;
};

const BOARD_SQL = `
SELECT
    dp.draft_key,
    dp.pick_id,
    dp.round_number,
    dp.slot_number,
    dp.round_label,
    dp.pick_type,

    dp.column_team_key,
    COALESCE(column_team.team_name, dp.column_team_key)
        AS column_team_name,

    dp.current_owner_team_key,
    COALESCE(owner_team.team_name, dp.current_owner_team_key)
        AS current_owner_team_name,

    dp.traded_flag,
    dp.ownership_note,

    COALESCE(ds.yahoo_player_key, dka.yahoo_player_key)
        AS yahoo_player_key,

    pu.player_name
        AS selected_player_name,

    CASE
        WHEN ds.yahoo_player_key IS NOT NULL
            THEN COALESCE(ds.pick_kind, 'STANDARD')
        WHEN UPPER(COALESCE(dka.keeper_kind, '')) = 'CONTRACT'
            THEN 'CONTRACT_PLACEHOLDER'
        WHEN UPPER(COALESCE(dka.keeper_kind, '')) = 'PT'
            THEN 'PT_PLACEHOLDER'
        WHEN dka.keeper_kind IS NOT NULL
            THEN UPPER(dka.keeper_kind)
        ELSE NULL
    END AS pick_kind,

    ds.selected_at_utc,

    pu.primary_position
        AS selected_primary_position,

    dka.keeper_kind
        AS placeholder_source,

    CASE
        WHEN UPPER(COALESCE(dka.keeper_kind, '')) = 'CONTRACT'
            THEN contract_effective.years_remaining
        ELSE NULL
    END AS contract_years_remaining

FROM mlf.draft_pick AS dp

JOIN mlf.draft AS d
  ON d.draft_key = dp.draft_key

LEFT JOIN mlf.draft_selection AS ds
  ON ds.draft_key = dp.draft_key
 AND ds.pick_id = dp.pick_id

LEFT JOIN mlf.draft_keeper_assignment AS dka
  ON dka.draft_key = dp.draft_key
 AND dka.pick_id = dp.pick_id

LEFT JOIN mlf.team AS column_team
  ON column_team.league_key = d.league_key
 AND column_team.season_year = d.season_year
 AND column_team.team_key = dp.column_team_key

LEFT JOIN mlf.team AS owner_team
  ON owner_team.league_key = d.league_key
 AND owner_team.season_year = d.season_year
 AND owner_team.team_key = dp.current_owner_team_key

LEFT JOIN mlf.player_universe AS pu
  ON pu.league_key = d.league_key
 AND pu.season_year = d.season_year
 AND pu.yahoo_player_key =
     COALESCE(ds.yahoo_player_key, dka.yahoo_player_key)

LEFT JOIN mlf.v_contract_effective AS contract_effective
  ON contract_effective.league_key = d.league_key
 AND contract_effective.season_year = d.season_year
 AND contract_effective.yahoo_player_key =
     COALESCE(ds.yahoo_player_key, dka.yahoo_player_key)

WHERE dp.draft_key = $1

ORDER BY
    dp.round_number,
    dp.slot_number
`;

export async function loadBoard(
  draftKey: string,
): Promise<BoardRow[]> {
  const client = await getPool().connect();

  try {
    await client.query("BEGIN READ ONLY");

    const result = await client.query<BoardRow>(
      BOARD_SQL,
      [draftKey],
    );

    await client.query("ROLLBACK");

    return result.rows;
  } catch (error) {
    try {
      await client.query("ROLLBACK");
    } catch {
      // Preserve the original database error.
    }

    throw error;
  } finally {
    client.release();
  }
}