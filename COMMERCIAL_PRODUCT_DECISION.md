# Commercial Product Decision Record

**Decision Date:** 2026-09-05  
**Status:** DECIDED FOR MINIMUM SELLABLE PRODUCT SPECIFICATION  
**Decision Gate:** Commercial Product Decision  
**Discovery Status:** Complete  
**MLF Cross-Validation:** CORROBORATED

---

## 1. Product Decision

The first commercial fantasy-sports product will be:

# Contract Keeper Commissioner

Contract Keeper Commissioner will be an opinionated companion application for
commissioners who want the strategic depth of multi-year player control
without running a salary-cap dynasty platform or maintaining complex manual
spreadsheets.

The product will have three major layers:

1. Contract Keeper Commissioner Core
2. Trade Lab
3. League Health

The Commissioner Core is the product.

Trade Lab and League Health are differentiators.

The product will not attempt to replace Yahoo, Sleeper, ESPN, or another
fantasy platform for weekly scoring, lineup management, waivers, standings,
or ordinary in-season gameplay.

---

## 2. Target Customer

The initial target customer is:

**The commissioner of an established fantasy league who wants more strategic
player control than a normal keeper league but less bookkeeping and
administrative burden than a salary-cap dynasty league.**

The strongest initial customer is a commissioner who:

- runs or wants to run a serious long-term fantasy league;
- has a relatively stable group of managers;
- wants contracts, qualifying offers, franchise-tag-style retention, and
  tradable draft picks;
- currently depends on spreadsheets, custom rules, commissioner memory,
  Discord messages, or other manual recordkeeping;
- wants to continue using an existing fantasy provider for weekly play;
- values league continuity, transparency, and competitive integrity;
- is willing to pay a modest league-level annual fee to reduce commissioner
  administration.

The initial buyer is the commissioner, not the individual fantasy manager.

---

## 3. Primary Customer Pain

The primary problem is not drafting.

The primary problem is:

**Running a sophisticated contract-keeper league creates a persistent
commissioner administration burden that ordinary fantasy platforms do not
model well.**

That burden includes:

- tracking multi-year player control;
- identifying expiring contracts;
- recording offseason decisions;
- administering qualifying offers;
- resolving retain, release, and poach outcomes;
- managing franchise-tag-style retention;
- tracking traded draft picks;
- maintaining stable franchise history across seasons;
- reconstructing offseason state correctly;
- preserving an audit trail;
- explaining rules and decisions to league members;
- understanding whether the control system is creating unhealthy
  competitive concentration.

The product should reduce that operational burden while preserving the
strategic depth that makes the league attractive.

---

## 4. Product Positioning

Primary positioning:

> **More roster strategy than keeper. More player movement than dynasty.
> Without salary-cap bookkeeping.**

Contract Keeper Commissioner allows an existing fantasy league to add
structured multi-year player control, qualifying offers, franchise-tag-style
retention, draft-pick trading, offseason workflows, and historical control
tracking while continuing to use its normal fantasy platform for scoring and
weekly play.

The product is not positioned as:

- a new fantasy scoring platform;
- a generic draft board;
- a salary-cap dynasty platform;
- a daily lineup optimizer;
- a generic commissioner website;
- an AI-first fantasy prediction subscription.

---

## 5. Decision Record Status

This record is complete.

Discovery is closed.

Remaining exact rule and implementation decisions are intentionally deferred
to the Minimum Sellable Product specification.

---

## 6. Product Configuration Philosophy

Contract Keeper Commissioner will be sport-aware and profile-driven.

The commercial product will not assume that one football or baseball league contract structure is universally correct.

The product will use bounded configuration rather than one mandatory ruleset.

Commissioners may configure supported contract-keeper building blocks within defined product boundaries.

The product will support sensible sport-specific templates while allowing commissioners to adjust supported parameters.

It will not become a generic rules engine for arbitrary fantasy-league logic.

Canonical principle:

    supported strategic building blocks + bounded commissioner configuration + sport-specific defaults

