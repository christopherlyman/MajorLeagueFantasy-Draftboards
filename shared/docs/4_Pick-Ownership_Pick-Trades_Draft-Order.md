# Fantasy Portfolio — Pick Ownership / Pick Trades / Draft Order Canonical

**Purpose:**  
This document defines the canonical truth model for:

- Draft order
- Pick slot identity
- Current pick ownership
- Pick trades
- Traded-pick rendering semantics
- Persistence and verification of pick ownership changes

It exists to help a new chat deterministically answer questions like:

- What is the difference between draft order and pick ownership?
- What defines a pick’s column identity?
- What changes when a pick is traded?
- What is the authoritative source of current pick ownership?
- How do traded predraft picks behave?
- How should pick-trade persistence be verified?
- How do league-profile facts affect draft-order derivation?

If something conflicts:

> **DB truth → container runtime → application state → documentation**

This document is **not** a history log.

---

# 0) Scope

This canonical governs:

- draft-order truth
- slot identity
- current pick ownership
- traded-pick semantics
- persisted ownership state
- pick-trade mutation rules
- predraft traded-pick ownership behavior
- profile-driven draft-order inputs
- deterministic verification procedures

This canonical does **not** define:

- full DraftBoard restore/healing internals
- full team/franchise identity design
- full contract / PT / QO business policy
- UI layout styling beyond ownership/render semantics
- auth / permissions architecture
- deployment topology

It may reference those only where required to explain pick ownership truth.

---

# 1) Domain Overview

## 1.1 Draft Order and Pick Ownership Are Different Concepts [VERIFIED]

A major anti-drift rule in this system is:

```text
draft order ≠ current pick ownership
```

These must not be conflated.

Canonical distinctions:

- **Draft order** defines slot/column baseline identity
- **Current pick ownership** defines who currently controls a given pick
- **Pick trade** changes ownership, not slot identity

## 1.2 Column Identity and Pick Identity Are Related but Distinct [VERIFIED]

A pick occupies a slot in the board.

That slot has a baseline owner/column identity.

But the current owner of that pick may differ if the pick was traded.

Canonical rule:

```text
slot baseline identity and current pick owner are separate truths
```

---

# 2) Canonical Draft Order Truth

## 2.1 Draft Order Must Be Derived From the First Standard Round [VERIFIED]

Draft order must be derived from the **first standard round**, not from predraft rounds and not from a hard-coded league-specific round number.

Canonical draft-order input:

```text
first_standard_round
```

Canonical rule:

```text
slot baseline derivation must use first_standard_round,
not a hard-coded Round-6 assumption
```

## 2.2 Draft Order Mode Is a Canonical League Profile Fact [VERIFIED]

A second canonical runtime concept exists:

```text
draft.order_mode
```

Canonical rule:

```text
straight vs snake is league-profile truth,
not UI-only behavior and not a league-name assumption
```

## 2.3 Current Implementation Status of Draft Order Mode [VERIFIED/REQUIREMENT]

At this stage:

- `first_standard_round` is profile-driven
- active `draft.order_mode` is runtime-loadable from the canonical league profile
- full snake slot-order behavior is **not yet complete**

Canonical rule:

```text
do not document snake support as complete
until slot-order derivation and dependent behavior fully consume order_mode
```

## 2.4 Canonical Runtime Draft Order Surface [VERIFIED]

The authoritative runtime surface for slot order is:

```python
state.draft_order_team_keys_by_slot
```

Canonical rule:

```text
draft_order_team_keys_by_slot is the slot baseline truth for board columns
```

## 2.5 Draft Order Must Not Be Inferred From Board Appearance [REQUIREMENT]

Visual board appearance can be misleading because:

- placeholders may exist
- picks may be traded
- predraft rounds may have special content behavior
- runtime state may be stale or partially healed

Canonical rule:

```text
do not infer canonical draft order from board appearance alone
```

---

# 3) Slot Identity Model

## 3.1 Slot Identity Is Stable Within the Season [VERIFIED]

Board columns represent the seasonal slot baseline.

This identity is not supposed to move merely because a pick is traded.

Canonical rule:

```text
board columns represent slot identity,
not live ownership of every pick in that column
```

