# Minimum Sellable Product Specification

**Specification Date:** 2026-09-11
**Status:** IN PROGRESS — PRODUCT SCOPE FROZEN
**Parent Product Decision:** `cfa4530` — Commercial Product Decision Record
**Roadmap Transition:** `f69617d` — MSP specification active

---

## 1. Specification Purpose

This document freezes the Minimum Sellable Product implementation boundary for Fantasy Commissioner Support Tools.

The original Contract Keeper decision established the implementation foundation. This scope freeze broadens the sellable product while preserving Contract Keeper as the deepest supported league model.

This specification does not reopen product discovery. It converts the approved product direction into deterministic implementation requirements, supported configuration, workflows, UI surfaces, data-model boundaries, acceptance criteria, and pilot scope.

Where earlier product documents or sections conflict with the scope frozen below, this specification controls MSP implementation. League-specific assumptions must not silently become product rules.

---

## 2. MSP Decision 1 — First Sport

**Decision: Baseball is the first implementation and live outsider-pilot sport.**

Football is the second supported sport and must remain an architectural compatibility target from the beginning.

### 2.1 Baseball Implementation Principle

The first commercial baseball profile will be informed by the proven MLF contract and player-control implementation, but the commercial product must not be hard-coded to MLF.

MLF remains a league-specific reference implementation and validation source.

Commercial core behavior must be expressed through supported product capabilities and league-profile configuration rather than MLF-specific branches.

### 2.2 Initial Baseball Capability Boundary

The baseball MSP is expected to support the following bounded building blocks:

- configurable multi-year contracts;
- configurable restricted-rights mechanisms, with Qualifying Offer available as a league-defined label;
- optional Prospect Tag;
- optional Franchise Tag;
- traded draft-pick ownership;
- deterministic offseason workflow and phase validation;
- canonical franchise and player-control history;
- commissioner audit trail;
- Trade Lab;
- League Health.

Exact counts, durations, eligibility rules, precedence, phase order, valuation models, and UI behavior remain to be frozen in later sections of this specification.

### 2.3 Football Compatibility Boundary

The shared product architecture must not introduce baseball-only assumptions that would prevent later football or hockey profiles from supporting their own contract structures, restricted-rights rules, optional tags, draft behavior, and sport-specific valuation.

Baseball-first determines implementation order, not permanent product scope.

---

## 3. Product Scope Freeze — 2026-09-11

### 3.1 Product Definition

Fantasy Commissioner Support Tools is a multi-sport commissioner companion platform, not a fantasy scoring host.

It sits alongside Yahoo, ESPN, Sleeper, Fantrax, Fleaflicker, and other hosts and manages commissioner work those platforms leave manual or fragmented.

The host remains responsible for live scoring, standings, lineups, waivers/free agents, matchups, player news, and host-native communication.

The commercial core must work without a live provider API. Direct integrations are optional enhancements; structured import and Manual/Other remain valid fallbacks.

### 3.2 Independent League Dimensions

Create League and the canonical profile must model these independently:

- **Sport:** Baseball first; Football second; Hockey third.
- **Platform:** Yahoo, ESPN, Sleeper, Fantrax, Fleaflicker, Manual/Other.
- **League model:** Redraft, Keeper, Dynasty, Contract Keeper.
- **Draft method:** Snake, Straight/Linear, Auction, Offline/Custom.

Auction is a draft method, not a league model.

### 3.3 League-Model Scope

- **Redraft:** setup, franchises, draft order, DraftBoard, optional traded picks, history.
- **Keeper:** Redraft plus keeper declarations, keeper count/cost rules, eligibility, rollover.
- **Dynasty:** persistent roster control, future picks, annual/rookie drafts, rollover, ownership history.
- **Contract Keeper:** configurable contract-slot count, individual slot durations, assignment/expiration, offseason workflow, optional restricted rights/tags, player-control history, audit trail.

Contract Keeper remains the deepest and flagship ruleset, but commercial behavior must not be hard-coded to MLF, NFFL, or NFHL values or terminology.

Generic capabilities must support league-defined labels. For example, **restricted rights on expiring players** may be labeled Qualifying Offer, Right of First Refusal, Restricted Keeper, or another league term.

Configuration remains bounded; the MSP is not an arbitrary executable rules engine.

### 3.4 DraftBoard and Product Boundary

A commissioner-operated offline DraftBoard is core scope, including draft order, snake/straight sequencing, keeper-occupied picks, traded picks, commissioner-entered selections, availability, undo/correction, draft history, and auction price/budget recording when auction mode is enabled.

A fully synchronized multi-owner live draft service is not required for v1.

Trade Lab and League Health remain differentiators but must not block core workflow completion.

### 3.5 v1 Definition of Done

An outsider must be able to:

1. Create a league and choose sport, platform, league model, and draft method.
2. Configure relevant commissioner rules.
3. Import or manually initialize franchises, controlled players, and draft picks.
4. Run applicable keeper, dynasty, or contract offseason workflows.
5. Run an offline commissioner-operated draft.
6. Preserve canonical state and audit/history.
7. Roll the league into the next season.
8. See required commissioner actions from a dashboard.
9. Complete supported workflows without manual SQL edits, source changes, or hand-edited config files.

Out of scope for v1: scoring/standings, lineup submission, waivers/FAAB, player-news feeds, host-style matchup engines, general chat, arbitrary executable rule logic, every provider integration, and fully synchronized multi-owner live drafting.

### 3.6 Implementation Sequence

1. Product Foundation — branding, product shell, profile dimensions, Create League v2.
2. League Lifecycle — save/edit, franchises, history, canonical state, rollover.
3. Commissioner Rules — Keeper, Dynasty, Contract Keeper.
4. DraftBoard — generalized keeper/traded-pick/auction support.
5. Commissioner Intelligence — dashboard, Trade Lab, League Health.
6. Outsider Readiness — onboarding, imports, deployment, error handling, documentation, pilot.

Implementation proceeds in vertical slices: specify enough for the next slice, build/test it, then refine later detail from product feedback.
