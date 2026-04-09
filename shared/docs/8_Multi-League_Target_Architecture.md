# Fantasy Portfolio — Multi-League Target Architecture Canonical

## Purpose

This document defines the **target architecture** for evolving the current fantasy portfolio into a reusable multi-league platform that can support multiple leagues with different rule sets.

Current active league set:

- `mlf` = Major League Fantasy  
  Baseball contract/control-rights league with contracts, predraft rights, commissioner tooling, auth, and complex DraftBoard state behavior.

- `milf` = Minor League Fantasy  
  Baseball redraft league with a simpler DraftBoard and fewer control-rights features.

Future leagues may be added later using the same architectural boundaries.

This document is about **target structure, current boundaries, and migration rules**, not just immediate file moves.

If something conflicts:

> **DB truth → container runtime → application state → documentation**

This document is **not** a history log.

---

# 0) Scope

This canonical governs:

- portfolio root vs league root boundaries
- shared vs league-specific ownership
- target folder structure
- profile-driven multi-league architecture direction
- current live deployment ownership vs future shared extraction direction
- feature-tier strategy
- DB strategy
- deployment strategy
- migration-phase rules
- no-move / high-risk live-surface rules

This canonical does **not** define:

- full DraftBoard state logic
- full auth business semantics
- full UI implementation detail
- league-specific business policy details
- low-level deployment verification procedures except where they affect architecture boundaries

It may reference those areas only where necessary to explain target architecture.

---

# 1) Core Architectural Principle

## 1.1 Portfolio Root vs League Root

Going forward, distinguish between:

- **Portfolio root**  
  The parent area that contains shared assets and multiple league roots.

- **League root**  
  The root folder for one specific league deployment/project.

Current canonical boundary:

- `/Volume1/Bots/fantasy/` = **portfolio root**
- `/Volume1/Bots/fantasy/shared/` = **shared portfolio assets**
- `/Volume1/Bots/fantasy/mlf/` = **current MLF league root**
- `/Volume1/Bots/fantasy/milf/` = **current MiLF league root**

Canonical rule:

```text
fantasy/ is the portfolio container
shared/ holds shared assets
mlf/ and milf/ are league roots inside that portfolio
```

Do not treat `fantasy/` as if it were the MLF app root.

## 1.2 Shared Canonicals vs League Overlays

Shared canonicals own:

- shared architecture rules
- shared subsystem truth
- shared deployment patterns
- shared anti-drift rules
- shared future-state direction

League docs own:

- league-only overlays
- league-only rule differences
- league-only runtime/deploy deviations
- league-only next-season procedures

Canonical rule:

```text
shared canonicals define common truth
league overlays define only real deviations
```

---

# 2) Current Canonical Portfolio Structure

## 2.1 Current Practical Structure

Current practical structure should now be reasoned as:

```text
fantasy/
  shared/
    app/
    runtime/
    scripts/
    docs/
  mlf/
    runtime/
    scripts/
    docs/
    data/
    outputs/
    sql/
    .env
    Dockerfile
    requirements.txt
  milf/
    runtime/
    docs/
    data/
    outputs/
    config/
    state/
    .env
```

This is the **current portfolio-level structure in use**, not merely a hypothetical future container.

## 2.2 Current Three-Docs-Folder Rule

Canonical documentation structure now includes:

```text
fantasy/shared/docs/
fantasy/mlf/docs/
fantasy/milf/docs/
```

Canonical rule:

```text
shared canonicals live in shared/docs
league overlays and league-specific docs live in each league root
```

## 2.3 Shared Runtime Extraction Has Begun

A proven shared runtime asset now exists:

```text
/Volume1/Bots/fantasy/shared/runtime/auth_bridge.py
```

Canonical rule:

```text
shared extraction has now begun in a limited proven form
```

This does **not** mean deployment ownership is already fully shared.

---

# 3) Shared vs League-Specific Boundaries