## 3.2 Original Team Key Defines Slot Baseline for the Pick [VERIFIED/REQUIREMENT]

Each pick carries baseline slot identity through the concept of original ownership.

Important canonical distinction:

- `original_team_key` = baseline slot identity for the pick
- `owner_team_key` = current owner of the pick

## 3.3 Slot Identity Must Not Be Mutated During Pick Trades [VERIFIED/REQUIREMENT]

When a pick is traded, the system must not mutate the structural slot baseline.

Canonical rule:

```text
pick trades do not move columns
pick trades do not redefine slot identity
```

---

# 4) Current Pick Ownership Truth

## 4.1 Canonical Current Owner Field [VERIFIED]

Current pick ownership is represented by:

```python
state.picks[pick_id].owner_team_key
```

Canonical rule:

```text
owner_team_key is the current ownership truth for a pick
```

## 4.2 Current Pick Ownership Is Not Defined by Trade Receipts [VERIFIED]

Trade receipts are audit/history only.

They do not themselves define current live ownership of picks.

Canonical rule:

```text
trade receipt rows ≠ live current pick ownership truth
```

## 4.3 Current Pick Ownership Is Persisted in DraftBoard State [VERIFIED]

Current pick ownership is persisted through DraftBoard state, not via a separate pick-ownership SSOT table.

Canonical persisted truth path:

```text
public.draftboard_state.state_json
```

with persistence tracked by:

```text
public.draftboard_state.state_sha256
```

Canonical rule:

```text
current pick ownership SSOT = persisted DraftBoard state
```

---

# 5) Pick Trades — Canonical Mutation Model

## 5.1 Pick Trade Changes Ownership Only [VERIFIED]

Canonical pick-trade effect:

```text
update pick.owner_team_key
preserve pick.original_team_key
preserve draft_order_team_keys_by_slot
```

This is the core anti-drift rule for pick trades.

## 5.2 What a Pick Trade Must Not Change [VERIFIED/REQUIREMENT]

A pick trade must not mutate:

- `pick.original_team_key`
- `state.draft_order_team_keys_by_slot`
- board column order
- seasonal slot baseline

Canonical rule:

```text
pick trade = ownership reassignment only
not column reassignment
```

## 5.3 Canonical Pick Trade Write Path [VERIFIED]

Canonical conceptual write flow:

```text
build trade receipt rows
→ insert public.trade
→ insert public.trade_asset
→ mutate state.picks[pick_id].owner_team_key
→ save_autosave(state)
→ rerun UI
```

This means pick-trade truth becomes canonical only when the DraftBoard state mutation is persisted.

## 5.4 Receipt History and Ownership Truth Must Stay Separate [VERIFIED]

Trade receipts matter for audit/history.

DraftBoard persisted state matters for current truth.

Canonical rule:

```text
receipt history and live pick ownership are different domains
```

## 5.5 Pick Trade Allowance Is a League Profile Fact [VERIFIED/REQUIREMENT]

A canonical profile fact exists:

```text
draft.pick_trades_allowed
```

Canonical rule:

```text
pick-trade allowance is league-profile truth,
not platform-UI capability truth
```

For offline-draft leagues, canonical pick-trade support belongs to DraftBoard policy/profile truth, not Yahoo UI capability.

---

# 6) Persisted State Model for Pick Ownership

## 6.1 Persisted DraftBoard State Carries Pick Ownership [VERIFIED]

Persisted pick ownership lives in DraftBoard state serialization.

Relevant conceptual path:

```text
state.picks[*].owner_team_key
→ save_autosave(state)
→ public.draftboard_state.state_json
→ restore path rebuilds PickSlot objects
```

## 6.2 Restore Must Preserve Traded Pick Ownership [VERIFIED]

After a pick trade has been persisted correctly, traded ownership must survive:

- rerun
- browser refresh
- restore/load path

Canonical rule:

```text
if traded ownership disappears after refresh,
canonical persistence failed or healing logic overwrote it
```

## 6.3 Fast Persistence Detector [VERIFIED]

Preferred fast detector for persisted DraftBoard ownership mutation:

```sql
public.draftboard_state.state_sha256
```

