# Fantasy Portfolio — Shared Draft State / Initialization / Restore Canonical

**Purpose:**  
This document defines the canonical truth model for **DraftBoard state**, including initialization, restore precedence, healing behavior, draft-order derivation, placeholder rebuilding, and reset expectations across the fantasy portfolio.

It exists to help a new chat deterministically answer questions like:

- What is the authoritative draft state?
- What happens on boot or rerun?
- How does restore work?
- What must be rebuilt vs trusted?
- What is safe to reset?
- What must never be inferred from UI appearance alone?
- How do profile-driven rules affect initialization and draft-order derivation?

If something conflicts:

> **DB truth → container runtime → application state → documentation**

This document is **not** a history log.

---

# 0) Scope

This canonical governs:

- persisted DraftBoard state
- initialization flow
- restore precedence
- state healing
- draft-order derivation as a state concern
- placeholder rebuild rules
- reset expectations
- state verification procedure
- profile-driven initialization inputs that directly affect state reconstruction

This canonical does **not** define:

- full auth architecture
- deployment topology
- player ingestion internals
- commissioner workflow UX
- detailed contract / PT / QO business policy except where required to explain placeholder rebuild behavior
- full pick-trade policy semantics beyond what initialization/restore must preserve

---

# 1) Canonical Draft State Model

## 1.1 Persisted Draft State Is Authoritative [VERIFIED]

Draft state is not defined by the current browser session.

The authoritative persisted draft-state store is:

```sql
public.draftboard_state
```

In particular, persisted runtime truth is carried through:

```text
public.draftboard_state.state_json
```

and tracked by:

```text
public.draftboard_state.state_sha256
```

Canonical rule:

```text
live draft truth must be reasoned from persisted state,
not UI appearance alone
```

## 1.2 Application State Is Runtime Materialization [VERIFIED]

Runtime DraftBoard state is an in-memory materialization of persisted and loaded truth.

That runtime state is used for:

- picks grid
- teams
- draft order
- placeholders
- commissioner actions
- board rendering

But runtime state is not authoritative unless it is tied back to canonical persisted/DB truth.

## 1.3 UI Is Never Authoritative [VERIFIED]

The UI may display:

- stale state
- partially healed state
- pre-rerun state
- session-local state

Therefore:

```text
a visible board state is not proof of canonical truth
```

Always prove DB and runtime state first.

---

# 2) State Sources and Precedence

## 2.1 Restore Precedence [VERIFIED]

Restore precedence is:

1. `public.draftboard_state`
2. disk autosave JSON

Canonical rule:

```text
deleting disk autosave alone does not reset the DraftBoard
if DB persisted state still exists
```

## 2.2 Disk Autosave Is Secondary [VERIFIED]

Disk autosave may exist as a persistence layer, but it is secondary to the DB-backed persisted state.

It must not be assumed to be the sole restore source.

## 2.3 State Reset Must Respect Restore Precedence [REQUIREMENT]

Any reset or cleanup procedure must prove which persisted source is actually controlling restore.

Do not assume a state reset succeeded unless authoritative persisted state has been verified.

---

# 3) Initialization Law

## 3.1 Initialization Must Reconstruct Canonical Runtime State [VERIFIED/REQUIREMENT]

Initialization is not allowed to blindly trust all persisted shapes.

It must reconstruct runtime state in canonical form.

Canonical responsibilities during initialization include:

- load persisted/autosaved state
- rebuild canonical teams from SSOT
- normalize ownership keys
- validate/rebuild picks grid if needed
- replay selections
- recompute draft order
- clear and rebuild keeper placeholders
- sync QO placeholders
- save healed autosave/state

## 3.2 Initialization Boundary Is the Canonical Normalization Point [VERIFIED/REQUIREMENT]

Legacy keyspaces and stale persisted shapes may exist.

Canonical rule:

```text
normalization belongs at the initialization boundary
```

Not in:

- board rendering
- UI tabs
- ad hoc display helpers
- scattered patch logic

If state looks wrong in the board:

```text
STOP → inspect initialization / normalization boundary first
```

## 3.3 Streamlit Import-Time State Mutation Is Unsafe [VERIFIED]

Because Streamlit reruns re-import modules:

- stateful operations must not happen at import time
- initialization must occur inside runtime execution paths

Primary runtime root:

```python
render_app()
```

## 3.4 Initialization Is State-Owned, Not UI-Owned [VERIFIED/REQUIREMENT]

Initialization and restore remain state-owned responsibilities.

Canonical rule:

```text
initialization and restore belong to state-owned runtime seams,
not to UI-owned ad hoc behavior
```

---

# 4) League Profile as a State Input

