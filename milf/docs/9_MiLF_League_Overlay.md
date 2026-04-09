# MiLF League Overlay Canonical

## Purpose

This document defines the **MiLF-only overlay** on top of the shared fantasy portfolio canonicals.

Shared canonicals live in:

```text
../../shared/docs/
```

This overlay exists to capture only the parts of MiLF that are **not** common shared truth.

It should answer questions like:

- What is specifically MiLF-only?
- Which features or rules make MiLF different from MLF?
- Which workflows are unique to MiLF?
- What next-season prep exists only because MiLF is a simpler redraft league?
- Are there any current MiLF-specific runtime or env differences from the shared canonicals?

If something conflicts:

> **DB truth → container runtime → application state → documentation**

This document is **not** a history log.

---

# 0) Scope

This overlay governs:

- MiLF-only redraft behavior
- MiLF-only workflow notes
- MiLF-only next-season prep differences
- any MiLF-only runtime or env deviations from shared canonicals
- any MiLF-only commissioner/runtime differences that are actually proven

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
this overlay defines MiLF-only deltas,
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

## 1.2 MiLF-Owned Overlay Surface

MiLF currently owns:

- this overlay file

Canonical rule:

```text
shared docs own common subsystem truth
milf docs own only MiLF-specific deltas
```

---

# 2) MiLF Identity as a League Profile

## 2.1 MiLF Is the Simpler Redraft League

Within the current portfolio, MiLF should be treated as the simpler **redraft** contrast case.

High-level MiLF characteristics include:

- simpler redraft behavior
- reduced predraft/control-rights complexity
- less commissioner workflow complexity than MLF
- good first proof-of-reuse outside the current MLF live stack

Canonical rule:

```text
MiLF is the simpler redraft proof-of-template league,
not a contract/control-rights league
```

## 2.2 MiLF Is Not Just “MLF Minus a Few Buttons”

MiLF should not be reasoned about as an accidental cut-down copy of MLF.

It should be reasoned about as its own league profile with its own simpler behavior.

Canonical rule:

```text
MiLF should be modeled by its own profile truth,
not by subtracting random MLF behaviors
```

---

# 3) MiLF-Only Rules and Feature Boundaries

## 3.1 MiLF Is Redraft-Only by Default

MiLF should be treated as a redraft league unless its profile truth is deliberately changed in a future season.

Practical consequences include:

- no contract-control model
- no prospect-tag control model
- no qualifying-offer control model
- no MLF-style predraft control-rights reconstruction

Canonical rule:

```text
MiLF redraft behavior is the league baseline;
MLF control-rights behavior must not leak into MiLF by assumption
```

## 3.2 MiLF Does Not Inherit MLF Control-Rights Semantics

The following must not be assumed for MiLF unless explicitly adopted in a future league profile:

- contracts
- prospect tags
- qualifying offers
- MLF-style predraft placeholder/control-rights behavior
- MLF-specific next-season carry-forward logic for player control

Canonical rule:

```text
contracts, PT, and QO are not MiLF baseline behavior
```

## 3.3 Shared Draft-State Rules Still Apply

Even though MiLF is simpler, it still uses the shared rules for:

- persisted state authority
- restore precedence
- initialization/healing boundary
- slot identity vs current pick ownership
- profile-driven draft-order reasoning

Canonical rule:

```text
MiLF is simpler in league rules,
not exempt from shared state and identity rules
```

---

# 4) MiLF-Only Commissioner Workflows

## 4.1 MiLF Commissioner Workflows Should Be Simpler Than MLF

MiLF commissioner workflows should focus on core redraft operations rather than control-rights administration.

Examples of MiLF-appropriate workflow categories include:

- standard draft setup checks
- current league profile verification
- draft-order and league settings sanity checks
- current player-universe refresh
- commissioner access verification
- basic pre-draft board sanity checks

## 4.2 MiLF Commissioner Workflows Must Still Respect Shared Auth Boundaries

Even when MiLF commissioner workflows are simpler, shared auth rules still apply:

- commissioner access is not the same as site-admin authority
- admin-only auth/account controls remain separately gated
- URL surface selection is not permission truth by itself

Canonical rule:

```text
MiLF may simplify commissioner workflow scope
without changing shared auth/permission boundaries
```

## 4.3 MiLF Should Avoid Accidental MLF Workflow Carryover

If a commissioner workflow exists only because MLF has contracts / PT / QO / richer predraft control logic, it should not be copied into MiLF by default.

