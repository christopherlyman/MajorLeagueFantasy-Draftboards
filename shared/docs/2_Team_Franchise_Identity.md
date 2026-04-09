# Fantasy Portfolio — Team / Franchise Identity Canonical

**Purpose:**  
This document defines the canonical truth model for **team identity**, **franchise continuity**, **season-specific mapping**, and **identity resolution** across the fantasy portfolio.

It exists to help a new chat deterministically answer questions like:

- What is the authoritative team identity in the current season?
- What is the stable cross-season identity?
- What is the difference between `franchise_id` and `team_key`?
- How should season rollover be handled?
- What should never be treated as identity?
- How should manager/team mappings reason about continuity?

If something conflicts:

> **DB truth → container runtime → application state → documentation**

This document is **not** a history log.

---

# 0) Scope

This canonical governs:

- franchise identity
- season team identity
- current-season canonical team keys
- franchise continuity across seasons
- season rollover mapping logic
- owner-guid linkage rules
- identity verification procedures
- anti-drift identity rules

This canonical does **not** define:

- full auth system behavior
- draft-state restore internals
- contract / PT / QO business logic
- UI layout behavior
- deployment topology
- league-specific commissioner workflow details except where required to explain identity boundaries

It may reference those areas only where necessary to explain identity boundaries.

---

# 1) Identity Model Overview

## 1.1 Identity Is Multi-Layered [VERIFIED]

The system uses more than one kind of “team identity.”

These must not be collapsed into one concept.

Canonical layers:

1. **Franchise identity** — stable internal identity across seasons
2. **Season identity** — franchise participation in a specific league season
3. **Current-season external team identity** — current-season Yahoo-style `team_key`
4. **Display identity** — human-facing team name / owner fields
5. **Owner linkage helper** — `owner_guid` as a continuity aid, not ultimate identity

Canonical rule:

```text
franchise identity ≠ season team identity ≠ display label
```

A large share of identity drift comes from mixing these layers.

## 1.2 Identity Must Be Scoped [VERIFIED]

Identity reasoning must be scoped by the active league/season context.

Canonical rule:

```text
identity reasoning is invalid if it ignores league_key and season_year
```

This matters because team identity is season-specific even when franchise continuity spans multiple seasons.

---

# 2) Canonical Current-Season Identity

## 2.1 Season Team Assignment SSOT [VERIFIED]

The canonical SSOT table for current-season team assignment is:

```sql
public.franchise_season_team
```

This table is the single source of truth for:

- which current-season `team_key` maps to which internal `franchise_id`
- current-season display values such as `team_name` and owner-facing fields
- assignment provenance such as `auto` vs `manual`

Canonical rule:

```text
current-season team assignment truth lives in public.franchise_season_team
```

## 2.2 Current-Season Canonical Team Key [VERIFIED]

Within a season, canonical runtime team identity uses the Yahoo-style team key stored in SSOT:

```text
<game_key>.l.<league_id>.t.<team_id>
example: 469.l.41640.t.12
```

Canonical rule:

```text
canonical season team_key = franchise_season_team.team_key
```

This is the identity that should be used in current-season runtime ownership and mapping.

## 2.3 Current-Season Mapping Sanity [VERIFIED/REQUIREMENT]

Within a scoped `(league_key, season_year)`, expected sanity rules include:

- `team_key` unique within the scoped season
- `franchise_id` unique within the scoped season
- row count aligned with the actual league team count for that scoped season

Canonical rule:

```text
current-season mapping must be one-franchise-to-one-team
within a scoped league season
```

---

# 3) Stable Cross-Season Identity

## 3.1 Franchise Identity Is the Stable Unit [VERIFIED]

Cross-season continuity is represented by:

```sql
public.franchise
```

using:

```text
franchise_id
```

as the stable internal identity.

Canonical rule:

```text
franchise_id is the durable league-side identity across seasons
```

## 3.2 Season Participation Is a Separate Layer [VERIFIED]

Season-specific participation is represented by:

```sql
public.franchise_season_team
```

Therefore the canonical hierarchy is:

1. **Franchise identity** = `franchise_id`
2. **Season identity** = (`franchise_id`, `season_year`, `league_key`)
3. **Current-season external team identity** = `team_key`
4. **Display identity** = `team_name` and related labels

Canonical rule:

```text
do not use team_key as the stable cross-season franchise identity
```

## 3.3 Why This Matters [VERIFIED]

A raw season `team_key` is season-scoped and not stable across seasons.

A stable internal identity is required for:

- franchise continuity
- manager/team mapping
- cross-season reasoning
- rollover linking
- historical analysis
- future multi-league hygiene

