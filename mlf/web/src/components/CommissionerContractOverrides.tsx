"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";

import styles from "../app/commissioner/page.module.css";


type ContractTeam = {
  team_key: string;
  team_name: string;
};


type ContractPlayer = {
  yahoo_player_key: string;
  name: string;
  mlb_team: string;
  positions: string[];
  rank_value: number | null;

  active_contract_team_key: string | null;
  active_contract_team_name: string | null;
  active_contract_years_remaining: number | null;
  effective_source: string | null;

  prospect_team_key: string | null;
  prospect_team_name: string | null;

  override_exists: boolean;
  override_team_key: string | null;
  override_years_remaining: number | null;
  override_note: string | null;

  base_contract_team_key: string | null;
  base_contract_years_remaining: number | null;
};


type ContractOverrideRow = {
  yahoo_player_key: string;
  player_name: string;
  team_key: string | null;
  team_name: string | null;
  years_remaining: number;
  note: string | null;
  updated_at_utc: string | null;
};


type ContractState = {
  draft_key: string;
  draft_status: string;
  selection_count: number;
  can_edit: boolean;
  lock_reason: string | null;
  active_contract_count: number;
  override_count: number;
  void_override_count: number;
  teams: ContractTeam[];
  players: ContractPlayer[];
  overrides: ContractOverrideRow[];
};


type WriteStatus = {
  write_enabled: boolean;
  user_id: number | null;
  email: string | null;
  authority: string;
  must_change_password: boolean;
};


type MutationResponse = {
  action: string;
  team_key: string | null;
  yahoo_player_key: string;
  years_remaining: number | null;
  keeper_assignments: number;
  performed_by: string;
  state: ContractState;
};


type ActionMode = "set" | "void";


const CONTRACT_YEARS = [
  1,
  2,
  3,
  4,
  5,
];