## 3.1 Shared Components

These should be shared across leagues when the responsibility is genuinely reusable:

- core DraftBoard app shell
- auth/session framework
- board rendering framework
- shared DraftBoard state framework
- pick log / pick tracker framework
- shared runtime helpers
- shared Yahoo ingestion utilities where reuse is proven
- shared DB access helpers
- shared profile-loading/validation seams
- shared canonicals and anti-drift rules

## 3.2 League-Specific Components

These should remain configurable or league-owned per league:

- league code (`mlf`, `milf`, future league codes)
- league key
- season year
- draft key
- display title / branding
- enabled feature set
- rule model
- control-rights features
- league-specific commissioner workflows
- league-specific raw data / outputs / archives
- league-specific next-season procedures

## 3.3 Boundary Rule

Canonical rule:

```text
shared architecture should be derived from reusable responsibilities
not from leftover MLF operational sediment
```

Do not promote a file to shared-core merely because it currently lives in a path that looks reusable.

---

# 4) Current Live Ownership vs Future Extraction

## 4.1 Current Live Deployment Ownership Remains League-Local

For the current live public stack, deployment ownership still remains under the MLF league root.

Current live deployment surfaces include:

```text
/Volume1/Bots/fantasy/mlf/runtime/docker-compose.yml
/Volume1/Bots/fantasy/mlf/.env
```

Canonical rule:

```text
current live MLF deployment remains league-local
even where some code assets are now shared
```

## 4.2 Shared Code Extraction Does Not By Itself Imply Shared Deployment Ownership

The current auth bridge is the clearest proven example:

- code location = shared
- deployment ownership = still MLF-local
- public behavior = stable and proven

Canonical rule:

```text
shared code extraction can happen before shared deployment extraction
```

## 4.3 Current Live Shared Runtime Example

Current live shared runtime example:

```text
shared/runtime/auth_bridge.py
```

Current live bridge execution path:

```text
python /shared/runtime/auth_bridge.py
```

Canonical rule:

```text
a shared runtime helper may be live
while deployment SSOT remains league-local
```

---

# 5) Feature-Tier Model

## 5.1 Why Feature Tiers Exist

To avoid cloning the entire app per league, leagues should eventually be modeled by **feature tier** and **profile truth**, not by separate hard-forked apps.

## 5.2 Proposed Tiers

### Tier A — Redraft

Example:

- `milf`

Typical features:

- draft board
- pick controls
- teams
- available players
- pick tracker
- little or no keeper/control-rights logic

### Tier B — Keeper / Dynasty

Future example:

- another league with keeper/dynasty behavior but without the full MLF contract/QO/PT model

Typical features:

- draft board
- keeper logic
- roster continuity features
- no assumption of MLF-specific predraft-control model

### Tier C — Contract / Control Rights

Example:

- `mlf`

Typical features:

- contracts
- predraft rights / placeholders
- commissioner control tools
- more complex state restore / prefill logic
- richer auth/admin workflows

## 5.3 Architectural Implication

The app should eventually be configurable by:

- league identity
- profile truth
- feature flags / enabled modules
- rule model

Canonical rule:

```text
future reuse should be driven by league identity + feature profile
not by maintaining separate hard-forked app copies
```

---

# 6) Canonical Naming Rules

## 6.1 League Code

Each league gets a short stable code such as:

- `mlf`
- `milf`

This code should be the canonical internal short identifier for:

- folders
- env naming patterns
- deployment naming
- profile records
- future league-specific automation

## 6.2 Human Display Name

Human-facing names remain separate from code identifiers.

Do not use display names as code identifiers.

Canonical rule:

```text
code identifiers and human display names are different concerns
```

---

# 7) League Profile Strategy

## 7.1 Canonical League Profile Boundary

The main multi-league architecture seam is now the **canonical league profile**.

Canonical identity scope:

```text
(league_key, season_year)
```