### 6.1 Sport Awareness

Baseball and football are the initial sports under consideration.

The product may implement or pilot one sport first, but the underlying control model should remain sport-aware rather than hard-coded to one league.

### 6.2 Recommended Templates

The product should provide recommended starting templates rather than one mandatory universal ruleset.

Examples may include a simpler football-oriented template and a deeper baseball-oriented template.

Exact template values will be defined in the Minimum Sellable Product specification.

## 7. Supported Player-Control Building Blocks

### 7.1 Contracts

Multi-year contracts are a core capability.

The commissioner may configure the supported number of annual contracts and their durations within bounded product limits.

### 7.2 Qualifying Offers

Qualifying Offer volume should be configured in relationship to the contract model rather than fixed universally.

Leagues with more controlled contracts may use more QO opportunities or tiers.

Exact supported QO counts and poaching behavior will be defined in the Minimum Sellable Product specification.

### 7.3 Franchise Tag

Franchise Tag is an optional supported feature that a commissioner may enable or disable.

### 7.4 Prospect Tag

Prospect Tag is an optional supported feature, expected to be especially relevant to baseball-style leagues but not mandatory for them.

### 7.5 Configuration Boundary

Commissioners may combine supported product capabilities, but configuration must remain bounded.

The normal onboarding model will not require one-off league code branches or arbitrary commissioner-authored rule logic.

---

## 8. Core Commissioner Workflows

The commercial product must support the complete commissioner operating cycle rather than only the draft itself.

### 8.1 League Setup

The commissioner must be able to:

- create a league and season;
- define or import franchises;
- establish stable franchise identities;
- select a supported sport/template;
- configure supported contract-control features;
- import or enter current controlled-player and draft-pick state;
- review and validate setup before activation.

### 8.2 Season Rollover

The commissioner must be able to create the next season while preserving franchise, contract, pick, and historical continuity.

Rollover must identify returning franchises, ownership changes, carried contracts, expirations, and unresolved control decisions.

Season rollover must not depend on display names or commissioner memory.

### 8.3 Offseason Decision Dashboard

The product must provide one authoritative commissioner view of offseason state.

It should show expiring contracts, remaining contracts, available contract opportunities, QO decisions, enabled tag decisions, unresolved items, completed items, and validation failures.

The goal is to make offseason administration deterministic and understandable without spreadsheet reconstruction.

### 8.4 Operational Product Principle

Contract Keeper Commissioner is an operational application, not merely a documentation, tracking, or recordkeeping tool.

For supported league rules, the software should calculate, enforce, advance, validate, and persist commissioner workflow state.

The commissioner should make discretionary league decisions, while the application handles the deterministic mechanics around those decisions.

Examples include:

- calculating contract carry-forward and expiration;
- determining player eligibility for contracts, QOs, Franchise Tags, and Prospect Tags;
- enforcing configured limits and blocking invalid states;
- resolving deterministic QO and poaching consequences;
- maintaining current player-control and draft-pick ownership;
- constructing predraft and draft state from completed offseason decisions;
- validating unresolved or inconsistent league state before phase advancement;
- preserving historical and audit evidence automatically.

Canonical operating model:

    league profile -> workflow engine -> canonical league state -> commissioner UI -> draft, trade, and League Health outputs

### 8.5 Contract Administration

The application must calculate contract carry-forward, years remaining, expiration, and available annual contract opportunities from the active league profile and canonical state.

When the commissioner assigns a contract, the application must validate player eligibility, contract availability, supported duration, and any conflicting control rights before accepting the decision.

Invalid contract states must be blocked or explicitly surfaced for commissioner resolution.

### 8.6 Qualifying Offer Administration

When QOs are enabled, the application must determine eligible players and available QO opportunities from the configured league model.

The workflow must present valid retain, release, and poach choices and enforce the configured protection and precedence rules.

Once a QO decision becomes locked, the application must calculate and persist the resulting player-control and draft consequences automatically.

