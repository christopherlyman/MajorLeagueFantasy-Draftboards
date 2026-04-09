````markdown
# MLF DraftBoard — Player Control Canonical (Contracts / Prospect Tags / QO)

**Purpose:**  
This document defines the canonical truth model for **player control rights** in the MLF DraftBoard system, specifically:

- Contracts
- Prospect Tags (PT)
- Qualifying Offers (QO)

It exists to help a new chat deterministically answer questions like:

- What is the authoritative source of contract / PT / QO truth?
- How do these domains affect DraftBoard ownership and placeholders?
- What is stored in DB vs projected into runtime state?
- What can be traded, overridden, refreshed, or rebuilt?
- What is canonical current truth versus display/runtime projection?

If something conflicts:

> **DB truth → container runtime → application state → documentation**

This document is **not** a history log.

---

# 0) Scope

This canonical governs:

- contract storage and effective contract truth
- prospect tag storage and behavior
- qualifying offer storage and placeholder projection
- player-control runtime caches
- keeper placeholder rules
- QO placeholder rules
- trade effects on player-control truth
- override semantics
- refresh / rebuild boundaries
- deterministic verification procedures

This canonical does **not** define:

- full draft-state restore internals
- full team/franchise identity architecture
- full auth / permissions design
- UI layout behavior
- deployment topology

It may reference those areas only where required to explain player-control truth.

---

# 1) Domain Overview

## 1.1 Player Control Rights Are Multi-Source [VERIFIED]

The DraftBoard does not derive all player-control rights from one table.

Canonical player-control domains are:

1. **Contracts**
2. **Prospect Tags (PT)**
3. **Qualifying Offers (QO)**

These are separate truth domains with different storage models and different runtime effects.

## 1.2 Shared Runtime Consequence [VERIFIED]

Although stored differently, Contracts and PT share one major DraftBoard consequence:

```text
both become keeper placeholders in standard rounds
````

QO is different:

```text
QO becomes predraft placeholder projection in rounds 1–5
```

Canonical rule:

```text
Contracts/PT and QO are related player-control systems, but not the same kind of truth.
```

---

# 2) Identity and Scope Rules

## 2.1 Player Identity Rule [VERIFIED]

All player-control logic must anchor on:

```text
yahoo_player_key
```

Names are never authoritative.

## 2.2 Scope Rule [VERIFIED]

All player-control logic must be scoped by:

```text
(league_key, season_year)
```

Unscoped reasoning is invalid.

## 2.3 Team Identity Rule [VERIFIED]

Current-season ownership/team association must use canonical current-season team identity, not legacy keyspaces or display labels.

---

# 3) Contracts — Canonical Truth Model

## 3.1 Contract SSOT [VERIFIED]

The authoritative raw contract store is:

```sql
public.contract
```

Canonical invariant:

```text
public.contract is the raw single source of truth for contract ownership facts
```

## 3.2 Contract Primary Key Shape [VERIFIED]

Canonical identity for a contract row is:

```text
(league_key, season_year, yahoo_player_key)
```

This means contract truth is scoped per player, league, and season.

## 3.3 Core Contract Meaning [VERIFIED]

A contract row expresses:

* scoped league and season
* current owning `team_key`
* player identity
* years remaining
* audit note / mutation metadata

## 3.4 Contract Ownership Truth [VERIFIED]

DraftBoard does not infer contract ownership from:

* UI overlays
* trade receipts
* display labels
* temporary runtime assumptions

Canonical rule:

```text
contract ownership comes from contract SSOT / effective contract resolution
```

---

# 4) Contract Overrides and Effective Contract Truth

## 4.1 Override Store [VERIFIED]

Overrides are written to:

```sql
public.contract_override
```

Purpose:

* correct contract state without direct ad hoc DB edits
* support commissioner override behavior
* support contract void operations

## 4.2 Effective Contract View [VERIFIED]

The effective contract truth surface is:

```sql
public.v_contracts_effective_current
```

Canonical rule:

```text
runtime contract loaders should reason from effective contract truth, not raw table rows alone, when effective behavior matters
```

## 4.3 Void Semantics [VERIFIED]

Voiding a contract does not delete the contract concept by erasing history through UI assumptions.

Effective void behavior is represented by:

```text
years_remaining = 0
```

Canonical result:

```text
voided player is removed from contracted runtime ownership and becomes available
```

## 4.4 Override Semantics [VERIFIED]

Override meaning:

* `years_remaining > 0` → replace / define effective contract state
* `years_remaining = 0` → void effective contract

---

# 5) Contract Runtime Cache

## 5.1 Runtime Contract Cache [VERIFIED]

Contract truth is refreshed into runtime/session state via:

```python
_refresh_contract_cache_into_session_state()
```

This populates:

```python
st.session_state["contracted_keys_2026"]
st.session_state["contract_rows_2026"]
```

## 5.2 Cache Is Not SSOT [VERIFIED]

Canonical rule:

```text
runtime/session contract cache is a performance cache, not source of truth
```

DB truth remains authoritative.

## 5.3 Refresh Boundary [VERIFIED]

After contract mutation, the canonical refresh path is:

```text
DB mutation
→ _refresh_contract_cache_into_session_state()
→ st.rerun()
```

Do not attempt to “fix” large-scale contract truth only by manipulating stale UI/session state.

---

# 6) Prospect Tags (PT) — Canonical Truth Model

## 6.1 PT Truth Store [VERIFIED]

Prospect Tag truth is stored in:

```sql
public.prospect_tag
```

PT is an authoritative player-control domain, not a UI-only feature.

## 6.2 PT DraftBoard Meaning [VERIFIED]

PT does not behave like a QO right.

PT behaves like a keeper-control right for DraftBoard placement.

Canonical rule:

```text
PT players become keeper placeholders in rounds 6–25, same as contracts
```

## 6.3 PT Ownership Meaning [VERIFIED]

A PT row establishes scoped control of a player for the applicable league/season/team context and participates in keeper placement logic.

## 6.4 PT and Contract Runtime Union [VERIFIED]

For practical eligibility / ownership logic, contracts and PT are often unioned into runtime “owned/controlled” sets.

This is why PT and contract logic frequently interact in:

* available-player filtering
* commissioner tools eligibility
* keeper placeholder rebuild logic

---

# 7) PT Eligibility / Selection Rules

## 7.1 PT Selector Policy [VERIFIED]

The commissioner PT selector excludes only players already controlled through the effective ownership union and players that are QO-eligible.

Canonical exclusion logic includes:

1. already owned/controlled via contracts + PT union
2. QO-eligible players

## 7.2 PT Eligibility Must Not Be Hard-Blocked by Heuristic Stats [VERIFIED]

AB/IP heuristics are not the canonical blocker for PT selection.

Commissioners retain control unless canonical exclusion rules apply.

## 7.3 PT Refresh Dependency [VERIFIED]

Because PT selection logic depends on runtime player cache and contract cache together, refresh boundaries matter.

Canonical rule:

```text
player refresh that affects PT eligibility must keep state.players and contract cache aligned
```

---

# 8) Contracts + PT Keeper Model

## 8.1 Shared Keeper Definition [VERIFIED]

Contracts and PT share the same DraftBoard keeper-placement behavior:

* both become keeper placeholders
* both are placed only in rounds 6–25
* neither belongs in QO rounds

## 8.2 Keeper Placeholder Invariant [VERIFIED]

Keeper placeholders must satisfy:

* `round_number >= 6`
* `selected_player_key != None`
* `selected_ts_iso == None`

If a timestamp exists, it is a real pick, not a keeper placeholder.

## 8.3 Prefill Algorithm [REQUIREMENT]

Canonical keeper prefill per team:

1. collect contract players + PT players
2. sort by `rank_value ASC` with `None` last
3. fill bottom-up from `R25 → R06`
4. skip already drafted/timestamped players

## 8.4 Idempotency Rule [VERIFIED/REQUIREMENT]

On every initialization path:

* fresh boot
* restore
* healed restore

the system must:

1. clear keeper placeholders in standard rounds
2. rebuild them deterministically

Canonical rule:

```text
keeper reconstruction must behave the same after restore as after fresh boot
```

## 8.5 Canonical Clearing Rule [VERIFIED]

Clearing targets:

* `round_number > 5`
* `selected_ts_iso is NULL`
* `selected_player_key is NOT NULL`

This clearing must not depend on whether a player is presently in a runtime ownership cache.

---

# 9) QO — Canonical Truth Model

## 9.1 Predraft QO Truth Source [VERIFIED]

Predraft QO truth originates from:

```sql
public.qualifying_offer
```

Canonical rule:

```text
predraft QO truth comes from public.qualifying_offer, not from draft picks
```

## 9.2 QO Is Not the Same as Pick Ownership [VERIFIED]

QO establishes predraft player-control / dibs mechanics, not the underlying existence of draft slots.

QO must not be conflated with:

* standard draft order
* pick-column identity
* traded-pick ownership baseline

## 9.3 QO Placeholder Pipeline [VERIFIED]

Canonical conceptual pipeline:

```text
public.qualifying_offer
→ _compute_current_qos_from_log()
→ _sync_qo_placeholders()
→ state.picks runtime placeholders
→ board render
```

This means QO display on the board is a runtime projection of authoritative predraft truth plus current ownership/state.

## 9.4 QO Placeholder Invariant [VERIFIED]

QO placeholders must satisfy:

* `round_number <= 5`
* `selected_player_key != None`
* `selected_ts_iso == None`

If a timestamp exists, it is a real pick, not a QO placeholder.

## 9.5 Poach Eligibility Rule [REQUIREMENT]

Conceptual poach eligibility depends on:

```text
submitter_level > current_round
```

This is business-rule intent that governs how QO rights remain actionable across rounds.

---

# 10) QO Canonical Team Key Rule

## 10.1 QO Storage Must Use Canonical Team Keys [VERIFIED]

For canonicalized current-state storage, QO rows must use canonical Yahoo-format current-season `team_key` values.

Canonical rule:

```text
QO storage must not retain TEAM_XX in canonical current-state areas
```

## 10.2 Legacy QO Key Migration Lesson [VERIFIED]

Legacy TEAM_XX values may have existed historically, but current canonical write/load paths must use current-season team keys.

## 10.3 Minimal Legacy Detector [VERIFIED]

Useful anti-drift detector:

```sql
select count(*) as legacy_qos
from public.qualifying_offer
where league_key='469.l.41640'
  and season_year=2026
  and team_key like 'TEAM_%';
