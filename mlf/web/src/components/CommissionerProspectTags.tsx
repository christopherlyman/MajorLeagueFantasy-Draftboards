"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";

import styles from "../app/commissioner/page.module.css";


type ProspectPlayer = {
  yahoo_player_key: string;
  name: string;
  mlb_team: string;
  positions: string[];
  rank_value: number | null;
  h_ab: string | null;
  ip: number | null;
  percent_owned: number | null;
  is_qo_eligible: boolean;
  eligible_for_pt: boolean;
  ineligibility_reason: string | null;
  prospect_team_key: string | null;
  prospect_note: string | null;
};


type ProspectTeam = {
  team_key: string;
  team_name: string;
  prospect_player_keys: string[];
};


type ProspectState = {
  draft_key: string;
  draft_status: string;
  selection_count: number;
  mirror_synced: boolean;
  team_multiplicity_valid: boolean;
  can_edit: boolean;
  lock_reason: string | null;
  prospect_count: number;
  keeper_pt_count: number;
  eligible_count: number;
  teams: ProspectTeam[];
  players: ProspectPlayer[];
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
  team_key: string;
  yahoo_player_key: string;
  keeper_assignments: number;
  performed_by: string;
  state: ProspectState;
};


export function CommissionerProspectTags() {
  const [
    state,
    setState,
  ] = useState<ProspectState | null>(null);

  const [
    writeStatus,
    setWriteStatus,
  ] = useState<WriteStatus | null>(null);

  const [
    teamKey,
    setTeamKey,
  ] = useState("");

  const [
    search,
    setSearch,
  ] = useState("");

  const [
    selectedPlayer,
    setSelectedPlayer,
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
      async (): Promise<ProspectState> => {
        const response = await fetch(
          "/gateway/commissioner/prospect-tags",
          {
            credentials: "same-origin",
            cache: "no-store",
          },
        );

        if (!response.ok) {
          throw new Error(
            "Prospect Tag state is unavailable.",
          );
        }

        const body =
          await response.json();

        const nextState =
          body as ProspectState;

        setState(nextState);

        return nextState;
      },
      [],
    );


  useEffect(() => {
    let active = true;

    async function initialLoad() {
      try {
        const [
          nextState,
        ] = await Promise.all([
          loadState(),
          loadWriteStatus(),
        ]);

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
                "Prospect Tag state "
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


  const currentTeam = useMemo(
    () => (
      state?.teams.find(
        (team) => (
          team.team_key === teamKey
        ),
      )
      ?? null
    ),
    [
      state,
      teamKey,
    ],
  );


  const currentPlayerKey = (
    currentTeam
      ?.prospect_player_keys[0]
    ?? ""
  );


  const currentPlayer = (
    currentPlayerKey
      ? playersByKey.get(
          currentPlayerKey,
        )
      : null
  );


  const candidatePlayers = useMemo(
    () => {
      const query =
        search.trim().toLowerCase();

      return (
        (state?.players ?? [])
          .filter(
            (player) => (
              player.eligible_for_pt
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
      search,
    ],
  );


  function playerLabel(
    player: ProspectPlayer,
  ): string {
    const bits: string[] = [];

    if (player.rank_value !== null) {
      bits.push(
        `#${player.rank_value}`,
      );
    }

    bits.push(
      player.name,
    );

    if (player.mlb_team) {
      bits.push(
        player.mlb_team,
      );
    }

    if (player.positions.length > 0) {
      bits.push(
        player.positions.join("/"),
      );
    }

    return bits.join(" — ");
  }


  async function refreshAfterMutation(
    nextState: ProspectState,
    nextTeamKey: string,
  ) {
    setState(nextState);
    setTeamKey(nextTeamKey);
    setSelectedPlayer("");
    setSearch("");
  }


  async function saveTag() {
    if (
      !state
      || !teamKey
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

    const teamName =
      currentTeam?.team_name
      ?? teamKey;

    const player =
      playersByKey.get(
        selectedPlayer,
      );

    const accepted = window.confirm(
      `${
        currentPlayerKey
          ? "Replace"
          : "Add"
      } the Prospect Tag for ${teamName}`
      + (
        player
          ? ` with ${player.name}`
          : ""
      )
      + "? Keeper assignments will be rebuilt.",
    );

    if (!accepted) {
      return;
    }

    setBusy(true);
    setMessage("");

    try {
      const response = await fetch(
        "/gateway/commissioner/"
        + "prospect-tags/"
        + encodeURIComponent(teamKey),
        {
          method: "POST",
          credentials: "same-origin",
          headers: {
            "content-type":
              "application/json",
          },
          body: JSON.stringify({
            yahoo_player_key:
              selectedPlayer,
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

        if (response.status === 409) {
          throw new Error(
            "Prospect Tag state changed or "
            + "the PT mirrors are not synchronized. "
            + "Reload before retrying.",
          );
        }

        if (response.status === 400) {
          throw new Error(
            "Player is not eligible for a "
            + "Prospect Tag.",
          );
        }

        throw new Error(
          "Prospect Tag update failed.",
        );
      }

      const result =
        body as MutationResponse;

      await refreshAfterMutation(
        result.state,
        result.team_key,
      );

      setMessage(
        result.action === "replace"
          ? (
              "Prospect Tag replaced and "
              + "keeper assignments rebuilt."
            )
          : (
              "Prospect Tag added and "
              + "keeper assignments rebuilt."
            ),
      );

    } catch (error) {
      setMessage(
        error instanceof Error
          ? error.message
          : "Prospect Tag update failed.",
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


  async function removeTag() {
    if (
      !state
      || !teamKey
      || !currentPlayerKey
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

    const teamName =
      currentTeam?.team_name
      ?? teamKey;

    const accepted = window.confirm(
      `Remove the Prospect Tag for `
      + `${teamName}`
      + (
        currentPlayer
          ? ` (${currentPlayer.name})`
          : ""
      )
      + "? Keeper assignments will be rebuilt.",
    );

    if (!accepted) {
      return;
    }

    setBusy(true);
    setMessage("");

    try {
      const response = await fetch(
        "/gateway/commissioner/"
        + "prospect-tags/"
        + encodeURIComponent(teamKey),
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
            "Prospect Tag state changed or "
            + "the PT mirrors are not synchronized. "
            + "Reload before retrying.",
          );
        }

        throw new Error(
          "Prospect Tag removal failed.",
        );
      }

      const result =
        body as MutationResponse;

      await refreshAfterMutation(
        result.state,
        result.team_key,
      );

      setMessage(
        "Prospect Tag removed and "
        + "keeper assignments rebuilt.",
      );

    } catch (error) {
      setMessage(
        error instanceof Error
          ? error.message
          : "Prospect Tag removal failed.",
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
            Prospect Tags
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
          Loading Prospect Tag state…
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
          Prospect Tags
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
        Review, assign, replace, or remove one
        Prospect Tag per team.
      </span>

      {state && (
        <div className={styles.ptPanel}>
          <div className={styles.tradeContext}>
            <span>
              Prospect Tags
              <strong>
                {state.prospect_count}
              </strong>
            </span>

            <span>
              PT keeper slots
              <strong>
                {state.keeper_pt_count}
              </strong>
            </span>

            <span>
              Eligible players
              <strong>
                {state.eligible_count}
              </strong>
            </span>

            <span>
              Mirror
              <strong>
                {state.mirror_synced
                  ? "Synced"
                  : "Mismatch"}
              </strong>
            </span>
          </div>

          <div className={styles.tradeNotice}>
            PT eligibility excludes active
            contracts, existing PTs, and
            QO-eligible players. PT changes rebuild
            keeper assignments but do not rewrite
            real draft selections.
          </div>

          {state.lock_reason && (
            <div className={styles.tradeWriteNotice}>
              {state.lock_reason}
            </div>
          )}

          <label className={styles.ptField}>
            <span>
              Team
            </span>

            <select
              value={teamKey}
              onChange={(event) => {
                setTeamKey(
                  event.target.value,
                );
                setSelectedPlayer("");
                setSearch("");
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

          <div className={styles.ptCurrent}>
            <span>
              Current Prospect Tag
            </span>

            <strong>
              {currentPlayer
                ? playerLabel(
                    currentPlayer,
                  )
                : (
                    currentPlayerKey
                      || "None"
                  )}
            </strong>
          </div>

          <div className={styles.ptSelector}>
            <label className={styles.ptField}>
              <span>
                Search eligible players
              </span>

              <input
                type="search"
                value={search}
                placeholder={
                  "Name, MLB team, position…"
                }
                onChange={(event) => {
                  setSearch(
                    event.target.value,
                  );
                }}
              />
            </label>

            <label className={styles.ptField}>
              <span>
                New Prospect Tag
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
                  Select eligible player
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
          </div>

          {selectedPlayer && (
            <div className={styles.ptPreview}>
              {(() => {
                const player =
                  playersByKey.get(
                    selectedPlayer,
                  );

                if (!player) {
                  return null;
                }

                return (
                  <>
                    <span>
                      Player
                      <strong>
                        {player.name}
                      </strong>
                    </span>

                    <span>
                      Rank
                      <strong>
                        {player.rank_value
                          ?? "—"}
                      </strong>
                    </span>

                    <span>
                      H/AB
                      <strong>
                        {player.h_ab
                          ?? "—"}
                      </strong>
                    </span>

                    <span>
                      IP
                      <strong>
                        {player.ip
                          ?? "—"}
                      </strong>
                    </span>

                    <span>
                      % Owned
                      <strong>
                        {player.percent_owned
                          ?? "—"}
                      </strong>
                    </span>
                  </>
                );
              })()}
            </div>
          )}

          <div className={styles.ptActions}>
            <button
              type="button"
              className={
                styles.writeSecondaryButton
              }
              disabled={
                !state.can_edit
                || !currentPlayerKey
                || busy
                || !writeEnabled
              }
              onClick={() => {
                void removeTag();
              }}
            >
              Remove PT
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
                void saveTag();
              }}
            >
              {busy
                ? "Saving…"
                : (
                    currentPlayerKey
                      ? "Replace PT"
                      : "Add PT"
                  )}
            </button>
          </div>

          {!writeEnabled && (
            <div className={styles.tradeWriteNotice}>
              Enable Commissioner Writes in
              Step 2 to change Prospect Tags.
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
        </div>
      )}
    </>
  );
}