---

# 4) What Is Not Canonical Identity

## 4.1 Legacy TEAM_XX Keys Are Not Canonical [VERIFIED]

Legacy identifiers such as:

```text
TEAM_01 ... TEAM_16
```

may still appear in:

- old autosave state
- historical rows
- older predraft data
- legacy storage surfaces

But they are never canonical current-season identity.

Canonical rule:

```text
TEAM_XX keys are legacy representations,
not canonical identity
```

## 4.2 UI Order Is Not Identity [VERIFIED]

The following must not be treated as team identity:

- board column order
- slot number
- visual board location
- tab ordering
- commissioner display order

These may reflect state or presentation, but they are not canonical identity.

## 4.3 Display Labels Are Not Identity [VERIFIED]

Human-facing values such as:

- `team_name`
- owner name text
- UI labels

are useful for display and validation, but they are not canonical identity keys.

Canonical rule:

```text
display labels help humans;
they do not replace canonical keys
```

---

# 5) Owner GUID and Continuity Rules

## 5.1 owner_guid Is a Linkage Helper [VERIFIED]

`owner_guid` may be used to help connect returning managers/franchises across seasons.

Its role is:

- continuity aid
- auto-link helper during season rollover
- evidence input for mapping decisions

## 5.2 owner_guid Is Not Stable Franchise Identity [VERIFIED]

Canonical rule:

```text
owner_guid is not the franchise identity
```

Reason:

- owners can change
- franchise continuity may persist even when ownership changes
- owner linkage is helpful but not sufficient as ultimate identity truth

## 5.3 Correct Use of owner_guid [VERIFIED/REQUIREMENT]

Use `owner_guid` to:

- assist automated carry-forward matching
- identify likely returning franchises
- reduce commissioner manual work

Do not use `owner_guid` to permanently replace:

- `franchise_id`
- season SSOT mapping
- commissioner-reviewed assignment decisions

---

# 6) Season Rollover Canonical Model

## 6.1 Rollover Exists to Preserve Franchise Continuity [VERIFIED]

Season rollover must preserve stable franchise continuity while attaching it to the new season’s current-season teams.

This is a mapping problem, not a UI-order problem.

## 6.2 Canonical Rollover Algorithm [VERIFIED]

For a new season, canonical rollover proceeds conceptually as follows:

1. Load new-season teams into the current-season team source
2. Seed new-season rows in:

```sql
public.franchise_season_team
```

3. Auto-link likely returning franchises using prior-season `owner_guid`
4. Identify exceptions / unmatched cases
5. Commissioner manually assign `(new season team) → existing franchise_id` where needed
6. Persist an audit receipt of the assignment decision

Canonical rule:

```text
rollover should preserve franchise continuity
while re-establishing season mapping
```

## 6.3 Commissioner Manual Mapping Is Part of the Canonical Model [VERIFIED]

Manual mapping is not a failure of the system.

It is part of the expected canonical rollover process for cases where:

- owner linkage is ambiguous
- ownership changed
- auto-link confidence is insufficient
- league changes require deliberate reassignment

---

# 7) Runtime Identity Rules

## 7.1 Current-Season Runtime Should Use Canonical team_key [VERIFIED]

Within the running DraftBoard system, season ownership/mapping should reason from the canonical current-season `team_key` surface.

Canonical rule:

```text
current-season runtime ownership should use
the canonical Yahoo-format team_key
from franchise_season_team
```

## 7.2 Runtime Must Not Invent Alternate Team Identity Systems [REQUIREMENT]

Do not create parallel live identity systems based on:

- `TEAM_XX`
- slot numbers
- UI labels
- ad hoc display strings
- temporary session ordering

If an alternate representation is necessary for display or import compatibility, it must be normalized back to canonical identity at the proper boundary.

## 7.3 Identity Normalization Boundary [VERIFIED/REQUIREMENT]

If legacy or alternate team representations enter runtime, normalization must occur at a single canonical boundary during initialization/loading.

Not in:

- rendering code
- board HTML helpers
- scattered UI logic
- one-off display patches

Canonical rule:

```text
identity normalization belongs at the canonical
initialization/loading boundary
```

---

# 8) Relationship to Auth / Permissions

## 8.1 Manager Identity Must Not Be Mapped Only to team_key [VERIFIED]

Human/manager identity should not be modeled as direct permanent attachment to a raw season `team_key`.

Canonical mapping path is:

```text
auth_user
→ auth_user_league_role.franchise_id
→ franchise_season_team
→ current season team_key / team_name
```

This preserves continuity even when season team keys change.