### 8.7 Optional Tag Administration

Franchise Tag and Prospect Tag are optional league-profile capabilities.

When either feature is enabled, the application must calculate eligibility, enforce configured limits and reuse rules, prevent conflicting control assignments, and carry valid tag state into later offseason phases.

When a tag feature is disabled, its workflow and eligibility rules should not appear as active league requirements.

### 8.8 Phase Advancement

The application must know which offseason phase is active and which prerequisites are required before the commissioner advances the league.

Unresolved required decisions, invalid control state, or failed validation should prevent phase advancement until resolved or explicitly handled through an authorized commissioner override.

### 8.9 Draft-State Construction

The application must construct the predraft and draft state from canonical league, player-control, franchise, and pick-ownership truth.

Controlled players, completed QO outcomes, enabled tag outcomes, draft order, and current pick ownership must be reflected automatically rather than reconstructed manually by the commissioner.

The application must validate that required offseason phases are complete before the live draft is opened.

### 8.10 Draft-Pick Ownership

Draft-pick trading is a core supported capability.

The application must preserve the distinction between original draft-slot identity and current pick ownership.

A trade changes current ownership of a pick without rewriting the structural identity of the original draft slot.

Pick ownership must persist across reloads, offseason preparation, and draft-state reconstruction.

### 8.11 Commissioner Trade Recorder

The product must provide an operational trade workflow for league-control consequences even when the external fantasy provider executes the roster transaction.

A completed commissioner-recorded trade may transfer supported player-control rights, draft picks, or both.

The application must validate the transaction, calculate resulting ownership changes, persist canonical state, and preserve the transaction as historical evidence.

Trade recording and Trade Lab analysis are separate capabilities: Trade Lab evaluates a proposed trade, while the Trade Recorder changes canonical league state after an approved trade.

### 8.12 Audit and History

Material commissioner actions must leave inspectable historical evidence.

The system should be able to explain who controlled a player or pick, what mechanism created that control, when it changed, and what commissioner action produced the change.

Corrections and authorized overrides should preserve prior state or an equivalent audit record rather than silently erasing history.

---

## 9. Trade Lab

Trade Lab is a launch differentiator layered on top of canonical league and player-control state.

It evaluates proposed trades; it does not itself execute or approve them.

### 9.1 Fairness Versus Fit

Trade Lab must distinguish two different questions:

- Trade Fairness: whether the value exchanged is reasonably balanced;
- Trade Fit: whether the trade makes strategic sense for the specific franchises involved.

A trade may be reasonably fair while fitting one franchise better than another because competitive windows, roster needs, contracts, and draft assets differ.

### 9.2 Inputs

Trade Lab should be sport-aware and may use supported inputs such as player value or projections, position, contract duration, control rights, QO or tag implications, draft picks, roster needs, and competitive window.

Exact football and baseball valuation sources and pick-value models will be frozen in the Minimum Sellable Product specification.

### 9.3 Explainability

Trade Lab must explain the major reasons behind its result, including important player-value, contract-control, pick-value, and team-fit effects.

The initial product should favor deterministic and explainable reasoning over a black-box model trained on sparse historical league trades.

### 9.4 Decision Boundary

Trade Lab must not automatically approve, reject, or veto a trade.

Commissioners and managers retain judgment; Trade Lab provides structured decision support.

---

## 10. League Health

League Health is a launch differentiator that analyzes how competitively meaningful controlled assets are distributed, retained, and recirculated over time.

It is not a generic standings or parity dashboard.

### 10.1 Evidence-Based Focus

NFFL and MLF independently showed that raw controlled-player quantity was a weak competitive signal while controlled-player quality and persistent elite control were materially more associated with success.

Both leagues also showed that elite assets were materially more likely than ordinary assets to remain with the incumbent franchise after contractual endpoints.

League Health should therefore focus on the concentration, persistence, expiration, and recirculation of elite controlled assets.

### 10.2 Core Measures

The application should calculate supported measures such as:

- elite controlled-asset concentration by franchise;
- elite share by franchise;
- persistent elite control across seasons;
- elite retention into the next season;
- contract-expiration outcomes;
- same-franchise continuation;
- incumbent-control break;
- elite versus non-elite recirculation.

Exact sport-specific player-quality methodology and minimum historical-data requirements will be frozen in the Minimum Sellable Product specification.

### 10.3 Commissioner Interpretation

League Health should translate calculated measures into plain-language commissioner findings.

Examples include increasing elite concentration, repeated control of the strongest portfolios by the same franchises, healthy overall turnover but weak elite recirculation, or meaningful recirculation after expiration.

The application may flag patterns that warrant commissioner review, but it must not automatically declare a league unfair or prescribe a rule change.

### 10.4 Causal Boundary

League Health measures league structure and asset flow; it does not prove that contract rules alone cause competitive outcomes.

Manager skill, drafting, trades, injuries, prospect evaluation, waiver activity, and other factors remain relevant.

---

## 11. Provider Strategy

Contract Keeper Commissioner is a companion application rather than a replacement fantasy scoring platform.

Provider integrations should reduce setup and synchronization work, but the core commissioner workflow must not depend on a live provider API to remain usable.

### 11.1 Required Non-API Path

Essential league setup and commissioner workflows must have a supported fallback through structured import, CSV import, commissioner entry, or another deterministic setup process.

The product should remain commercially usable even when a provider integration is unavailable.

### 11.2 Yahoo

Yahoo is the best-proven provider integration in the existing software, but commercial use of the Yahoo API remains an unresolved external dependency.

The first commercial product therefore must not require Yahoo API access for its core operation.

Yahoo-assisted import or synchronization may be added when commercial use is confirmed to be acceptable.

### 11.3 Other Providers

Sleeper and other providers may be supported later through the same provider boundary.

The initial commercial product does not require simultaneous live integration with every fantasy platform.

## 12. Authentication and Permissions

Commissioner authority is league-scoped.

The product must distinguish site administration, league commissioner authority, and ordinary league-manager access.

Only authorized commissioner roles may perform mutations that change canonical contract, QO, tag, trade, pick, draft, or override state.

League-visible reports and commissioner-only controls must remain explicitly separated.

---

## 13. Deployment and League Isolation

The first commercial pilots should favor strong per-league isolation over premature multi-tenant complexity.

Each league must remain logically isolated in configuration, canonical state, permissions, history, and commissioner actions.

The underlying application architecture should still be league-scoped so later consolidation into a shared hosted service remains possible.

Pilot isolation is an implementation strategy, not a requirement that every future customer receive a permanently separate codebase.

## 14. Onboarding Model

Initial onboarding will be commissioner-focused and concierge-assisted while the product learns where setup remains confusing.

The target onboarding flow is:

1. commissioner identifies sport and selects a supported starting template;
2. commissioner configures supported contract, QO, tag, and draft options;
3. franchises are created or imported and mapped to stable internal identities;
4. current controlled players and draft-pick ownership are imported or entered;
5. the application validates the resulting league state and surfaces exceptions;
6. commissioner resolves exceptions and approves activation;
7. canonical history is maintained by the product from that point forward.

Concierge assistance is acceptable during pilots.

Permanent dependence on developer code edits, direct database changes, or custom per-league branches is not.

The MSP must make setup understandable enough that an outside commissioner can operate the league without continuous developer handholding.

---

## 15. Pricing Hypothesis

The initial pricing hypothesis is a league-level annual price rather than a per-manager subscription.

The current standard-price hypothesis is approximately $49 per league-season.

This is a validation hypothesis, not a permanently fixed price.

Concierge setup may carry a separate one-time fee when substantial historical import or configuration assistance is required.

The pricing test should measure value against commissioner time saved, administrative burden removed, rule clarity, preserved history, Trade Lab usefulness, and League Health usefulness.

## 16. Pilot and Validation Thresholds

Initial validation targets are:

- at least 10 commissioner reactions;
- at least 5 commissioners who clearly understand and value the problem;
- at least 5 serious prospects;
- at least 3 outsider pilot leagues;
- at least 2 paying leagues at $30 or more per league-season.

Pilot leagues must test the commissioner operating workflow rather than only the DraftBoard.

## 17. Success Criteria

The initial product is successful only if outsiders can operate it with limited ongoing support.

Success targets include:

- accurate season and franchise continuity;
- deterministic contract, QO, tag, trade, and pick state;
- understandable commissioner workflows;
- useful Trade Lab output;
- useful League Health output;
- average steady-state support below approximately 30 minutes per league;
- recurring direct cost below approximately 20 percent of revenue;
- no unresolved provider dependency that prevents normal operation;
- demonstrated willingness to pay.

Initial Year-1 hobby-business target:

- approximately 10 to 25 paid league-seasons;
- approximately $750 to $2,500 gross revenue;
- less than approximately $500 recurring operating cost;
- less than approximately 3 hours per month active-season support;
- incremental net return above approximately $30 per hour.

A result above approximately $2,500 annual net income at more than $50 per hour incremental effort would justify serious consideration of expansion.

---

## 18. Stop and Pivot Criteria

The commercial effort should pause, narrow, pivot, or stop if external validation shows that the product cannot produce attractive hobby-business economics without excessive support or customization.

Material warning conditions include:

- commissioners do not understand the product without extensive explanation;
- fewer than 5 serious prospects emerge from initial validation;
- fewer than 2 leagues will pay at least $30 per league-season;
- onboarding repeatedly requires developer intervention;
- steady-state support materially exceeds approximately 30 minutes per league;
- provider restrictions prevent reliable operation and non-API workflows are not attractive enough;
- customer pressure consistently requires custom code or an unrestricted rules engine;
- Trade Lab or League Health do not materially improve customer interest;
- recurring costs materially weaken the targeted economics;
- commissioner workload reduction is not meaningful.

If a pivot is required, prefer narrowing the product before broadening it.

## 19. Explicit Initial Non-Goals

The first sellable product will not attempt to:

- replace a fantasy provider for weekly scoring, lineups, standings, or waivers;
- become a salary-cap dynasty platform;
- support arbitrary commissioner-authored rule logic;
- support every historical keeper or dynasty variation;
- commercialize the DraftBoard as a standalone product;
- commercialize the current baseball or hockey roster-manager tools;
- commercialize NPB as part of this initial product;
- support every fantasy provider at launch;
- require Yahoo API access for core operation;
- automatically approve or veto trades;
- create custom per-league application branches as the normal operating model.

## 20. Remaining MSP Decisions

The following decisions remain intentionally open for the Minimum Sellable Product specification:

- which sport is implemented and piloted first;
- recommended football contract and QO template values;
- recommended baseball contract and QO template values;
- exact supported configuration limits;
- detailed QO protection and poaching precedence;
- Franchise Tag eligibility and reuse patterns;
- Prospect Tag eligibility rules;
- exact offseason phase order and lock rules;
- Trade Lab football and baseball valuation sources;
- Trade Lab draft-pick valuation models;
- League Health sport-specific player-quality methodology;
- League Health minimum historical-data requirements;
- first pilot import format and setup workflow;
- default visibility of Trade Lab and League Health outputs;
- final public product name and branding.

These are specification questions and do not reopen product discovery.

## 21. Product Decision Gate Closure

The Commercial Product Decision Gate is resolved.

Proceed with Contract Keeper Commissioner as an operational, sport-aware, profile-driven commissioner application with Trade Lab and League Health as launch differentiators.

The product will use bounded configuration, supported sport-specific templates, optional control features, deterministic workflow enforcement, stable franchise and pick identity, and provider-independent core operation.

The next required artifact is the Minimum Sellable Product specification.

No substantial commercial implementation should begin until that specification freezes exact workflows, supported configuration, data-model requirements, UI surfaces, acceptance criteria, and implementation boundaries.