```

Expected in canonicalized current state:

```text
0
```

---

# 11) QO Ownership and Traded Picks

## 11.1 QO-Round Picks Are Tradable Assets [VERIFIED/REQUIREMENT]

QO placeholders are special, but QO-round draft slots are still tradable assets.

Canonical rule:

```text
QO-round pick ownership may change without moving the slot column
```

## 11.2 QO Placeholder Ownership Must Follow Current Pick Owner [VERIFIED]

For traded QO-round picks, the placeholder/player assignment must follow:

```python
pick.owner_team_key
```

not forced slot-order ownership.

Canonical rule:

```text
QO placeholder content follows current pick owner
column identity still follows draft-order slot baseline
```

## 11.3 Why This Matters [VERIFIED]

If QO placeholder sync forces slot-order ownership instead of current pick ownership, traded QO-round behavior becomes incorrect.

This is a core anti-drift rule for QO reasoning.

---

# 12) Trade Effects on Player Control

## 12.1 Trade Receipts Are Audit, Not SSOT [VERIFIED]

Trade receipt tables record history. They do not directly define current player-control truth.

## 12.2 Contract Trade Effect [VERIFIED]

When a traded player has contract years remaining, canonical mutation updates contract ownership in SSOT.

Canonical effect:

```text
PLAYER trade with contract years > 0
→ update public.contract.team_key
→ refresh runtime contract cache
→ rerun UI
```

## 12.3 Non-Contract Player Trade Effect [VERIFIED]

If a traded player is not contract-controlled, the trade does not invent a contract row merely because a receipt exists.

## 12.4 PT / QO Must Be Reasoned Separately [REQUIREMENT]

A trade receipt does not automatically redefine PT or QO truth unless the canonical mutation path for those domains explicitly says so.

Always prove which player-control domain was actually mutated.

---

# 13) Refresh and Reload Boundaries

## 13.1 Do Not Fix Player-Control Truth Only in UI [REQUIREMENT]

Large-scale contract/QO/PT truth must be corrected at the source:

* DB truth
* canonical views
* canonical loaders

Not by hand-editing broad state in the Streamlit UI and hoping runtime cache aligns.

## 13.2 QO “Reload from DB” Boundary [VERIFIED]

The commissioner-tools QO “Reload from DB” control is only a rerun trigger.

Canonical rule:

```text
QO reload button is not an ingestion/import pipeline
```

The authoritative QO truth remains `public.qualifying_offer`.

## 13.3 Player Refresh Boundary [VERIFIED]

Refreshing the player universe reloads runtime player data and must also keep dependent caches aligned when eligibility depends on them.

This matters particularly for PT eligibility.

---

# 14) Availability / Ownership Display Rules

## 14.1 Available Players Must Not Treat Undrafted QOs as Team-Owned [VERIFIED]

Ownership display for available players should derive from:

* real picks
* keeper placeholders (contracts/PT)

Undrafted QO placeholders do not make a player appear owned in the same way as a drafted or keeper-controlled player.

## 14.2 Team Labels in UI Are Derived, Not SSOT [VERIFIED]

Ownership/team labels shown in commissioner tools or player lists are useful derived surfaces.

They must not replace the underlying canonical domain truth.

---

# 15) Deterministic Verification Procedure

## 15.1 Minimum Questions

When Contracts / PT / QO logic looks wrong, answer these first:

1. What is the scoped `league_key` and `season_year`?
2. Is player identity anchored on `yahoo_player_key`?
3. What do the canonical DB tables say?
4. If contracts are involved, is raw truth or effective truth the right surface?
5. Has runtime contract cache been refreshed after mutation?
6. Are keeper placeholders being rebuilt rather than blindly trusted?
7. Are QO placeholders derived from canonical QO truth and current pick ownership?
8. Is someone mistaking a receipt table or UI display for source of truth?

## 15.2 Deterministic Debug Order [VERIFIED]

When player-control behavior looks wrong:

1. DB truth
2. imported runtime file certainty
3. loader/runtime output
4. session/application state
5. UI rendering

Never reverse this order.

---

# 16) Verify Pack

## 16.1 Contract truth

```sql
select count(*) as contract_rows
from public.contract
where league_key='469.l.41640'
  and season_year=2026;