export function CommissionerContractOverrides() {
  const [
    state,
    setState,
  ] = useState<ContractState | null>(null);

  const [
    writeStatus,
    setWriteStatus,
  ] = useState<WriteStatus | null>(null);

  const [
    mode,
    setMode,
  ] = useState<ActionMode>("set");

  const [
    search,
    setSearch,
  ] = useState("");

  const [
    selectedPlayer,
    setSelectedPlayer,
  ] = useState("");

  const [
    teamKey,
    setTeamKey,
  ] = useState("");

  const [
    years,
    setYears,
  ] = useState(1);

  const [
    note,
    setNote,
  ] = useState("");

  const [
    loading,
    setLoading,
  ] = useState(true);

  const [
    busy,
    setBusy,
  ] = useState(false);

  const [
    message,
    setMessage,
  ] = useState("");


  const loadWriteStatus =
    useCallback(async (): Promise<
      WriteStatus | null
    > => {
      try {
        const response = await fetch(
          "/gateway/commissioner/write-status",
          {
            credentials: "same-origin",
            cache: "no-store",
          },
        );

        if (!response.ok) {
          return null;
        }

        const body =
          await response.json();

        const nextStatus =
          body as WriteStatus;

        setWriteStatus(nextStatus);

        return nextStatus;
      } catch {
        return null;
      }
    }, []);


  const loadState =
    useCallback(
      async (): Promise<ContractState> => {
        const response = await fetch(
          "/gateway/commissioner/contract-overrides",
          {
            credentials: "same-origin",
            cache: "no-store",
          },
        );

        if (!response.ok) {
          throw new Error(
            "Contract Override state is unavailable.",
          );
        }

        const body =
          await response.json();

        const nextState =
          body as ContractState;

        setState(nextState);

        return nextState;
      },
      [],
    );


  useEffect(() => {
    let active = true;

    async function initialLoad() {
      try {
        const nextState =
          await loadState();

        await loadWriteStatus();

        if (!active) {
          return;
        }

        setTeamKey(
          nextState.teams[0]
            ?.team_key
          ?? "",
        );

      } catch (error) {
        if (active) {
          setMessage(
            error instanceof Error
              ? error.message
              : (
                "Contract Override state "
                + "is unavailable."
              ),
          );
        }

      } finally {
        if (active) {
          setLoading(false);
        }
      }
    }

    void initialLoad();

    const onWriteStatusChanged = () => {
      void loadWriteStatus();
    };

    window.addEventListener(
      "mlf-commissioner-write-status-changed",
      onWriteStatusChanged,
    );

    return () => {
      active = false;

      window.removeEventListener(
        "mlf-commissioner-write-status-changed",
        onWriteStatusChanged,
      );
    };
  }, [
    loadState,
    loadWriteStatus,
  ]);


  const playersByKey = useMemo(
    () => new Map(
      (state?.players ?? []).map(
        (player) => [
          player.yahoo_player_key,
          player,
        ],
      ),
    ),
    [state],
  );


  const teamNames = useMemo(
    () => new Map(
      (state?.teams ?? []).map(
        (team) => [
          team.team_key,
          team.team_name,
        ],
      ),
    ),
    [state],
  );


  const candidatePlayers = useMemo(
    () => {
      const query =
        search.trim().toLowerCase();

      return (
        (state?.players ?? [])
          .filter(
            (player) => (
              mode === "void"
                ? (
                    player
                      .active_contract_years_remaining
                    !== null
                    && player
                      .active_contract_years_remaining
                    > 0
                  )
                : (
                    player.prospect_team_key
                    === null
                  )
            ),
          )
          .filter(
            (player) => {
              if (!query) {
                return true;
              }

              const haystack = [
                player.name,
                player.mlb_team,
                ...player.positions,
                player.yahoo_player_key,
                player.active_contract_team_name
                  ?? "",
              ]
                .join(" ")
                .toLowerCase();

              return haystack.includes(
                query,
              );
            },
          )
          .slice(0, 150)
      );
    },
    [
      state,
      mode,
      search,
    ],
  );


  const selectedPlayerState =
    selectedPlayer
      ? (
          playersByKey.get(
            selectedPlayer,
          )
          ?? null
        )
      : null;


  function playerLabel(
    player: ContractPlayer,
  ): string {
    const bits: string[] = [];

    if (player.rank_value !== null) {
      bits.push(
        `#${player.rank_value}`,
      );
    }

    bits.push(player.name);

    if (player.mlb_team) {
      bits.push(player.mlb_team);
    }

    if (player.positions.length > 0) {
      bits.push(
        player.positions.join("/"),
      );
    }

    if (
      player
        .active_contract_years_remaining
      !== null
    ) {
      bits.push(
        `${player.active_contract_years_remaining}yr`,
      );
    }

    return bits.join(" - ");
  }


  async function saveOverride() {
    if (
      !state
      || !selectedPlayer
      || !state.can_edit
      || busy
    ) {
      return;
    }

    const currentWriteStatus =
      await loadWriteStatus();

    if (
      currentWriteStatus?.write_enabled
      !== true
    ) {
      setMessage(
        "Commissioner writes are locked. "
        + "Enable the named write identity "
        + "in Step 2 first.",
      );

      return;
    }

    const player =
      playersByKey.get(
        selectedPlayer,
      );

    const isVoid =
      mode === "void";

    if (
      !isVoid
      && !teamKey
    ) {
      setMessage(
        "Select the contract team.",
      );

      return;
    }

    const accepted = window.confirm(
      isVoid
        ? (
            `Void the active contract for ${
              player?.name
              ?? selectedPlayer
            }? This changes contract control only; `
            + "it does not remove a Prospect Tag. "
            + "Keeper assignments will be rebuilt."
          )
        : (
            `Set ${
              years
            } contract year${
              years === 1
                ? ""
                : "s"
            } for ${
              player?.name
              ?? selectedPlayer
            } on ${
              teamNames.get(teamKey)
              ?? teamKey
            }? Keeper assignments will be rebuilt.`
          ),
    );

    if (!accepted) {
      return;
    }

    setBusy(true);
    setMessage("");

    try {
      const response = await fetch(
        "/gateway/commissioner/"
        + "contract-overrides/"
        + encodeURIComponent(
            selectedPlayer,
          ),
        {
          method: "POST",
          credentials: "same-origin",
          headers: {
            "content-type":
              "application/json",
          },
          body: JSON.stringify({
            team_key:
              isVoid
                ? null
                : teamKey,
            years_remaining:
              isVoid
                ? 0
                : years,
            note:
              note.trim()
              || null,
          }),
        },
      );

      const body =
        await response.json();

      if (!response.ok) {
        if (response.status === 401) {
          throw new Error(
            "Commissioner write authorization "
            + "expired. Enable writes again.",
          );
        }

        if (response.status === 400) {
          throw new Error(
            "The requested contract override "
            + "is not valid.",
          );
        }

        if (response.status === 409) {
          throw new Error(
            "Contract state changed or the "
            + "keeper rebuild rejected the "
            + "override. Reload before retrying.",
          );
        }

        throw new Error(
          "Contract Override update failed.",
        );
      }

      const result =
        body as MutationResponse;

      setState(result.state);
      setSelectedPlayer("");
      setSearch("");
      setNote("");

      setMessage(
        result.action === "void"
          ? (
              "Contract void saved and "
              + "keeper assignments rebuilt."
            )
          : (
              "Contract override saved and "
              + "keeper assignments rebuilt."
            ),
      );

    } catch (error) {
      setMessage(
        error instanceof Error
          ? error.message
          : "Contract Override update failed.",
      );

      try {
        await loadState();
      } catch {
        // Preserve original error.
      }

    } finally {
      setBusy(false);
    }
  }


  async function deleteOverride(
    playerKey: string,
  ) {
    if (
      !state
      || !playerKey
      || busy
    ) {
      return;
    }

    const currentWriteStatus =
      await loadWriteStatus();

    if (
      currentWriteStatus?.write_enabled
      !== true
    ) {
      setMessage(
        "Commissioner writes are locked. "
        + "Enable the named write identity "
        + "in Step 2 first.",
      );

      return;
    }

    const player =
      playersByKey.get(playerKey);

    const override =
      state.overrides.find(
        (row) => (
          row.yahoo_player_key
          === playerKey
        ),
      );

    const accepted = window.confirm(
      "Delete the Commissioner override for "
      + `${
          player?.name
          ?? override?.player_name
          ?? playerKey
        }? `
      + "Underlying base-contract truth, if any, "
      + "will become effective again. "
      + "Keeper assignments will be rebuilt.",
    );

    if (!accepted) {
      return;
    }

    setBusy(true);
    setMessage("");

    try {
      const response = await fetch(
        "/gateway/commissioner/"
        + "contract-overrides/"
        + encodeURIComponent(
            playerKey,
          ),
        {
          method: "DELETE",
          credentials: "same-origin",
          headers: {
            "content-type":
              "application/json",
          },
          body: "{}",
        },
      );

      const body =
        await response.json();

      if (!response.ok) {
        if (response.status === 401) {
          throw new Error(
            "Commissioner write authorization "
            + "expired. Enable writes again.",
          );
        }

        if (response.status === 409) {
          throw new Error(
            "Deleting this override would "
            + "create an invalid control state. "
            + "Resolve the Prospect Tag first.",
          );
        }

        if (response.status === 400) {
          throw new Error(
            "That contract override no longer exists.",
          );
        }

        throw new Error(
          "Contract Override deletion failed.",
        );
      }

      const result =
        body as MutationResponse;

      setState(result.state);

      if (
        selectedPlayer === playerKey
      ) {
        setSelectedPlayer("");
      }

      setMessage(
        "Contract override deleted and "
        + "keeper assignments rebuilt.",
      );

    } catch (error) {
      setMessage(
        error instanceof Error
          ? error.message
          : "Contract Override deletion failed.",
      );

      try {
        await loadState();
      } catch {
        // Preserve original error.
      }

    } finally {
      setBusy(false);
    }
  }


  if (loading) {
    return (
      <>
        <div className={styles.stepTitleRow}>
          <strong>
            Contract Overrides
          </strong>

          <span
            className={
              styles.writeLockedBadge
            }
          >
            Loading
          </span>
        </div>

        <span>
          Loading Contract Override state...
        </span>
      </>
    );
  }


  const writeEnabled = (
    writeStatus?.write_enabled
    === true
  );


  return (
    <>
      <div className={styles.stepTitleRow}>
        <strong>
          Contract Overrides
        </strong>

        <span
          className={
            state?.can_edit
              ? (
                  writeEnabled
                    ? styles.stepReady
                    : styles.writeLockedBadge
                )
              : styles.writeLockedBadge
          }
        >
          {state?.can_edit
            ? (
                writeEnabled
                  ? "Write enabled"
                  : "Write locked"
              )
            : "Data locked"}
        </span>
      </div>

      <span>
        Correct contract ownership or years,
        void an effective contract, or remove a
        prior Commissioner override.
      </span>

      {state && (
        <div className={styles.ptPanel}>
          <div className={styles.tradeContext}>
            <span>
              Active contracts
              <strong>
                {state.active_contract_count}
              </strong>
            </span>

            <span>
              Overrides
              <strong>
                {state.override_count}
              </strong>
            </span>

            <span>
              Contract voids
              <strong>
                {state.void_override_count}
              </strong>
            </span>

            <span>
              Selections
              <strong>
                {state.selection_count}
              </strong>
            </span>
          </div>

          <div className={styles.tradeNotice}>
            Contract years are 1-5. A void writes
            years_remaining=0 and affects contract
            control only. Prospect Tags remain
            separate in Step 5.
          </div>

          {state.lock_reason && (
            <div className={styles.tradeWriteNotice}>
              {state.lock_reason}
            </div>
          )}

          <div className={styles.ptSelector}>
            <label className={styles.ptField}>
              <span>
                Action
              </span>

              <select
                value={mode}
                onChange={(event) => {
                  setMode(
                    event.target.value === "void"
                      ? "void"
                      : "set",
                  );
                  setSelectedPlayer("");
                  setSearch("");
                  setMessage("");
                }}
              >
                <option value="set">
                  Set / correct contract
                </option>

                <option value="void">
                  Void active contract
                </option>
              </select>
            </label>

            <label className={styles.ptField}>
              <span>
                Search players
              </span>

              <input
                type="search"
                value={search}
                placeholder={
                  "Name, MLB team, position..."
                }
                onChange={(event) => {
                  setSearch(
                    event.target.value,
                  );
                }}
              />
            </label>
          </div>

          <label className={styles.ptField}>
            <span>
              Player
            </span>

            <select
              value={selectedPlayer}
              disabled={!state.can_edit}
              onChange={(event) => {
                setSelectedPlayer(
                  event.target.value,
                );
                setMessage("");
              }}
            >
              <option value="">
                {mode === "void"
                  ? "Select active contract"
                  : "Select player"}
              </option>

              {candidatePlayers.map(
                (player) => (
                  <option
                    key={
                      player.yahoo_player_key
                    }
                    value={
                      player.yahoo_player_key
                    }
                  >
                    {playerLabel(player)}
                  </option>
                ),
              )}
            </select>
          </label>

          {mode === "set" && (
            <div className={styles.ptSelector}>
              <label className={styles.ptField}>
                <span>
                  Contract team
                </span>

                <select
                  value={teamKey}
                  onChange={(event) => {
                    setTeamKey(
                      event.target.value,
                    );
                    setMessage("");
                  }}
                >
                  {state.teams.map(
                    (team) => (
                      <option
                        key={team.team_key}
                        value={team.team_key}
                      >
                        {team.team_name}
                      </option>
                    ),
                  )}
                </select>
              </label>

              <label className={styles.ptField}>
                <span>
                  Years remaining
                </span>

                <select
                  value={years}
                  onChange={(event) => {
                    setYears(
                      Number(
                        event.target.value,
                      ),
                    );
                    setMessage("");
                  }}
                >
                  {CONTRACT_YEARS.map(
                    (value) => (
                      <option
                        key={value}
                        value={value}
                      >
                        {value}
                      </option>
                    ),
                  )}
                </select>
              </label>
            </div>
          )}

          <label className={styles.ptField}>
            <span>
              Note (optional)
            </span>

            <input
              type="text"
              maxLength={500}
              value={note}
              placeholder={
                mode === "void"
                  ? "Reason for contract void"
                  : "Reason for correction"
              }
              onChange={(event) => {
                setNote(
                  event.target.value,
                );
              }}
            />
          </label>

          {selectedPlayerState && (
            <div className={styles.ptPreview}>
              <span>
                Player
                <strong>
                  {selectedPlayerState.name}
                </strong>
              </span>

              <span>
                Effective contract
                <strong>
                  {selectedPlayerState
                    .active_contract_years_remaining
                    !== null
                    ? (
                        `${selectedPlayerState
                          .active_contract_years_remaining} yr - `
                        + (
                          selectedPlayerState
                            .active_contract_team_name
                          ?? "Unknown team"
                        )
                      )
                    : "None"}
                </strong>
              </span>

              <span>
                Source
                <strong>
                  {selectedPlayerState
                    .effective_source
                    ?? "-"}
                </strong>
              </span>

              <span>
                Prospect Tag
                <strong>
                  {selectedPlayerState
                    .prospect_team_name
                    ?? "None"}
                </strong>
              </span>

              <span>
                Override
                <strong>
                  {selectedPlayerState
                    .override_exists
                    ? (
                        selectedPlayerState
                          .override_years_remaining
                        === 0
                          ? "VOID"
                          : (
                              `${
                                selectedPlayerState
                                  .override_years_remaining
                              } yr`
                            )
                      )
                    : "None"}
                </strong>
              </span>
            </div>
          )}

          <div className={styles.ptActions}>
            <button
              type="button"
              className={
                styles.writeSecondaryButton
              }
              disabled={
                !selectedPlayerState
                  ?.override_exists
                || busy
                || !writeEnabled
              }
              onClick={() => {
                if (selectedPlayer) {
                  void deleteOverride(
                    selectedPlayer,
                  );
                }
              }}
            >
              Delete Override
            </button>

            <button
              type="button"
              className={
                styles.writePrimaryButton
              }
              disabled={
                !state.can_edit
                || !selectedPlayer
                || busy
                || !writeEnabled
              }
              onClick={() => {
                void saveOverride();
              }}
            >
              {busy
                ? "Saving..."
                : (
                    mode === "void"
                      ? "Void Contract"
                      : "Save Contract Override"
                  )}
            </button>
          </div>

          {!writeEnabled && (
            <div className={styles.tradeWriteNotice}>
              Enable Commissioner Writes in
              Step 2 to change contract overrides.
            </div>
          )}

          {message && (
            <div
              className={styles.writeMessage}
              aria-live="polite"
            >
              {message}
            </div>
          )}

          <div className={styles.ptCurrent}>
            <span>
              Existing overrides
            </span>

            <strong>
              {state.override_count}
            </strong>
          </div>

          {state.overrides.length === 0 ? (
            <div className={styles.tradeNotice}>
              No Commissioner contract overrides
              are currently saved.
            </div>
          ) : (
            <div className={styles.tableWrap}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th>
                      Player
                    </th>
                    <th>
                      Override
                    </th>
                    <th>
                      Team
                    </th>
                    <th>
                      Note
                    </th>
                    <th>
                      Action
                    </th>
                  </tr>
                </thead>

                <tbody>
                  {state.overrides.map(
                    (row) => (
                      <tr
                        key={
                          row.yahoo_player_key
                        }
                      >
                        <td>
                          <strong>
                            {row.player_name}
                          </strong>
                        </td>

                        <td>
                          {row.years_remaining === 0
                            ? "VOID"
                            : (
                                `${row.years_remaining} yr`
                              )}
                        </td>

                        <td>
                          {row.team_name ?? "-"}
                        </td>

                        <td>
                          {row.note ?? "-"}
                        </td>

                        <td>
                          <button
                            type="button"
                            className={
                              styles.writeSecondaryButton
                            }
                            disabled={
                              busy
                              || !writeEnabled
                            }
                            onClick={() => {
                              void deleteOverride(
                                row.yahoo_player_key,
                              );
                            }}
                          >
                            Delete
                          </button>
                        </td>
                      </tr>
                    ),
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </>
  );
}