Canonical rule:

```text
league behavior should be driven by canonical league profile truth
not by scattered hard-coded league assumptions
```

## 7.2 Profile Storage Strategy

Canonical direction:

- DB-backed active profile storage
- full-snapshot profile history
- YAML import/export for human-friendly editing

Canonical rule:

```text
DB is canonical source of truth
YAML is the human-editable/import-export artifact
history stores full snapshots
```

## 7.3 One Season Can Differ From Another

This architecture assumes:

- league configuration may change from season to season
- offseason votes may change next season’s profile
- profile truth is season-scoped, not just league-scoped

Canonical rule:

```text
each season may have its own canonical league profile
for the same league family
```

## 7.4 Supported v1 Profile Direction

Current recognized profile concepts include:

### Platforms
- `yahoo` = supported in v1
- others = recognized later / unsupported in v1

### Draft
- `draft.type = standard`
- `draft.type = auction` = recognized but unsupported in v1
- `draft.order_mode = straight | snake`

### Control / keeper model
- no keeper
- dynasty-style keeper
- contract-style keeper
- auction keeper = recognized but unsupported in v1

Canonical rule:

```text
known-but-unsupported profile options should warn/stop startup
not silently degrade
```

---

# 8) Current MLF vs MiLF Profile Direction

## 8.1 MLF Profile Direction

Current MLF profile direction includes:

- more complex predraft/control-rights behavior
- contract-style control model
- additional commissioner/admin workflows
- current live deployment already proven

## 8.2 MiLF Profile Direction

Current MiLF profile direction includes:

- simpler redraft behavior
- reduced control-rights complexity
- useful first proof of reuse outside MLF

## 8.3 Why MiLF Is the Best First Template-Proof Candidate

Canonical rule:

```text
milf is the best first proof that the core can run outside mlf
because its rule model is simpler
```

That makes MiLF the safest early test of reusable core boundaries.

---

# 9) Database Strategy

## 9.1 Current Direction

The current architecture direction supports multiple leagues without rewriting app code for each one.

## 9.2 Recommended Model

Recommended model:

- **one shared database**
- **shared schema**
- **strict league scoping**
- **season-aware profile/config**

Canonical rule:

```text
shared DB is acceptable only if all league-owned behavior and data access
are strictly scoped by league_key and season_year where applicable
```

## 9.3 Why This Model Fits Best

Pros:

- easier reuse
- shared tooling
- shared ingestion patterns
- simpler profile-driven architecture

Risk:

- league scoping bugs can leak across league boundaries if filters are missed

Canonical consequence:

```text
multi-league growth requires strict scoping discipline everywhere
```

---

# 10) Deployment Strategy

## 10.1 Current Canonical Truth

For the current live MLF stack:

- deployment SSOT = `mlf/runtime/docker-compose.yml`
- runtime env SSOT = `mlf/.env`

## 10.2 Near-Term Recommendation

Prefer **separate deployment per league first**.

Examples of future league-specific deploy surfaces might include:

- `mlf_draftboard`
- `milf_draftboard`

Canonical rule:

```text
prove reuse by standing up a second league deployment
before attempting one shared live deployment surface for all leagues
```

## 10.3 Why Separate Per-League Deployment First

Pros:

- clearer isolation
- simpler debugging
- easier rollback per league
- safer migration from current MLF reality

This is the safest path while deployment ownership is still league-local.

---

# 11) Current Migration State

## 11.1 What Is Already Locked In

The following are now effectively locked in as current architectural facts:

- portfolio root = `fantasy/`
- shared root = `fantasy/shared/`
- league roots = at least `fantasy/mlf/` and `fantasy/milf/`
- shared/docs exists
- mlf/docs exists
- milf/docs exists
- shared runtime extraction has begun in a proven form
- current live deployment ownership remains MLF-local

## 11.2 Current Structure Is No Longer Purely Hypothetical