```

## 16.2 Contract override truth

```sql
select count(*) as contract_override_rows
from public.contract_override
where league_key='469.l.41640'
  and season_year=2026;
```

## 16.3 Effective contract surface

```sql
select *
from public.v_contracts_effective_current
where league_key='469.l.41640'
  and season_year=2026;
```

## 16.4 PT truth

```sql
select count(*) as prospect_tag_rows
from public.prospect_tag
where league_key='469.l.41640'
  and season_year=2026;
```

## 16.5 QO truth

```sql
select count(*) as qo_rows
from public.qualifying_offer
where league_key='469.l.41640'
  and season_year=2026;
```

## 16.6 QO legacy detector

```sql
select count(*) as legacy_qos
from public.qualifying_offer
where league_key='469.l.41640'
  and season_year=2026
  and team_key like 'TEAM_%';
```

Expected:

```text
0
```

## 16.7 Runtime compile check

```bash
docker exec -i mlf_draftboard bash -lc "python -m py_compile /app/app/src/draftboard/ui/components/commissioner_tools.py /app/app/src/draftboard/ui/app.py /app/app/src/draftboard/data/db_players.py"
```

## 16.8 Cache refresh truth

After any contract mutation, prove that:

* `_refresh_contract_cache_into_session_state()` ran
* runtime behavior survives rerun / refresh

---

# 17) Critical Invariants (Do Not Break)

* player identity always = `yahoo_player_key`
* all player-control logic must be scoped by `(league_key, season_year)`
* `public.contract` is the raw contract SSOT
* `public.contract_override` modifies effective contract behavior
* `public.v_contracts_effective_current` is the effective contract truth surface
* runtime contract cache is not source of truth
* PT is authoritative player-control truth stored in `public.prospect_tag`
* Contracts and PT both become keeper placeholders in rounds 6–25
* keeper placeholders must always be cleared then rebuilt on initialization/restore
* predraft QO truth comes from `public.qualifying_offer`
* QO placeholders in rounds 1–5 are runtime projections, not independent truth
* canonical QO storage must use current-season team keys, not `TEAM_XX`
* QO placeholder ownership for traded QO picks follows current pick owner
* trade receipts are audit/history, not direct player-control SSOT
* large-scale truth corrections must happen at DB/canonical-loader level, not only in UI

---

# 18) Document Intent

This document exists to help a new chat:

* reason correctly about contract / PT / QO truth
* separate storage truth from runtime projection
* avoid confusing receipts, caches, placeholders, and SSOT
* rebuild keeper/QO behavior deterministically
* debug player-control issues without UI-first drift

It intentionally does **not** try to fully document every subsystem that consumes player-control truth.
Those details should live in companion canonicals such as:

* Draft State / Initialization / Restore
* Team / Franchise Identity
* Pick Ownership / Pick Trades
* Auth / Permissions
* UI Architecture


```
# 19) Addendum — QO Display Projection, Owner Labels, and Timestamp Formatting

## 19.1 QOs Tab Owner Labels Use Canonical Owner Names for Display [VERIFIED]

When the QOs tab displays the owner for a team row, the human-readable label should come from:

```sql
public.yahoo_team_map.owner_name
```

joined/mapped by canonical current-season `team_key`.

Canonical rule:

```text
QOs tab owner labels are display projections from yahoo_team_map.owner_name
and do not redefine player-control truth
```

This is a UI projection rule only.  
It does **not** replace the canonical QO truth source.

---

## 19.2 QOs Tab Continues to Derive QO State from Predraft Truth Plus Replay [VERIFIED]

The QOs tab must continue to show **derived current QO state**, not raw draft-pick state.

Canonical derivation remains:

```text
public.qualifying_offer
→ predraft_levels
→ replay POACH events from pick_log
→ current_levels
→ display
```

Canonical rule:

```text
QOs tab display is derived from predraft QO truth plus replayed POACH history,
not from current board pick-slot contents
```

This preserves the distinction between:

* QO truth
* QO display
* board placeholder projection
* actual draft picks

---

## 19.3 QOs Tab Row Numbering Is Display Metadata Only [VERIFIED]

If the QOs tab shows a leading `#` column with values `1..16`, that numbering is display-only metadata.

It must not be interpreted as defining:

* canonical draft order truth
* team identity truth
* QO ownership truth

Canonical rule:

```text
QOs row numbering is display metadata only;
canonical row order still comes from state.draft_order_team_keys_by_slot
```

---

## 19.4 Implicit DataFrame Index Must Not Leak Into QOs Display [VERIFIED]

The QOs tab should not expose the implicit dataframe index as an unlabeled extra column.

Canonical rule:

```text
QOs tables may show an explicit # column,
but must not leak the implicit dataframe index
```

This is a display hygiene rule only.

---

## 19.5 User-Facing QO Timestamps Should Be Formatted for Readability [VERIFIED]

The QOs tab `Updated` field should be display-formatted for users and should not expose microseconds when they add no value.

Accepted display behavior:

* strip timezone suffix for display if not needed
* strip fractional seconds / microseconds
* preserve stored canonical timestamp truth underneath

Example accepted display format:

```text
2026-03-14T03:26:05
```

Canonical rule:

```text
QO display timestamps may be simplified for readability;
storage truth remains unchanged
```

---

## 19.6 QO Display Formatting Must Not Mutate QO Truth [VERIFIED]

Formatting owner names, row numbers, or timestamps in the QOs tab does not alter any canonical QO domain truth.

These UI changes must not be mistaken for mutation of:

* `public.qualifying_offer`
* current QO ownership semantics
* placeholder ownership rules
* POACH replay logic

Canonical rule:

```text
QOs tab formatting changes are display-only unless a canonical DB mutation path is explicitly executed
```

---

## 19.7 Undrafted QO Rights Still Do Not Count as Keeper Ownership [VERIFIED]

This chat reaffirmed the existing rule that undrafted QO rights should not be treated the same as contract/PT keeper ownership in general ownership display logic.

Canonical distinction:

* Contracts/PT → keeper placeholders in rounds 6–25
* QO → predraft rights projected in rounds 1–5
* undrafted QO player → not the same as a standard keeper-owned player

Canonical rule:

```text
QO rights remain a separate player-control domain and must not be collapsed into keeper ownership semantics
```

---

## 19.8 QOs Tab Full-Table Rendering Is Acceptable When Native Widgets Misfit [VERIFIED]

When Streamlit native table widgets introduce unwanted nested scrolling, hidden rows, or index leakage for the QOs tab, it is acceptable to render the prepared QOs display table through HTML output from a dataframe.

Accepted reasons:

* show all 16 rows fully
* avoid nested internal scroll behavior
* suppress implicit dataframe index leakage
* preserve whole-page vertical scroll behavior

Canonical rule:

```text
QOs tab may use prepared HTML table rendering for stable full-table display
when native Streamlit table widgets do not fit the required UX
```

This is a UI rendering choice, not a change to the player-control model.

---

## 19.9 Structural Reminder: QO Truth, Placeholder Projection, and Tab Display Are Different Layers [VERIFIED]

This chat reaffirmed an important separation:

1. **QO truth storage**  
   `public.qualifying_offer`

2. **Runtime QO placeholder projection**  
   `_compute_current_qos_from_log()` + `_sync_qo_placeholders()`

3. **QOs tab display projection**  
   owner/timestamp/row-number/table formatting for user-facing presentation

Canonical rule:

```text
QO truth storage, runtime placeholder projection, and tab display formatting
are separate layers and must not be conflated
```

---