## 4.1 Canonical League Profile Is Now an Initialization Input [VERIFIED]

Initialization is no longer allowed to assume that league behavior is fully defined by hard-coded MLF constants.

A canonical read-only runtime seam now exists:

```text
draftboard.state.league_profile
```

This seam owns:

- DB-backed active league profile load
- YAML parse
- profile validation
- active-profile accessors for runtime use

Canonical rule:

```text
initialization may depend on canonical league profile values,
but profile loading/validation must not be scattered across UI or ad hoc helpers
```

## 4.2 Profile-Driven Initialization Facts [VERIFIED]

Initialization now has access to canonical profile-driven facts such as:

- active `draft.order_mode`
- active `first_standard_round`

Canonical rule:

```text
initialization/state logic must consume profile-derived facts
through the profile seam,
not by re-encoding league assumptions locally
```

## 4.3 Profile Truth and Operational Ingest Truth Are Different [VERIFIED/REQUIREMENT]

A league profile may exist before that league’s operational ingest truth is fully proven in league-owned runtime tables/state inputs.

Canonical rule:

```text
profile truth and operational ingest truth are distinct
```

Initialization must not conflate:

- approved league/season config
- fully ingested operational league data

## 4.4 Unsupported Profile Options Must Stop Before Unsafe Initialization [REQUIREMENT]

Known-but-unsupported profile options should be recognized and rejected cleanly before unsafe runtime behavior proceeds.

Canonical rule:

```text
unsupported profile options should fail validation
before initialization relies on them
```

---

# 5) Draft State Healing Model

## 5.1 Healing Is Canonical, Not Optional [VERIFIED/REQUIREMENT]

Persisted autosave/state may contain obsolete or partially canonical data.

Initialization must heal such state before relying on it.

Canonical healing flow:

```text
load autosave/state
→ rebuild canonical teams from SSOT
→ normalize ownership keys
→ rebuild picks grid if invalid owners exist
→ replay selections
→ recompute draft order
→ clear + reapply keeper prefill
→ sync QO placeholders
→ save autosave/state
```

## 5.2 Autosave Must Not Remain Partially Canonical [VERIFIED/REQUIREMENT]

Canonical rule:

```text
autosave/state must not remain half-healed
```

If healing logic runs, the resulting state should be saved back in canonical form.

## 5.3 Healing Must Preserve Real Draft Facts [REQUIREMENT]

Healing is for repairing stale representation, not erasing legitimate draft facts.

It must preserve:

- real selections
- valid ownership
- canonical season team mapping
- persisted draft-order truth where applicable
- valid traded-pick ownership
- legitimate persisted state mutations

---

# 6) Team and Pick State Concepts

## 6.1 Distinct Concepts Must Not Be Confused [VERIFIED]

The following are different concepts and must stay separate:

- season team identity
- draft column / slot identity
- original pick owner
- current pick owner
- placeholder ownership
- real selection ownership

A large share of drift comes from collapsing these into one idea.

## 6.2 Draft Columns Represent Slot Identity [VERIFIED]

Draft columns represent slot identity, not necessarily current ownership of every pick in that column.

Canonical draft-column baseline is derived from:

```python
state.draft_order_team_keys_by_slot
```

## 6.3 Pick Ownership Is Separate From Column Identity [VERIFIED]

A pick may belong to a different team than the column baseline.

Canonical current pick ownership is carried in:

```python
state.picks[pick_id].owner_team_key
```

Canonical rule:

```text
column identity and pick ownership are not the same thing
```

## 6.4 Original Team Key Must Remain Stable for Slot Identity [VERIFIED/REQUIREMENT]

For traded picks and restore behavior:

- `original_team_key` / slot baseline define slot identity
- `owner_team_key` defines current ownership

Canonical rule:

```text
pick trade = ownership reassignment
not column reassignment
```

Initialization/reset/healing must preserve this distinction.

---

# 7) Draft Order Canonical Rules

## 7.1 Draft Order Must Be Derived From the First Standard Round [VERIFIED]

Draft order must be derived from the **first standard round**, not from QO rounds and not from a hard-coded MLF-specific round number.

Canonical derivation concept:

```text
first_standard_round
```

Canonical rule:

```text
draft-order derivation must use first_standard_round,
not a hard-coded Round-6 assumption
```

## 7.2 Canonical Runtime Surface for Draft Order [VERIFIED]

The runtime surface for slot mapping is:

```python
state.draft_order_team_keys_by_slot
```

This must be validated against the active league/season context rather than assumed from board appearance.

## 7.3 Draft Order Mode Is a Canonical League Profile Fact [VERIFIED]

A second canonical runtime concept now exists:

```text
draft.order_mode
```

Canonical rule:

```text
straight vs snake is league-profile truth,
not UI-only behavior and not a league-name assumption
```

## 7.4 Current Implementation Status of Draft Order Mode [VERIFIED/REQUIREMENT]

At this stage:

- `first_standard_round` is profile-driven
- active `draft.order_mode` is runtime-loadable from the canonical league profile
- full snake slot-order behavior is **not yet complete**

Canonical rule:

```text
do not document snake support as complete
until slot-order derivation and dependent behavior fully consume order_mode
```

## 7.5 Draft Order Must Not Be Inferred From Visual Placeholder Layout [REQUIREMENT]

QO placeholders and other board visuals can mislead.

Canonical rule:

```text
use canonical draft-order derivation/state,
not board appearance,
to reason about slot order
```

---

# 8) Placeholder Model

## 8.1 Placeholder Types Must Be Distinguished [VERIFIED]

There are multiple placeholder categories with different meanings:

- QO placeholders
- keeper placeholders
- real drafted picks

These must not be conflated.

## 8.2 QO Placeholders [VERIFIED]

QO placeholders are visual/runtime projections of predraft QO truth.

Canonical traits:

- predraft rounds only
- `selected_player_key != None`
- `selected_ts_iso == None`

If a timestamp exists, it is a real pick, not a placeholder.

## 8.3 Keeper Placeholders [VERIFIED]

Keeper-style placeholders behave as runtime board placement constructs in standard rounds.

Canonical traits:

- standard rounds only
- `selected_player_key != None`
- `selected_ts_iso == None`

League-specific keeper/control-rights rules belong in the owning domain canonical or league overlay.

## 8.4 Placeholder Semantics [VERIFIED]

Placeholders are runtime board/state constructs.

They are not equivalent to:

- real drafted selections
- standalone truth sources
- independent ownership truth

They must always be rebuildable from authoritative domain truth plus initialization logic.

---

# 9) Keeper / Predraft Rebuild Law

## 9.1 Predraft Placeholder Classes Must Be Cleared Then Rebuilt [VERIFIED/REQUIREMENT]

On every initialization path, whether:

- fresh boot
- restore
- healed restore

the system must clear applicable predraft placeholder classes and rebuild them deterministically from authoritative truth.

Canonical rule:

```text
restore must behave like fresh boot
for deterministic predraft placeholder reconstruction
```

## 9.2 Clearing Rule Must Follow Placeholder Semantics [VERIFIED/REQUIREMENT]

Clearing must target predraft placeholder rows, not timestamped real picks.

Canonical rule:

```text
placeholder clearing must be driven by canonical placeholder semantics,
not by ad hoc cache presence
```

## 9.3 Rebuild Ordering Must Be Deterministic [REQUIREMENT]

Prefill/rebuild ordering must be deterministic and must not silently vary by UI order, incidental iteration order, or stale runtime cache order.

---

# 10) QO Sync and State Rules

## 10.1 Predraft QO Truth Is External to Draft Picks [VERIFIED]

Predraft QO truth originates from its authoritative DB domain.

It is not derived from draft picks.

## 10.2 Runtime Projection Pipeline [VERIFIED]

Canonical conceptual pipeline:

```text
DB predraft truth
→ compute current state from log/state
→ sync placeholders
→ state.picks visual/runtime projection
→ board render
```

## 10.3 Placeholder Ownership Must Follow Current Pick Ownership [VERIFIED]

For traded predraft picks, placeholder ownership must follow:

```python
pick.owner_team_key
```

not forced slot-order ownership.

Canonical rule:

```text
placeholder content follows current pick owner.
column identity still follows draft-order slot baseline.
```

---

# 11) Pick Trade State Rules

## 11.1 Trade Receipts Are Audit Only [VERIFIED]

Trade receipt tables are history, not live pick ownership truth.

Canonical current pick ownership lives in persisted DraftBoard state, not receipt rows.

## 11.2 Canonical Pick Ownership Field [VERIFIED]

Current pick ownership is:

```python
state.picks[pick_id].owner_team_key
```

persisted through the DraftBoard state save path.

## 11.3 State Save Path Matters [VERIFIED]

Canonical ownership changes must be persisted through the DraftBoard state save path.

If trade receipts change but canonical DraftBoard state does not change, then live ownership truth did not persist.

## 11.4 Fast Detector for Ownership Persistence [VERIFIED]

Preferred fast persistence detector:

```sql
public.draftboard_state.state_sha256
```

Interpretation:

- receipt rows changed but `state_sha256` unchanged → state mutation did not persist
- `state_sha256` changed and behavior survives refresh → canonical state mutation succeeded

