"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";

import styles from "../app/commissioner/page.module.css";


type Team = {
  team_key: string;
  team_name: string;
};


type Player = {
  yahoo_player_key: string;
  name: string;
  rank_value: number | null;
  contract_team_key: string | null;
  contract_years: number;
};


type Pick = {
  pick_id: string;
  round_number: number;
  slot_number: number;
  current_owner_team_key: string;
  owner_team_name: string;
  traded_flag: boolean;
};


type BuilderState = {
  draft_key: string;
  draft_status: string;
  selection_count: number;
  teams: Team[];
  players: Player[];
  picks: Pick[];
};


type WriteStatus = {
  write_enabled: boolean;
  user_id: number | null;
  email: string | null;
  authority: string;
  must_change_password: boolean;
};


type Asset = {
  asset_type: "PLAYER" | "PICK";
  asset_id: string;
};


type SubmitReceipt = {
  player_updates: number;
  pick_updates: number;
  keeper_assignments: number;
  receipt_written: boolean;
  receipt_trade_id: string | null;
  receipt_asset_count: number;
  receipt_warning: string | null;
  performed_by: string;
};


function apiCode(body: unknown): string {
  if (
    typeof body === "object"
    && body !== null
    && "detail" in body
  ) {
    const detail = (
      body as {
        detail?: unknown;
      }
    ).detail;

    if (
      typeof detail === "object"
      && detail !== null
      && "code" in detail
    ) {
      const code = (
        detail as {
          code?: unknown;
        }
      ).code;

      if (typeof code === "string") {
        return code;
      }
    }
  }

  return "";
}