## 8.2 Why franchise_id Matters for Auth [VERIFIED]

Using `franchise_id` as the stable middle layer makes auth/permissions reasoning safer because:

- it survives season rollover
- it supports multi-season continuity
- it avoids making season-scoped team keys act like permanent human identity

Canonical rule:

```text
franchise_id is the stable human-to-franchise linkage layer
```

---

# 9) Relationship to Draft State

## 9.1 Draft State Depends on Canonical Team Identity [VERIFIED]

Draft state, pick ownership, and board semantics must all reason from canonical team identity surfaces.

If team identity mapping is wrong, downstream systems will misreason about:

- pick ownership
- draft order interpretation
- contracts / PT / QO ownership
- manager permissions
- display correctness

## 9.2 Board Surfaces Must Not Override Identity Truth [REQUIREMENT]

If the board appears to show a team in a certain place, that visual placement is not identity proof.

Always verify the underlying season identity mapping first.

---

# 10) Deterministic Verification Procedure

## 10.1 Minimum Identity Questions

When identity reasoning looks wrong, answer these first:

1. What is the scoped `league_key` and `season_year`?
2. What does `public.franchise_season_team` say?
3. Is each `franchise_id` unique within the scoped season?
4. Is each `team_key` unique within the scoped season?
5. Is a legacy keyspace being mistaken for canonical identity?
6. Is someone using `owner_guid` as identity instead of linkage helper?
7. Is runtime using the canonical season `team_key`?

## 10.2 Deterministic Debug Order [VERIFIED]

When team/franchise identity looks wrong:

1. DB truth
2. imported runtime file certainty
3. loader/runtime output
4. application state
5. UI rendering

Never reverse this order.

---

# 11) Verify Pack

## 11.1 Scoped season mapping sanity

```sql
select
  season_year,
  league_key,
  count(*) as rows
from public.franchise_season_team
where season_year = <season_year>
  and league_key = '<league_key>'
group by season_year, league_key;
```

Expected:

```text
row count = actual team count for the scoped league season
```

## 11.2 Uniqueness sanity

```sql
select team_key, count(*) as c
from public.franchise_season_team
where season_year = <season_year>
  and league_key = '<league_key>'
group by team_key
having count(*) > 1;
```

Expected:

```text
0 rows
```

```sql
select franchise_id, count(*) as c
from public.franchise_season_team
where season_year = <season_year>
  and league_key = '<league_key>'
group by franchise_id
having count(*) > 1;
```

Expected:

```text
0 rows
```

## 11.3 Full scoped mapping surface

```sql
select
  fst.league_key,
  fst.season_year,
  fst.franchise_id,
  fst.team_key,
  fst.team_name,
  fst.source
from public.franchise_season_team fst
where fst.league_key = '<league_key>'
  and fst.season_year = <season_year>
order by fst.franchise_id;
```

Use this as the primary identity proof surface.

## 11.4 Legacy-key detector where relevant

Example detector shape:

```sql
select count(*) as legacy_team_keys
from public.qualifying_offer
where league_key = '<league_key>'
  and season_year = <season_year>
  and team_key like 'TEAM_%';
```

Expected in canonicalized current-state areas:

```text
0
```

This is not the only detector, but it is a useful anti-drift check where legacy team representations previously existed.

---

# 12) Critical Invariants (Do Not Break)

- `franchise_id` is the stable cross-season franchise identity
- `public.franchise_season_team` is the SSOT for current-season franchise ↔ team mapping
- canonical current-season runtime team identity uses the Yahoo-format `team_key`
- `team_key` is season-scoped, not stable cross-season identity
- `owner_guid` is a rollover linkage helper, not ultimate franchise identity
- `TEAM_XX` is legacy, not canonical
- UI order, slot number, and display labels are not identity
- identity normalization belongs at a single loading/initialization boundary
- commissioner manual mapping is part of the canonical rollover model
- auth and permissions should use `franchise_id` as the stable linkage layer
- identity reasoning must remain scoped by `(league_key, season_year)`

---

# 13) Document Intent

This document exists to help a new chat:

- reason correctly about team identity
- distinguish franchise continuity from season-specific mapping
- avoid treating display/UI order as identity
- handle rollover deterministically
- keep downstream systems anchored to correct identity truth
- preserve shared identity rules while allowing league-specific overlays only where needed

It intentionally does **not** try to document every subsystem that consumes identity.

Those details should live in companion canonicals or league overlays such as:

- Draft State / Initialization / Restore
- Auth / Permissions
- Pick Ownership / Pick Trades
- UI Architecture
- league-specific overlays for MLF or MiLF differences