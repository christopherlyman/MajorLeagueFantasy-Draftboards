# NFFL - Contract Lifecycle / History Canonical

**Purpose:**
This document defines the canonical truth model and season-end operating procedure for NFFL contracts.

If something conflicts:

> **DB truth -> container runtime -> application state -> documentation**

This is not a feature-history log. Re-verify live production truth before consequential writes.

---

# 0) Production Status

## 0.1 Current State [VERIFIED/OBSERVED]

As of August 16, 2026:

- Contract History / lifecycle implementation is complete
- migrations 020 through 027 are applied in production
- Commissioner lifecycle overrides are deployed
- historical 2021-2025 and modern 2026+ history are unified
- modern Franchise Tags are represented in Contract History
- Trade, Waiver, Dropped, No Contract, Expired and active contract states are supported
- active players display bright/bold; inactive historical players are dimmed
- managers can view Contract History read-only
- internal Commissioner notes remain hidden from managers
- Commissioner mutation and season-end controls remain Commissioner-only
- manager-side visual signoff is complete

Observed release baseline:

```text
6cc09b8 Expose contract history to managers
42b9974 Integrate commissioner contract history overrides
c0bb845 Dim inactive players in contract history
f16cdec Add NFFL contract history and lifecycle management
```

Re-verify current `main` before relying on those commit IDs later.

## 0.2 Outstanding Work [REQUIREMENT]

The real 2026 -> 2027 rollover is not unfinished development.

It is deferred operational execution of the implemented season-end workflow after the 2026 season ends.

---

# 1) Canonical Contract Model

## 1.1 Current Operational Truth [VERIFIED]

```sql
nffl.contract
```

This is the current ordinary-contract state:

- current owning team
- years remaining
- active / expired / void / needs_review status
- current operational source

Canonical rule:

```text
nffl.contract = current ordinary-contract truth
```

## 1.2 Immutable Modern Award Identity [VERIFIED]

```sql
nffl.contract_history_episode
```

This records the original 2026+ contract award episode.

A later carry-forward must not create another award episode.

Canonical rule:

```text
one new award = one immutable contract episode
```

## 1.3 Season-Specific Commissioner Corrections [VERIFIED]

```sql
nffl.contract_history_season_override
```

This is a sparse season correction/display layer.

Supported contract states include:

- CONTRACT
- NO_CONTRACT
- DROPPED
- EXPIRED
- NEEDS_REVIEW

Supported acquisition values:

- NONE
- TRADE
- WAIVER

Do not use this table to rewrite immutable award history.

## 1.4 Franchise Tag Is Separate [VERIFIED/REQUIREMENT]

Franchise Tag state remains in the FT architecture, including:

```sql
nffl.franchise_tag_history
```

Canonical rule:

```text
Franchise Tag != ordinary contract
```

Do not create a fake ordinary contract to represent an FT.

---

# 2) Historical Contract Model - 2021-2025

Historical spreadsheet-derived episode identity is stored in:

```sql
nffl.historical_contract_episode
```

Per-season historical cells are stored in:

```sql
nffl.historical_contract_season
```

Historical status vocabulary includes:

- CONTRACT
- FT
- NO_CONTRACT
- EXPIRED
- DROPPED

Historical acquisition vocabulary includes:

- NONE
- TRADE
- WAIVER

Historical tables are evidence.

Do not copy modern annual state back into the historical import.

---

# 3) Modern Contract Lifecycle - 2026+

## 3.1 New Awards [VERIFIED]

A newly awarded ordinary contract creates:

1. operational state in `nffl.contract`
2. immutable award identity in `nffl.contract_history_episode`

Original awards are 2, 3 or 4 years.

A 1-year value is valid later as years remaining, but is not a valid new original award.

## 3.2 Carry-Forward [VERIFIED/REQUIREMENT]

Later seasons remain attached to the original episode.

```text
carry-forward preserves origin episode identity
```

## 3.3 Dropped vs No Contract [VERIFIED]

**Dropped** means a release from an existing lifecycle.

**Inactive / No Contract** means no active ordinary contract without falsely asserting a drop event.

## 3.4 Needs Review [VERIFIED/REQUIREMENT]

`NEEDS_REVIEW` is a safety state for ambiguous transitions.

Do not bypass it by guessing.

---

# 4) Contract State vs Acquisition

Contract state and acquisition method are separate concepts.

Valid examples:

```text
Active Contract + Standard
Active Contract + Trade
Active Contract + Waiver Pickup
```

Trade and Waiver are acquisition events, not mutually exclusive contract states.

---

# 5) Commissioner Keeper Contract / Roster Overrides

Supported actions:

- Active Contract
- Inactive / No Contract
- Franchise Tag
- Dropped
- Clear Franchise Tag

For Active Contract, acquisition supports:

- Standard
- Trade
- Waiver Pickup

For a genuinely new reconstructed contract:

- original award must be 2, 3 or 4 years
- an immutable Commissioner-origin episode is created
- `source_pick_kind = COMMISSIONER`

FT and ordinary active-contract state must not coexist for the same current player state.

Commissioner overrides are for deliberate correction and unusual lifecycle events.

They are not a replacement for season-end reconciliation or roster evidence.

---

# 6) Contract History UI

The matrix combines:

- 2021-2025 imported history
- legacy-to-2026 boundary state
- modern 2026+ award episodes
- later rollover/reconciliation state
- modern Franchise Tags
- Commissioner season overrides

Approved visual states include:

- 4 / 3 / 2 / 1 years remaining
- Franchise Tag
- No Contract
- Trade
- Dropped
- Waiver Pickup
- Expired

Do not casually replace the approved cell colors.

Manager Contract History is read-only.

Internal Commissioner notes and mutation controls remain Commissioner-only.