Interpretation:

- trade receipts changed but `state_sha256` unchanged → ownership mutation did not persist
- `state_sha256` changed and behavior survives refresh → canonical persistence likely succeeded

---

# 7) Traded-Pick Rendering Rules

## 7.1 Traded Pick Detection Rule [VERIFIED]

A pick is treated as traded when:

```text
current owner != slot baseline owner
```

Conceptually, traded-pick rendering compares:

- current owner from `pick.owner_team_key`
- baseline slot owner from `draft_order_team_keys_by_slot[slot - 1]`

## 7.2 Rendering Must Preserve Original Column [VERIFIED]

When a pick is traded:

- the pick remains in its original slot/column
- the current owner is shown as ownership metadata
- the column itself does not move

Canonical rule:

```text
traded pick stays in original column and shows changed owner
```

## 7.3 Rendering Must Not Reassign Slot Identity [REQUIREMENT]

Board rendering logic must not quietly convert traded ownership into column movement.

If the visual model moves the column itself, the board is misrepresenting canonical truth.

---

# 8) Predraft-Round Pick Ownership Rules

## 8.1 Predraft Slots Are Still Tradable [VERIFIED/REQUIREMENT]

A critical rule of this system is:

```text
predraft placeholders may be special
predraft draft slots are not exempt from being tradable assets
```

That means a predraft pick can be traded like any other pick if league policy allows it.

## 8.2 Predraft Pick Trade Does Not Move the Column [VERIFIED]

Trading a predraft pick changes ownership of the slot, not its baseline column identity.

Canonical rule:

```text
predraft pick trade = ownership change only
not column movement
```

## 8.3 Predraft Placeholder Ownership Must Follow Current Pick Owner [VERIFIED]

For traded predraft picks, placeholder content must follow:

```python
pick.owner_team_key
```

not forced slot-order ownership.

Canonical rule:

```text
predraft placeholder content follows current pick owner
column identity still follows slot baseline
```

## 8.4 Why This Rule Matters [VERIFIED]

If placeholder sync logic re-derives owner from slot baseline instead of current pick ownership, traded predraft behavior becomes wrong.

This is one of the most important anti-drift rules in this domain.

---

# 9) Relationship to Team Identity

## 9.1 Pick Ownership Must Use Canonical Current-Season Team Keys [VERIFIED]

Pick ownership should be expressed using canonical current-season team identity, not legacy keyspaces or display labels.

Canonical rule:

```text
pick ownership uses canonical current-season team_key surfaces
```

## 9.2 Slot Baseline Is Not a Substitute for Team Identity Modeling [REQUIREMENT]

Do not confuse:

- current team identity
- franchise continuity
- slot baseline identity
- display label

This canonical uses team keys for current-season ownership surfaces, but full identity truth belongs in the Team / Franchise Identity Canonical.

---

# 10) Relationship to Draft State / Restore

## 10.1 Draft State Restore Must Respect Pick Ownership [VERIFIED/REQUIREMENT]

When DraftBoard state is restored or healed, pick ownership must remain aligned with persisted truth.

Restore/healing logic must not accidentally overwrite valid traded ownership merely because it differs from the slot baseline.

## 10.2 Reset Behavior Must Be Explicitly Proven [VERIFIED/REQUIREMENT]

Reset behavior must explicitly verify whether traded ownership is preserved.

Canonical implication:

```text
reset investigations must explicitly verify
whether traded ownership is preserved
```

Never assume reset behavior from UI appearance alone.

---

# 11) Rollback Rules

## 11.1 Canonical Rollback Order [VERIFIED]

To make a test pick trade appear as though it never happened, rollback must occur in this order:

1. restore canonical DraftBoard pick ownership in persisted state
2. then remove audit receipts if desired

Conceptual rollback path:

```text
pick.owner_team_key = pick.original_team_key
→ save_autosave(state)
→ delete corresponding trade receipts
```

## 11.2 Why Order Matters [VERIFIED]

If receipts are removed before canonical persisted ownership is restored, current board truth may remain inconsistent with intended rollback state.

Canonical rule:

```text
restore current ownership truth first
clean up audit history second
```

---