export function CommissionerTradeBuilder() {
  const [
    state,
    setState,
  ] = useState<BuilderState | null>(null);

  const [
    writeStatus,
    setWriteStatus,
  ] = useState<WriteStatus | null>(null);

  const [
    teamA,
    setTeamA,
  ] = useState("");

  const [
    teamB,
    setTeamB,
  ] = useState("");

  const [
    aGets,
    setAGets,
  ] = useState<Asset[]>([]);

  const [
    bGets,
    setBGets,
  ] = useState<Asset[]>([]);

  const [
    aPlayer,
    setAPlayer,
  ] = useState("");

  const [
    bPlayer,
    setBPlayer,
  ] = useState("");

  const [
    aPick,
    setAPick,
  ] = useState("");

  const [
    bPick,
    setBPick,
  ] = useState("");

  const [
    confirmed,
    setConfirmed,
  ] = useState(false);

  const [
    busy,
    setBusy,
  ] = useState(false);

  const [
    loading,
    setLoading,
  ] = useState(true);

  const [
    message,
    setMessage,
  ] = useState("");

  const [
    receipt,
    setReceipt,
  ] = useState<SubmitReceipt | null>(null);


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
        // Keep the mutation button locked.
        return null;
      }
    }, []);


  const loadBuilder =
    useCallback(async () => {
      try {
        const response = await fetch(
          "/gateway/commissioner/trade-builder",
          {
            credentials: "same-origin",
            cache: "no-store",
          },
        );

        if (!response.ok) {
          throw new Error(
            "Trade Builder state is unavailable.",
          );
        }

        const body =
          await response.json();

        setState(
          body as BuilderState,
        );
      } catch (error) {
        setMessage(
          error instanceof Error
            ? error.message
            : "Trade Builder state is unavailable.",
        );
      }
    }, []);


  useEffect(() => {
    let active = true;

    async function initialLoad() {
      await Promise.all([
        loadBuilder(),
        loadWriteStatus(),
      ]);

      if (active) {
        setLoading(false);
      }
    }

    void initialLoad();

    const onWriteChange = () => {
      void loadWriteStatus();
    };

    window.addEventListener(
      "mlf-commissioner-write-status-changed",
      onWriteChange,
    );

    return () => {
      active = false;

      window.removeEventListener(
        "mlf-commissioner-write-status-changed",
        onWriteChange,
      );
    };
  }, [
    loadBuilder,
    loadWriteStatus,
  ]);


  const teamsByKey = useMemo(
    () => new Map(
      (state?.teams ?? []).map(
        (team) => [
          team.team_key,
          team,
        ],
      ),
    ),
    [state],
  );


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


  const picksById = useMemo(
    () => new Map(
      (state?.picks ?? []).map(
        (pick) => [
          pick.pick_id,
          pick,
        ],
      ),
    ),
    [state],
  );


  const used = useMemo(
    () => new Set(
      [...aGets, ...bGets].map(
        (asset) => (
          `${asset.asset_type}:${asset.asset_id}`
        ),
      ),
    ),
    [
      aGets,
      bGets,
    ],
  );


  const picksForA = useMemo(
    () => (
      (state?.picks ?? []).filter(
        (pick) => (
          teamB !== ""
          && pick.current_owner_team_key
            === teamB
          && !used.has(
            `PICK:${pick.pick_id}`,
          )
        ),
      )
    ),
    [
      state,
      teamB,
      used,
    ],
  );


  const picksForB = useMemo(
    () => (
      (state?.picks ?? []).filter(
        (pick) => (
          teamA !== ""
          && pick.current_owner_team_key
            === teamA
          && !used.has(
            `PICK:${pick.pick_id}`,
          )
        ),
      )
    ),
    [
      state,
      teamA,
      used,
    ],
  );


  function playerLabel(
    player: Player,
  ): string {
    if (player.contract_years > 0) {
      const team = (
        player.contract_team_key
          ? teamsByKey.get(
              player.contract_team_key,
            )?.team_name
          : null
      );

      return (
        `${player.name} — `
        + `${player.contract_years}y contract`
        + (
          team
            ? ` (${team})`
            : ""
        )
      );
    }

    return (
      `${player.name} — `
      + "no active contract"
    );
  }


  function assetLabel(
    asset: Asset,
  ): string {
    if (asset.asset_type === "PLAYER") {
      const player =
        playersByKey.get(
          asset.asset_id,
        );

      return player
        ? playerLabel(player)
        : asset.asset_id;
    }

    const pick =
      picksById.get(
        asset.asset_id,
      );

    if (!pick) {
      return asset.asset_id;
    }

    return (
      `${pick.pick_id} — `
      + `R${pick.round_number}.`
      + `${pick.slot_number}`
    );
  }


  function addAsset(
    side: "A" | "B",
    asset: Asset,
  ) {
    const identity =
      `${asset.asset_type}:${asset.asset_id}`;

    if (
      !asset.asset_id
      || used.has(identity)
    ) {
      return;
    }

    if (side === "A") {
      setAGets(
        (current) => [
          ...current,
          asset,
        ],
      );
    } else {
      setBGets(
        (current) => [
          ...current,
          asset,
        ],
      );
    }

    setConfirmed(false);
  }


  function removeAsset(
    side: "A" | "B",
    index: number,
  ) {
    if (side === "A") {
      setAGets(
        (current) =>
          current.filter(
            (_, i) => i !== index,
          ),
      );
    } else {
      setBGets(
        (current) =>
          current.filter(
            (_, i) => i !== index,
          ),
      );
    }

    setConfirmed(false);
  }


  async function submitTrade() {
    if (
      !teamA
      || !teamB
      || teamA === teamB
      || (
        aGets.length === 0
        && bGets.length === 0
      )
      || !confirmed
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

    const teamAName =
      teamsByKey.get(teamA)
        ?.team_name
      ?? teamA;

    const teamBName =
      teamsByKey.get(teamB)
        ?.team_name
      ?? teamB;

    const accepted = window.confirm(
      `Submit this trade between `
      + `${teamAName} and ${teamBName}? `
      + `Canonical contract/pick ownership `
      + `changes will commit immediately.`,
    );

    if (!accepted) {
      return;
    }

    setBusy(true);
    setMessage("");
    setReceipt(null);

    try {
      const response = await fetch(
        "/gateway/commissioner/"
        + "trade-builder/submit",
        {
          method: "POST",
          credentials: "same-origin",
          headers: {
            "content-type":
              "application/json",
          },
          body: JSON.stringify({
            team_a_key: teamA,
            team_b_key: teamB,
            team_a_gets: aGets,
            team_b_gets: bGets,
          }),
        },
      );

      const body = await response.json();

      if (!response.ok) {
        const code = apiCode(body);

        if (
          response.status === 401
          || code
            === "commissioner_write_required"
        ) {
          throw new Error(
            "Commissioner write authorization "
            + "expired. Enable writes again.",
          );
        }

        if (
          code === "trade_state_conflict"
        ) {
          throw new Error(
            "Trade rejected because canonical "
            + "ownership changed or an asset is "
            + "no longer tradable. Reloaded state "
            + "should be reviewed before retrying.",
          );
        }

        if (
          code === "invalid_trade_request"
        ) {
          throw new Error(
            "Trade request is invalid. Review "
            + "teams and selected assets.",
          );
        }

        throw new Error(
          "Trade submission failed.",
        );
      }

      const result =
        body as SubmitReceipt;

      setReceipt(result);

      if (result.receipt_written) {
        setMessage(
          "Canonical trade and audit receipt "
          + "were committed.",
        );
      } else {
        setMessage(
          "Canonical trade committed, but the "
          + "legacy audit receipt failed. "
          + "Do not resubmit this trade blindly.",
        );
      }

      setAGets([]);
      setBGets([]);
      setAPlayer("");
      setBPlayer("");
      setAPick("");
      setBPick("");
      setConfirmed(false);

      await loadBuilder();

    } catch (error) {
      setMessage(
        error instanceof Error
          ? error.message
          : "Trade submission failed.",
      );

      await loadBuilder();

    } finally {
      setBusy(false);
    }
  }


  if (loading) {
    return (
      <>
        <div className={styles.stepTitleRow}>
          <strong>
            Trade Builder
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
          Loading canonical trade state…
        </span>
      </>
    );
  }


  const writeEnabled =
    writeStatus?.write_enabled
    === true;

  const canSubmit = (
    writeEnabled
    && teamA !== ""
    && teamB !== ""
    && teamA !== teamB
    && (
      aGets.length > 0
      || bGets.length > 0
    )
    && confirmed
    && !busy
  );


  return (
    <>
      <div className={styles.stepTitleRow}>
        <strong>
          Trade Builder
        </strong>

        <span
          className={
            writeEnabled
              ? styles.stepReady
              : styles.writeLockedBadge
          }
        >
          {writeEnabled
            ? "Write enabled"
            : "Write locked"}
        </span>
      </div>

      <span>
        Process Commissioner-managed player,
        contract, and draft-pick trades through
        canonical relational state.
      </span>

      {state && (
        <div className={styles.tradePanel}>
          <div className={styles.tradeContext}>
            <span>
              Draft
              <strong>
                {state.draft_key}
              </strong>
            </span>

            <span>
              Status
              <strong>
                {state.draft_status}
              </strong>
            </span>

            <span>
              Selections
              <strong>
                {state.selection_count}
              </strong>
            </span>

            <span>
              Tradable picks
              <strong>
                {state.picks.length}
              </strong>
            </span>
          </div>

          <div className={styles.tradeNotice}>
            Contract ownership and draft-pick
            ownership are changed canonically.
            Non-contract player assets remain
            audit-receipt only; Yahoo roster
            transactions are not performed here.
          </div>

          {!writeEnabled && (
            <div className={styles.tradeWriteNotice}>
              Enable Commissioner Writes in
              Step 2 before submitting a trade.
              You can still build and review the
              trade while writes are locked.
            </div>
          )}

          <div className={styles.tradeTeams}>
            <TradeSide
              title="Team A gets"
              teamLabel="Team A"
              selectedTeam={teamA}
              otherTeam={teamB}
              teams={state.teams}
              players={state.players}
              selectedPlayer={aPlayer}
              setSelectedPlayer={setAPlayer}
              selectedPick={aPick}
              setSelectedPick={setAPick}
              picks={picksForA}
              assets={aGets}
              playerLabel={playerLabel}
              assetLabel={assetLabel}
              onTeamChange={(value) => {
                setTeamA(value);
                setAGets([]);
                setBGets([]);
                setAPick("");
                setBPick("");
                setConfirmed(false);
              }}
              onAddPlayer={() => {
                addAsset(
                  "A",
                  {
                    asset_type: "PLAYER",
                    asset_id: aPlayer,
                  },
                );
                setAPlayer("");
              }}
              onAddPick={() => {
                addAsset(
                  "A",
                  {
                    asset_type: "PICK",
                    asset_id: aPick,
                  },
                );
                setAPick("");
              }}
              onRemove={(index) => {
                removeAsset(
                  "A",
                  index,
                );
              }}
            />

            <TradeSide
              title="Team B gets"
              teamLabel="Team B"
              selectedTeam={teamB}
              otherTeam={teamA}
              teams={state.teams}
              players={state.players}
              selectedPlayer={bPlayer}
              setSelectedPlayer={setBPlayer}
              selectedPick={bPick}
              setSelectedPick={setBPick}
              picks={picksForB}
              assets={bGets}
              playerLabel={playerLabel}
              assetLabel={assetLabel}
              onTeamChange={(value) => {
                setTeamB(value);
                setAGets([]);
                setBGets([]);
                setAPick("");
                setBPick("");
                setConfirmed(false);
              }}
              onAddPlayer={() => {
                addAsset(
                  "B",
                  {
                    asset_type: "PLAYER",
                    asset_id: bPlayer,
                  },
                );
                setBPlayer("");
              }}
              onAddPick={() => {
                addAsset(
                  "B",
                  {
                    asset_type: "PICK",
                    asset_id: bPick,
                  },
                );
                setBPick("");
              }}
              onRemove={(index) => {
                removeAsset(
                  "B",
                  index,
                );
              }}
            />
          </div>

          <div className={styles.tradeFinalize}>
            <label>
              <input
                type="checkbox"
                checked={confirmed}
                disabled={
                  busy
                  || !teamA
                  || !teamB
                  || teamA === teamB
                  || (
                    aGets.length === 0
                    && bGets.length === 0
                  )
                }
                onChange={(event) => {
                  setConfirmed(
                    event.target.checked,
                  );
                }}
              />

              Finalize trade and confirm the
              asset selections above
            </label>

            <button
              type="button"
              className={
                styles.writePrimaryButton
              }
              disabled={!canSubmit}
              onClick={() => {
                void submitTrade();
              }}
            >
              {busy
                ? "Submitting…"
                : "Submit Trade"}
            </button>
          </div>

          {message && (
            <div
              className={styles.writeMessage}
              aria-live="polite"
            >
              {message}
            </div>
          )}

          {receipt && (
            <div className={styles.tradeReceipt}>
              <span>
                Contract updates
                <strong>
                  {receipt.player_updates}
                </strong>
              </span>

              <span>
                Pick updates
                <strong>
                  {receipt.pick_updates}
                </strong>
              </span>

              <span>
                Keeper assignments
                <strong>
                  {receipt.keeper_assignments}
                </strong>
              </span>

              <span>
                Audit assets
                <strong>
                  {receipt.receipt_asset_count}
                </strong>
              </span>
            </div>
          )}
        </div>
      )}
    </>
  );
}


