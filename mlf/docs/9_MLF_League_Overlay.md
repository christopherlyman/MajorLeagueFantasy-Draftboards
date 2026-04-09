# MLF League Overlay Canonical

## Purpose

This document defines the **MLF-only overlay** on top of the shared fantasy portfolio canonicals.

Shared canonicals live in:

```text
../../shared/docs/
```

This overlay exists to capture only the parts of MLF that are **not** common shared truth.

It should answer questions like:

- What is specifically MLF-only?
- Which features or rules make MLF different from MiLF?
- Which commissioner workflows are unique to MLF?
- What next-season prep exists only because MLF has contract/control-rights behavior?
- Are there any current MLF-specific runtime or deployment deviations from the shared canonicals?

If something conflicts:

> **DB truth → container runtime → application state → documentation**

This document is **not** a history log.

---

# 0) Scope

This overlay governs:

- MLF-only player-control model
- MLF-only predraft placeholder semantics
- MLF-only commissioner workflows
- MLF-only next-season prep differences
- any MLF-only runtime or deployment deviations from shared canonicals

This overlay does **not** replace the shared canonicals.

Use shared canonicals for:

- core anti-drift rules
- shared draft-state rules
- shared identity rules
- shared pick-ownership rules
- shared auth rules
- shared UI structure
- shared deployment/infrastructure truth
- shared multi-league target architecture

Canonical rule:

```text
this overlay defines MLF-only deltas,
not a second full canonical system
```

---

# 1) Relationship to Shared Canonicals

## 1.1 Shared Docs Remain Authoritative for Common Truth

The following documents remain shared-owned and authoritative for common truth:

- `../../shared/docs/0_CoreCanonicalGuide.md`
- `../../shared/docs/1_DraftState_Initialization_Restore.md`
- `../../shared/docs/2_Team_Franchise_Identity.md`
- `../../shared/docs/4_Pick-Ownership_Pick-Trades_Draft-Order.md`
- `../../shared/docs/5_Auth_Permissions.md`
- `../../shared/docs/6_UI_Architecture.md`
- `../../shared/docs/7_Deployment_Infrastructure.md`
- `../../shared/docs/8_Multi-League_Target_Architecture.md`

## 1.2 MLF-Owned Canonical Surfaces

MLF currently owns:

- `3_Contracts_Prospect_Tags_QO.md`
- this overlay file

Canonical rule:

```text
shared docs own common subsystem truth
mlf docs own only MLF-specific deltas
```

---

# 2) MLF Identity as a League Profile

## 2.1 MLF Is the Most Feature-Rich Current League

Within the current portfolio, MLF is the most complex active ruleset.

MLF should be treated as the **contract / control-rights** league.

High-level MLF characteristics include:

- contract-based player control
- qualifying offers
- prospect-tag behavior
- predraft placeholder complexity
- richer commissioner workflows
- more complex next-season prep than a simple redraft league

## 2.2 MLF Is Not the Shared Default

MLF is the current reference implementation for many live runtime surfaces, but it must not be treated as the automatic shared default for all future leagues.

Canonical rule:

```text
mlf is the current richest league implementation,
not the definition of all leagues
```

---

# 3) MLF-Only Player-Control Model

## 3.1 MLF Owns the Contract / PT / QO Layer

The full detailed truth for these areas belongs in:

```text
3_Contracts_Prospect_Tags_QO.md
```

This overlay only captures the architectural consequence:

MLF includes player-control and predraft-rights behavior that goes beyond shared redraft behavior.

## 3.2 MLF Predraft Board Complexity Is Higher

Compared with simpler leagues, MLF includes more complex predraft board reconstruction because it must account for:

- contract-controlled players
- prospect-tag-controlled players
- qualifying-offer behavior
- predraft placeholder reconstruction
- keeper-style prefill in standard rounds
- traded predraft pick ownership interacting with placeholder display

Canonical rule:

```text
mlf predraft board reconstruction is materially more complex
than a simple redraft league
```

## 3.3 MLF Control Rights Are Not Shared Assumptions

Contracts, PT, and QO must not be assumed in other leagues unless those leagues explicitly adopt them through league profile and league-owned docs.

Canonical rule:

```text
contracts, pt, and qo are mlf-owned league behavior,
not shared baseline behavior
```

---

# 4) MLF-Only Commissioner Workflows

## 4.1 MLF Commissioner Workflows Are Broader Than Shared Baseline

MLF commissioner workflows include shared commissioner surfaces plus MLF-specific operational tasks tied to control-rights and predraft state complexity.

Examples of MLF-only workflow categories include:

- contract / PT / QO preparation and review
- predraft placeholder validation
- traded predraft pick verification under MLF rules
- MLF-specific next-season carry-forward checks
- control-rights-sensitive board sanity verification before draft use

## 4.2 MLF Commissioner Actions Must Still Respect Shared Auth Boundaries

Even where MLF has richer commissioner workflows, shared auth rules still apply:

- commissioner access is not the same as site-admin authority
- admin-only auth/account controls remain separately gated
- URL surface selection is not permission truth by itself

Canonical rule:

```text
mlf may extend commissioner workflow complexity
without changing shared auth/permission boundaries
```

---

# 5) MLF-Only Next-Season Prep Differences

## 5.1 MLF Next-Season Prep Is Not Just a New League Key

MLF next-season prep requires more than standard season rollover because MLF must re-prove or refresh the league-specific control-rights surfaces.

Typical MLF-only next-season prep categories include:

- new season team/franchise rollover mapping
- new season active league profile verification
- contract carry-forward / update review
- prospect-tag carry-forward / update review
- qualifying-offer carry-forward / update review
- predraft placeholder reconstruction sanity checks
- draft-order and predraft rounds sanity under active MLF profile
- current Yahoo player-universe refresh for the new season
- MLF-specific commissioner tool checks before draft use

## 5.2 MLF Next-Season Prep Must Remain Deterministic

Canonical rule:

```text
mlf next-season prep must be driven by canonical db/profile truth
not by memory, ui appearance, or ad hoc manual assumptions
```

## 5.3 MLF Needs a League-Specific Prep Checklist

MLF should maintain a league-specific next-season prep checklist separate from shared architecture docs, because the shared docs should not absorb MLF-only seasonal operational detail.

Canonical rule:

```text
shared docs define structure;
mlf owns its own seasonal operating checklist
```

---

# 6) MLF Runtime / Deployment Deviations from Shared Canonicals

## 6.1 Current Live Public Stack Is Still MLF-Local

At the moment, the current live public deployment is still owned by the MLF league root.

Current live deployment surfaces remain:

```text
/Volume1/Bots/fantasy/mlf/runtime/docker-compose.yml
/Volume1/Bots/fantasy/mlf/.env
```

This is already documented in the shared deployment canonical, but it remains an important practical MLF fact.

## 6.2 This Is Current Ownership, Not Permanent Architecture Privilege

MLF’s current league-local deployment ownership should not be interpreted as meaning MLF permanently owns shared runtime behavior.

Canonical rule:

```text
mlf currently owns the live deployment surfaces,
but shared canonicals still own the common deployment rules
```

## 6.3 Current Shared Runtime Extraction Example Still Serves MLF

A proven shared runtime asset now exists:

```text
/Volume1/Bots/fantasy/shared/runtime/auth_bridge.py
```

But its current live deployment ownership remains tied to the MLF public stack.

Canonical rule:

```text
mlf can currently host a live shared runtime helper
without redefining shared ownership boundaries
```

## 6.4 No Additional MLF-Specific Deployment Deviation Should Be Invented Here Without Proof

This overlay should only document real MLF-specific deployment/runtime deviations that are proven.

Canonical rule:

```text
do not create mlf-specific deployment exceptions
unless runtime truth proves they are real
```

---

# 7) Relationship to MiLF

## 7.1 MiLF Is the Simpler Contrast Case

MiLF exists as the simpler redraft comparison point.

That contrast matters because it helps define what truly belongs to MLF only.

Typical MLF-vs-MiLF contrast:

- MLF = contracts / PT / QO / richer predraft-control logic
- MiLF = simpler redraft behavior

## 7.2 MLF Complexity Must Not Leak Into Shared Baseline

Canonical rule:

```text
mlf-only complexity should stay in mlf-owned docs and overlays
unless a rule is proven reusable across leagues
```

---

# 8) What Belongs Here vs Somewhere Else

## 8.1 Belongs Here

Keep in this overlay:

- MLF-only deltas from shared truth
- MLF-only workflow notes
- MLF-only next-season prep differences
- MLF-only runtime/deployment deviations from shared canonicals

## 8.2 Does Not Belong Here

Do **not** duplicate full subsystem truth from:

- shared core guide
- shared draft-state canonical
- shared identity canonical
- shared pick-ownership canonical
- shared auth canonical
- shared UI canonical
- shared deployment canonical
- shared multi-league target architecture canonical

Do **not** absorb the full detail of contract / PT / QO domain truth here either; keep that in:

```text
3_Contracts_Prospect_Tags_QO.md
```

Canonical rule:

```text
this overlay should stay thin
and point outward to the subsystem owner docs
```

---

# 9) Critical Invariants (Do Not Break)

- shared canonicals remain authoritative for shared truth
- MLF owns only MLF-specific deltas
- contracts / PT / QO are MLF-owned league behavior, not shared baseline assumptions
- MLF commissioner workflow may be richer without changing shared auth boundaries
- MLF next-season prep requires league-specific control-rights validation
- current live MLF deployment ownership does not make MLF the shared architecture owner
- do not duplicate full shared canonicals back into `mlf/docs`
- do not collapse `3_Contracts_Prospect_Tags_QO.md` into this overlay

---

# 10) Document Intent

This document exists to help a new chat:

- identify what is uniquely MLF
- avoid treating MLF as the shared default
- route shared questions back to shared canonicals
- keep MLF-specific complexity documented without duplicating whole subsystem docs
- preserve a clean shared-plus-overlay documentation structure

It intentionally does **not** try to replace the shared canonicals or the MLF contracts/PT/QO canonical.