# 12) Deterministic Verification Procedure

## 12.1 Minimum Questions

When pick ownership or draft order looks wrong, answer these first:

1. What is the active league profile?
2. What is the active `draft.order_mode`?
3. What is the active `first_standard_round`?
4. What is the canonical draft order surface?
5. Is the slot baseline correct?
6. What is the current `owner_team_key` for the affected pick?
7. Was a pick trade persisted into DraftBoard state or only logged as a receipt?
8. Did `state_sha256` change after the mutation?
9. Does the behavior survive rerun / browser refresh?
10. For predraft rounds, is placeholder ownership following current pick owner rather than slot baseline?

## 12.2 Deterministic Debug Order [VERIFIED]

When pick ownership behavior looks wrong:

1. DB/profile truth
2. imported runtime file certainty
3. DraftBoard state/runtime output
4. application state after restore/healing
5. UI rendering

Never reverse this order.

---

# 13) Verify Pack

## 13.1 Persisted DraftBoard state proof

```sql
select
  count(*) as draftboard_state_rows,
  max(updated_at_utc) as max_updated_at_utc,
  max(state_sha256) as state_sha256
from public.draftboard_state;
```

## 13.2 Draft order runtime proof

Prove the live runtime value of:

```python
state.draft_order_team_keys_by_slot
```

Validate expected slot consistency against the active profile.

## 13.3 Pick ownership runtime proof

Inspect the live runtime value of:

```python
state.picks[pick_id].owner_team_key
```

for the tested picks.

## 13.4 Import certainty proof

If behavior contradicts source edits, verify imported module path before debugging further.

Example pattern:

```bash
docker exec -i mlf_draftboard bash -lc 'python - << "PY"
import draftboard.ui.components.commissioner_tools as m
print(m.__file__)
PY'
```

## 13.5 Compile truth

```bash
docker exec -i mlf_draftboard bash -lc "python -m py_compile /app/app/src/draftboard/ui/components/commissioner_tools.py /app/app/src/draftboard/ui/components/board_html.py /app/app/src/draftboard/ui/app.py"
```

## 13.6 Refresh-proof behavior

After any pick-trade mutation:

- rerun app
- refresh browser
- confirm ownership persists
- confirm column did not move
- confirm traded label/owner display is correct

## 13.7 Predraft traded-pick proof

For traded predraft picks, explicitly verify:

- column remains fixed
- owner changed
- placeholder follows current pick owner
- behavior persists after refresh

---

# 14) Critical Invariants (Do Not Break)

- draft order and current pick ownership are different truths
- draft order derives from the active profile’s first standard round, not from predraft rounds and not from hard-coded Round-6 assumptions
- `draft.order_mode` is league-profile truth
- `state.draft_order_team_keys_by_slot` is the slot baseline runtime surface
- `state.picks[pick_id].owner_team_key` is current pick ownership truth
- current pick ownership is persisted in DraftBoard state, not defined by trade receipts
- trade receipts are audit/history only
- pick trade changes ownership only, not slot/column identity
- do not mutate `original_team_key` during pick trades
- do not mutate `draft_order_team_keys_by_slot` during pick trades
- traded picks stay in their original columns
- predraft picks are tradable assets when league policy allows them
- predraft placeholder ownership for traded predraft picks follows current pick owner
- profile-driven draft order does not redefine current pick ownership truth
- persistence success must be proven with DraftBoard state change and refresh-proof behavior
- rollback must restore canonical ownership before cleaning up receipt history
- do not claim full snake support complete until `order_mode` is fully consumed by live derivation/runtime behavior

---

# 15) Document Intent

This document exists to help a new chat:

- distinguish slot identity from current ownership
- reason correctly about traded picks
- verify pick-trade persistence deterministically
- avoid confusing receipts with current truth
- keep traded predraft-pick behavior aligned with canonical ownership rules
- keep profile-driven draft-order facts separate from ownership truth

It intentionally does **not** try to fully document every subsystem touching picks.

Those details should live in companion canonicals or league overlays such as:

- Draft State / Initialization / Restore
- Team / Franchise Identity
- Auth / Permissions
- UI Architecture
- league-specific overlays for MLF or MiLF differences