---

# 12) Reset Expectations

## 12.1 Reset Must Be Defined Against Canonical State [REQUIREMENT]

A reset operation is not “successful” because the board visually looks reset once.

It must be validated against:

- persisted state source
- restored runtime state
- post-rerun behavior
- browser refresh behavior

## 12.2 Danger-Zone Style Resets Must Not Blindly Wipe Canonical Structures [REQUIREMENT]

Any destructive/reset operation must be proven against canonical expectations before use.

At minimum, a reset investigation should explicitly verify whether it preserves or wipes:

- draft order
- pick ownership
- traded picks
- QO truth/projection
- keeper placement sources
- league-specific control-rights sources

## 12.3 Post-Reset Verification Is Mandatory [REQUIREMENT]

After any reset:

1. verify persisted state source
2. verify runtime state after restore
3. verify board after refresh
4. verify key invariants did not silently drift

---

# 13) Verification Procedure

## 13.1 Deterministic Debug Order [VERIFIED]

When draft state looks wrong:

1. DB truth
2. imported runtime file certainty
3. loader/runtime output
4. application state after initialization
5. UI rendering

Never reverse this order.

## 13.2 Minimum Questions to Ask

When diagnosing draft-state issues, answer these first:

1. What persisted state source is controlling restore?
2. Did initialization normalize/heal state?
3. Is draft order canonical for the active league profile?
4. Are pick owners canonical?
5. Are placeholders correctly rebuilt rather than blindly trusted?
6. Does behavior survive rerun/refresh?

## 13.3 Import Certainty Check

If source edits seem ignored:

```bash
docker exec -i mlf_draftboard bash -lc 'python - << "PY"
import draftboard.ui.app as m
print(m.__file__)
PY'
```

Use equivalent checks for the specific module under investigation.

---

# 14) Verify Pack

## 14.1 Container sanity

```bash
docker exec -i mlf_postgres psql -U mlf -d mlf -c "select 1;"
docker exec -i mlf_draftboard bash -lc "python -V"
```

Adjust names to the live runtime under investigation as needed.

## 14.2 Compile check

```bash
docker exec -i mlf_draftboard bash -lc "python -m py_compile /app/app/src/draftboard/ui/app.py"
```

## 14.3 Persisted state existence

```sql
select count(*) as draftboard_state_rows,
       max(updated_at_utc) as max_updated_at_utc,
       max(state_sha256) as state_sha256
from public.draftboard_state;
```

## 14.4 Draft order surface

Prove the live runtime value of:

```python
state.draft_order_team_keys_by_slot
```

and validate expected consistency against the active league/season profile.

## 14.5 Pick ownership surface

Inspect whether:

```python
state.picks[pick_id].owner_team_key
```

matches expected canonical ownership for tested picks.

## 14.6 Placeholder sanity

Verify:

- predraft placeholders have `selected_ts_iso == None`
- real picks have timestamps
- placeholder ownership follows current pick ownership where required
- placeholder rows are rebuildable from authoritative truth

## 14.7 Refresh-proof behavior

After any state mutation or reset test:

- rerun app
- refresh browser
- confirm behavior persists

---

# 15) Critical Invariants (Do Not Break)

- persisted DraftBoard state outranks UI appearance
- restore precedence is DB state before disk autosave
- initialization is the canonical normalization/healing boundary
- autosave/state must not remain partially canonical after healing
- draft order derives from the first standard round, not from QO rounds and not from hard-coded MLF Round-6 assumptions
- `draft.order_mode` is profile truth even where full downstream behavior is not yet complete
- column identity is separate from current pick ownership
- pick trade changes ownership, not column identity
- `owner_team_key` and `original_team_key` must not be conflated
- predraft placeholders are rebuildable projections, not independent truth
- predraft placeholder rebuild must be deterministic on initialization/restore
- placeholder ownership for traded predraft picks follows current pick owner
- trade receipts are audit/history, not live DraftBoard ownership truth
- reset success must be proven against persisted state and refresh-proof behavior
- debug in order: DB → runtime → state → UI

---

# 16) Document Intent

This document exists to help a new chat:

- prove authoritative draft-state truth
- reason correctly about initialization and restore
- avoid confusing slot identity with ownership
- avoid trusting visual board state too early
- debug reset/healing behavior deterministically
- keep profile-driven state inputs separate from league-specific business-rule detail

It intentionally does **not** try to document every feature that touches the draft board.

Those details should live in companion canonicals or league overlays such as:

- Team / Franchise Identity
- Pick Ownership / Pick Trades / Draft Order
- Auth / Permissions
- UI Architecture
- league-specific overlays for MLF or MiLF differences