type TradeSideProps = {
  title: string;
  teamLabel: string;
  selectedTeam: string;
  otherTeam: string;
  teams: Team[];
  players: Player[];
  selectedPlayer: string;
  setSelectedPlayer: (
    value: string,
  ) => void;
  selectedPick: string;
  setSelectedPick: (
    value: string,
  ) => void;
  picks: Pick[];
  assets: Asset[];
  playerLabel: (
    player: Player,
  ) => string;
  assetLabel: (
    asset: Asset,
  ) => string;
  onTeamChange: (
    value: string,
  ) => void;
  onAddPlayer: () => void;
  onAddPick: () => void;
  onRemove: (
    index: number,
  ) => void;
};


function TradeSide(
  props: TradeSideProps,
) {
  const {
    title,
    teamLabel,
    selectedTeam,
    otherTeam,
    teams,
    players,
    selectedPlayer,
    setSelectedPlayer,
    selectedPick,
    setSelectedPick,
    picks,
    assets,
    playerLabel,
    assetLabel,
    onTeamChange,
    onAddPlayer,
    onAddPick,
    onRemove,
  } = props;

  return (
    <section className={styles.tradeSide}>
      <h4>
        {title}
      </h4>

      <label className={styles.tradeField}>
        <span>
          {teamLabel}
        </span>

        <select
          value={selectedTeam}
          onChange={(event) => {
            onTeamChange(
              event.target.value,
            );
          }}
        >
          <option value="">
            Select team
          </option>

          {teams.map((team) => (
            <option
              key={team.team_key}
              value={team.team_key}
            >
              {team.team_name}
            </option>
          ))}
        </select>
      </label>

      <div className={styles.tradeAssetAdd}>
        <label className={styles.tradeField}>
          <span>
            Add player
          </span>

          <select
            value={selectedPlayer}
            onChange={(event) => {
              setSelectedPlayer(
                event.target.value,
              );
            }}
          >
            <option value="">
              Select player
            </option>

            {players.map((player) => (
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
            ))}
          </select>
        </label>

        <button
          type="button"
          className={
            styles.writeSecondaryButton
          }
          disabled={
            !selectedTeam
            || !otherTeam
            || !selectedPlayer
          }
          onClick={onAddPlayer}
        >
          Add player
        </button>
      </div>

      <div className={styles.tradeAssetAdd}>
        <label className={styles.tradeField}>
          <span>
            Add draft pick
          </span>

          <select
            value={selectedPick}
            disabled={
              !selectedTeam
              || !otherTeam
            }
            onChange={(event) => {
              setSelectedPick(
                event.target.value,
              );
            }}
          >
            <option value="">
              {otherTeam
                ? "Select pick"
                : "Select both teams first"}
            </option>

            {picks.map((pick) => (
              <option
                key={pick.pick_id}
                value={pick.pick_id}
              >
                {pick.pick_id}
                {" — "}
                R{pick.round_number}.
                {pick.slot_number}
                {" — "}
                {pick.owner_team_name}
              </option>
            ))}
          </select>
        </label>

        <button
          type="button"
          className={
            styles.writeSecondaryButton
          }
          disabled={!selectedPick}
          onClick={onAddPick}
        >
          Add pick
        </button>
      </div>

      <div className={styles.tradeAssets}>
        {assets.length === 0 ? (
          <span className={styles.writeMuted}>
            No assets added.
          </span>
        ) : (
          assets.map(
            (asset, index) => (
              <div
                className={
                  styles.tradeAsset
                }
                key={
                  `${asset.asset_type}:`
                  + `${asset.asset_id}`
                }
              >
                <div>
                  <span>
                    {asset.asset_type}
                  </span>

                  <strong>
                    {assetLabel(asset)}
                  </strong>
                </div>

                <button
                  type="button"
                  onClick={() => {
                    onRemove(index);
                  }}
                  aria-label="Remove asset"
                >
                  ×
                </button>
              </div>
            ),
          )
        )}
      </div>
    </section>
  );
}