The repo is no longer only at the “plan the target” stage.

It has already advanced into a **partially realized portfolio structure**.

Canonical rule:

```text
current architecture documentation must reflect executed structural moves,
not only future intentions
```

---

# 12) Migration Phases

## 12.1 Phase 1 — Classify and Reduce Drift

Goal:

- define the architecture
- classify current folders/files/scripts
- stop ad hoc root drift
- distinguish live runtime/deploy surfaces from archive sediment, generated outputs, and support artifacts

Status:

- substantially completed

## 12.2 Phase 2 — Stabilize Runtime and Deployment Truth

Goal:

- ensure env, compose, auth bridge, app entrypoints, and critical script paths are canonical
- eliminate drift in docs and deployment helpers
- verify that cleanup/moves are reflected in live bind-mounted runtime where needed

Status:

- substantially completed for the current live MLF stack

## 12.3 Phase 3 — Introduce Cleaner Shared and League Buckets

Goal:

- establish cleaner `shared/`, `mlf/`, and `milf/` responsibility boundaries
- move docs into the three-folder model
- move proven shared runtime helpers into `shared/runtime/`
- reduce top-level ambiguity without breaking live deployment

Status:

- partially completed and already realized in meaningful ways

## 12.4 Phase 4 — Extract Shared Framework Deliberately

Goal:

- separate shared app logic from MLF-specific logic
- identify feature/profile seams
- prepare MLF and MiLF to consume the same shared core more cleanly
- distinguish shared app/framework vs per-league config/deploy/data surfaces

Status:

- in progress conceptually, but not complete as a broad extraction phase

Rules:

```text
shared extraction should follow proven responsibility boundaries,
not folder aesthetics
```

## 12.5 Phase 5 — Stand Up and Prove the Second League

Goal:

- prove that the template/profile/shared-core direction works outside MLF
- validate that the architecture supports a second real league without cloning the whole app

Recommended first proof candidate:

- `milf`

---

# 13) Current No-Move / High-Risk Surface Rule

## 13.1 League-Local Live Deployment Surfaces Still Require Caution

Within current MLF live deployment ownership, the following remain high-risk live surfaces:

- `mlf/runtime/`
- `mlf/.env`
- `mlf/Dockerfile`
- `mlf/requirements.txt`

These should not be casually renamed or relocated without deliberate redesign and runtime proof.

## 13.2 Shared Live Runtime Surfaces Also Require Caution

The following shared live surface is now part of real runtime behavior:

- `shared/runtime/auth_bridge.py`

Canonical rule:

```text
do not move a live runtime surface casually
whether it is currently shared-owned or league-owned
```

## 13.3 Current Rule

Canonical rule:

```text
classify first
move or rename live runtime/deploy surfaces only after
deployment/import/runtime redesign is explicit and re-proven
```

---

# 14) Immediate Architecture Rule After This Document

The immediate deterministic architecture rule is now:

```text
lock current shared-vs-league boundaries in documentation
preserve identified live no-move surfaces
defer further high-risk runtime/deployment moves
until the next deliberate extraction phase
```

This means:

- shared canonicals should reflect the current three-folder doc structure
- league overlays should hold only real deviations
- current live deployment truth should remain explicitly documented as MLF-local
- future extraction should be design-driven, not exploratory cleanup

---

# 15) Current Canonical Conclusion

At this moment:

- `fantasy/` is the portfolio root
- `shared/` is the shared asset root
- `mlf/` is the current MLF league root
- `milf/` is the current MiLF league root
- current docs are split across `shared/docs`, `mlf/docs`, and `milf/docs`
- current live MLF deployment remains league-local
- shared extraction has begun in a proven form
- future reuse should be driven by shared canonicals, shared runtime responsibilities, league profiles, and separate per-league deployment first

Canonical rule:

```text
the portfolio is now past initial classification,
but not yet at the final extracted shared-framework end state
```