---

# 7) Identity Rules

Player names are not authoritative identity.

Contracts belong to the franchise, not permanently to:

- a manager
- a display team name
- one season Yahoo team key

For general franchise continuity and season-team mapping, use:

```text
app/docs/2_Team_Franchise_Identity.md
```

The contract subsystem must not invent a competing permanent franchise identity.

---

# 8) Final Roster Snapshot Evidence

Season-end reconciliation depends on authoritative evidence of the prior season final roster.

A finalized roster snapshot is immutable evidence.

Canonical rule:

```text
validate first
finalize second
never silently rewrite finalized evidence
```

Before finalization verify:

- source roster count
- normalized player count
- unmapped player count
- duplicate conditions
- team/franchise mapping coverage
- source and target season direction

---

# 9) Season Reconciliation Engine

Preview function:

```sql
nffl.preview_contract_season_reconciliation(...)
```

Apply function:

```sql
nffl.apply_contract_season_reconciliation(...)
```

Always preview before apply.

Typical outcome classes:

- CARRY_FORWARD
- EXPIRE
- DROP
- NEEDS_REVIEW

Needs Review is intentional and must not be bypassed.

---

# 10) Contract Lifecycle Migrations

```text
020_nffl_historical_contracts.sql
021_seed_nffl_historical_contracts.sql
022_nffl_contract_lifecycle_audit.sql
023_nffl_roster_snapshot_immutability.sql
024_nffl_contract_source_snapshot_provenance.sql
025_nffl_contract_season_reconciliation_engine.sql
026_nffl_legacy_contract_episode_identity.sql
027_nffl_contract_history_season_overrides.sql
```

Migration 027 added `nffl.contract_history_season_override` and allowed `COMMISSIONER` episode provenance.

Do not assume a migration is applied merely because its file exists.

---

# 11) 2026 -> 2027 Outstanding Operational Work

This is the primary restart point after the 2026 season ends.

## Phase A - Verify Context [PENDING]

1. verify production `main` and clean repository state
2. verify production runtime/database health
3. inspect `nffl.v_active_season_context`
4. prove source season = 2026
5. prove target season = 2027
6. prove source and target league keys
7. verify required lifecycle schema/functions still exist

## Phase B - Establish 2027 Franchise / Team Identity [PENDING]

1. load/verify 2027 league teams
2. establish canonical 2027 franchise/team mapping
3. resolve owner/team changes
4. verify every franchise has one appropriate 2027 team identity

Use `2_Team_Franchise_Identity.md` as the owning identity canonical.

## Phase C - Capture 2026 Final Roster Evidence [PENDING]

Capture the final 2026 Yahoo roster snapshot for the 2027 rollover.

Direction must be:

```text
target season: 2027
source season: 2026
```

Before finalization inspect:

- source rows
- normalized rows
- unmapped rows
- duplicates
- team mapping
- player identity failures

## Phase D - Finalize Snapshot [PENDING]

Only after validation:

1. finalize the snapshot
2. record evidence hash/provenance
3. verify immutability protections

## Phase E - Preview Reconciliation [PENDING]

Review:

- same-franchise carry-forward
- final-year expiration
- absent-player drop
- different-team / ownership-change cases
- unmapped identity
- Needs Review

Preview counts must reconcile to the eligible 2026 contracts.

## Phase F - Apply Automatic Reconciliation [PENDING]

Only after preview signoff:

1. apply the reconciliation
2. capture result counts
3. verify repeat application does not duplicate work
4. verify expected 2027 operational contracts

## Phase G - Resolve Needs Review [PENDING]

Use the existing Commissioner workflow.

For each case inspect authoritative roster/team evidence and record the deliberate resolution.

Do not use ad hoc SQL unless a verified product gap requires it.

## Phase H - Verify 2027 Contract Truth [PENDING]

Verify:

- active-contract count
- team ownership
- years remaining
- expirations
- drops
- move/trade resolutions
- FT separation
- no duplicate player contracts
- no duplicate award episodes

Canonical rule:

```text
2027 carry-forward does not create a new award episode
```

## Phase I - UI Signoff [PENDING]

Verify Contract History shows correct 2027 continuity for Commissioner and manager views.

Manager view remains read-only.

Commissioner notes and season-end controls remain Commissioner-only.

---

# 12) Stop Conditions

Stop before mutation if any of these are true:

- wrong source or target season
- wrong league key
- incomplete 2027 franchise/team mapping
- unexpected roster count
- unexplained unmapped players
- snapshot unexpectedly already finalized
- reconciliation preview does not reconcile to eligible contracts
- duplicate active player contracts
- ambiguous ownership is being auto-resolved without evidence
- current production code/schema differs materially from this document

---

# 13) Operational Workflow Rules

- Git / repository / file-edit work uses native Windows PowerShell
- SSH to Apollo is used for NAS/runtime/database/dev operations when appropriate
- Docker may be used for actual runtime/Postgres operations
- never run Git through Docker
- prefer read-only inspection before mutation
- work one deterministic micro-step at a time
- do not casually restart/rebuild DraftBoard or Discord
- never use `git add .`
- stage exact files
- use direct `git commit -m` messages
- avoid zombie code and duplicate systems
- apply DB migrations before source deployment when source depends on new schema

---

# 14) New Chat Restart Rule

For end-of-season work, read these first:

```text
app/docs/0_CoreCanonicalGuide.md
app/docs/2_Team_Franchise_Identity.md
app/docs/3_Contract_Lifecycle_History.md
app/docs/7_Deployment_Infrastructure.md
```

Then verify live production truth before any write.

The first consequential operation must not occur until these are proven:

```text
2026 source context
2027 target context
franchise mapping
snapshot state
reconciliation tooling
```