Canonical rule:

```text
do not import MLF-only commissioner complexity into MiLF
without explicit league need and proof
```

---

# 5) MiLF-Only Next-Season Prep Differences

## 5.1 MiLF Next-Season Prep Should Be Simpler

MiLF next-season prep should be lighter than MLF next-season prep because it does not need the same control-rights carry-forward logic.

Typical MiLF-only next-season prep categories include:

- new season team/franchise rollover mapping
- new season active league profile verification
- new season draft-order and league settings sanity
- current Yahoo player-universe refresh
- basic commissioner tool checks before draft use

## 5.2 MiLF Next-Season Prep Should Not Pretend Control-Rights Validation Exists

MiLF next-season prep should not include MLF-style checks for:

- contract carry-forward
- prospect-tag carry-forward
- qualifying-offer carry-forward
- MLF-style predraft placeholder reconstruction from player-control sources

Canonical rule:

```text
MiLF next-season prep should stay aligned to redraft reality,
not copied MLF complexity
```

## 5.3 MiLF Needs Its Own League-Specific Prep Checklist

MiLF should maintain its own league-specific seasonal prep checklist separate from shared architecture docs.

Canonical rule:

```text
shared docs define structure;
MiLF owns its own seasonal operating checklist
```

---

# 6) MiLF Runtime / Env Differences from Shared Canonicals

## 6.1 Only Proven MiLF Runtime Differences Belong Here

This overlay should only document MiLF-specific runtime or env differences that are actually proven.

Canonical rule:

```text
do not invent MiLF-specific runtime/env differences
unless runtime truth proves they are real
```

## 6.2 Current MiLF Difference Is Primarily League Simplicity, Not Shared-Rule Replacement

At the current stage, the main architectural MiLF difference is:

- simpler redraft profile
- less control-rights complexity
- likely cleaner first reuse target

This does **not** mean MiLF gets its own separate versions of shared auth, shared state rules, or shared UI architecture by default.

## 6.3 MiLF Bring-Up Status Must Be Described Carefully

A MiLF profile may exist before every operational runtime surface is fully proven.

Canonical rule:

```text
MiLF profile truth and MiLF fully proven operational runtime truth
are different concepts
```

Therefore this overlay should avoid overstating MiLF runtime facts that have not yet been re-proven.

---

# 7) Relationship to MLF

## 7.1 MLF Is the Richer Contrast Case

MLF exists as the richer contract/control-rights contrast case.

That contrast matters because it helps define what truly belongs to MiLF only.

Typical MiLF-vs-MLF contrast:

- MiLF = simpler redraft behavior
- MLF = contracts / predraft rights / richer commissioner complexity

## 7.2 MiLF Simplicity Must Not Be Treated as Missing Shared Architecture

MiLF being simpler does **not** mean MiLF gets a different version of the shared canonicals.

Canonical rule:

```text
MiLF simplicity is a league-rule difference,
not a separate shared-architecture system
```

---

# 8) What Belongs Here vs Somewhere Else

## 8.1 Belongs Here

Keep in this overlay:

- MiLF-only deltas from shared truth
- MiLF-only workflow notes
- MiLF-only next-season prep differences
- real MiLF-only runtime/env differences if and when proven

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

Do **not** copy MLF-only contract / PT / QO detail here.

Canonical rule:

```text
this overlay should stay thin
and point outward to the subsystem owner docs
```

---

# 9) Critical Invariants (Do Not Break)

- shared canonicals remain authoritative for shared truth
- MiLF owns only MiLF-specific deltas
- MiLF is a redraft league baseline unless deliberately changed by future profile truth
- MLF control-rights behavior must not leak into MiLF by assumption
- MiLF commissioner workflow may be simpler without changing shared auth boundaries
- MiLF next-season prep should stay aligned to redraft reality
- do not duplicate full shared canonicals back into `milf/docs`
- do not invent MiLF-specific runtime/env exceptions without proof
- profile truth and fully proven operational runtime truth are different concepts

---

# 10) Document Intent

This document exists to help a new chat:

- identify what is uniquely MiLF
- avoid treating MLF complexity as MiLF default behavior
- route shared questions back to shared canonicals
- keep MiLF-specific simplicity documented without duplicating whole subsystem docs
- preserve a clean shared-plus-overlay documentation structure

It intentionally does **not** try to replace the shared canonicals.