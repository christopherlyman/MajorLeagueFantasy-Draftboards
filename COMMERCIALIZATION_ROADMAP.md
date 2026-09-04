# Fantasy Software Commercialization Roadmap

## Purpose

This document defines the commercialization path for the fantasy-sports
software portfolio in this repository.

The objective is not to build a venture-scale company or to commercialize
every existing fantasy tool. The objective is to determine whether a focused
fantasy-league product can become a useful household financial asset with:

- high net income per hour;
- low ongoing support burden;
- low infrastructure and operating cost;
- manageable legal and data-provider risk;
- repeatable self-service use by commissioners outside the existing leagues.

Existing NFFL, MLF, NFHL, and roster-management software are source assets,
prototypes, evidence, and reusable components. They are not automatically the
commercial product.

## Provisional Product Thesis

The current leading opportunity is:

**Contract Keeper Commissioner Software**

Positioning:

> More roster strategy than keeper, more player movement than dynasty,
> without salary-cap bookkeeping.

The core product would help a commissioner operate a standardized contract
keeper league while leaving weekly scoring and ordinary roster management on
an established fantasy platform such as Yahoo or Sleeper.

Potential differentiators are:

1. **Trade Lab**
   - Trade Fairness: value exchanged.
   - Trade Fit: whether the trade fits each team's competitive window,
     contracts, draft capital, and roster strategy.

2. **League Health**
   - Competitive balance.
   - Elite-asset concentration.
   - Contract duration.
   - Expiration outcomes.
   - FT/QO behavior.
   - Asset recirculation and persistent competitive advantage.

These differentiators remain subject to the Product Decision Gate below.

## Commercialization Roadmap

The commercialization project has four major stages.

### Stage 1 - Evidence and Product Discovery

**Weight: 25% of overall commercialization**

Purpose:

- understand the strongest reusable assets already built;
- test the contract-keeper product thesis;
- inspect market alternatives and pricing;
- identify data-provider, legal, and operational constraints;
- determine whether Trade Lab and League Health represent credible
  differentiation;
- use NFFL and MLF as empirical evidence rather than assuming the current
  league implementations are commercially optimal.

Current approximate completion: **100%**

The evidence and product-discovery stage is complete.

MLF League Health cross-validation is **100% complete**.

All bounded MLF tasks were completed:

1. historical hitter/pitcher quality methodology was frozen;
2. the 2017-2025 controlled-asset quality dataset was built;
3. controlled quantity versus finish was tested;
4. controlled quality versus finish was tested;
5. persistent elite control versus finish was tested;
6. expiration, retention, FT, and recirculation outcomes were tested;
7. MLF findings were compared with NFFL;
8. the final MLF League Health classification was issued.

### MLF Research Stopping Rule

**Status: SATISFIED**

Final classification:

**CORROBORATED**

Historical MLF research is closed.

Additional source archaeology or model refinement is justified only if a
specific unresolved question could materially change the commercial product
decision.

The active workstream is now the Product Decision Gate and Commercial Product
Decision Record.

### Stage 2 - Commercial Product Decision and Specification

**Weight: 10% of overall commercialization**

Purpose:

Convert the discovery evidence into one explicit build decision.

The primary deliverable is a versioned **Commercial Product Decision Record**
that freezes:

- first target customer;
- primary customer pain;
- product positioning;
- standardized contract-keeper rules;
- required versus configurable rules;
- companion-platform strategy;
- provider/API dependence;
- Yahoo commercial-permission status;
- import/manual fallback strategy;
- minimum sellable feature set;
- Trade Lab scope;
- League Health scope;
- onboarding model;
- pricing hypothesis;
- pilot scope;
- success criteria;
- stop/pivot criteria.

Current approximate completion: **50%**

The supporting discovery research and MLF cross-validation are complete.

The active Stage 2 task is now to convert that evidence into the versioned
Commercial Product Decision Record. The stage remains at approximately 50%
until that decision is explicitly frozen.

### Stage 3 - Build the Minimum Sellable Product

**Weight: 35% of overall commercialization**

Purpose:

Build the smallest product that an outside commissioner can actually operate.

Provisional core workflow:

**League Setup / Import**
→ **Offseason Decisions**
→ **Contracts**
→ **QO / Poaching**
→ **FT**
→ **Draft**
→ **Trades / Picks**
→ **History / Audit**

Existing NFFL and shared DraftBoard components should be reused selectively,
but the commercial product must not simply expose league-specific NFFL
implementation details.

The initial product should favor one clear, opinionated contract-keeper format
over a large generic fantasy rules engine.

Current approximate completion: **8%**

Existing software provides useful source assets, but the commercial
minimum-sellable product has not yet been built.

### Stage 4 - Outsider Validation, Paid Experiment, and Economics Review

**Weight: 30% of overall commercialization**

Purpose:

Determine whether this is actually a business rather than merely useful
personal software.

Validation sequence:

**5 serious prospects**
→ **3 outsider pilots**
→ **2 paying leagues**
→ **support and economics review**

The important proof is that an outside commissioner can:

1. understand the league format;
2. set up or import a league;
3. operate the offseason and draft;
4. receive meaningful value from the software;
5. do so without extensive developer handholding;
6. willingly pay for continued use.

Current approximate completion: **0%**

## Overall Progress Model

Overall commercialization progress is weighted by the four stages rather than
by the amount of technical work completed.

Current baseline:

| Stage | Weight | Approx. Stage Completion | Approx. Earned Progress |
| --- | ---: | ---: | ---: |
| Evidence and Product Discovery | 25% | 100% | 25.0% |
| Product Decision and Specification | 10% | 50% | 5.0% |
| Minimum Sellable Product | 35% | 8% | 2.8% |
| Outsider Validation and Economics | 30% | 0% | 0.0% |
| **Overall Commercialization** | **100%** |  | **~33%** |

This percentage is deliberately conservative.

Working software on Apollo does not by itself represent commercial progress.
Commercial completion requires an outside commissioner to use the product
with little assistance and demonstrate willingness to pay.

Progress percentages should be recalibrated when the MVP scope materially
changes, rather than allowed to drift as research or implementation expands.

## Current NFFL League Health Evidence

NFFL provides the first empirical baseline.

The strongest current findings are:

- controlled-player quantity by itself did not meaningfully predict finish;
- controlled-player quality was substantially associated with stronger
  finishes;
- persistent elite-player control was associated with stronger finishes;
- elite players were materially more likely than non-elite players to remain
  controlled by the same franchise after reaching the expiration boundary.

The current working interpretation is:

**Competitive Balance**
→ **Asset Quality**
→ **Contract Duration**
→ **Expiration**
→ **FT / QO**
→ **Recirculation**

The appropriate conclusion is not that contracts inherently create dynasties
or make leagues unfair. Manager skill, drafting, injuries, and other factors
remain potential confounders.

## Completed MLF League Health Evidence

MLF provides the long-horizon, second-sport validation.

Across 144 franchise-seasons from 2017 through 2025:

- raw controlled quantity was weakly associated with finish;
- average controlled quality was materially associated with stronger finish;
- elite controlled-player count and elite share were materially associated
  with stronger finish;
- Top-6 franchises had higher average controlled quality in every season;
- persistent elite portfolios were materially associated with stronger
  finish;
- elite next-year same-franchise retention was 89.27%, versus 73.24% for
  non-elite assets;
- conditional elite-retention share was much more weakly associated with
  finish than persistent elite count;
- overall incumbent-control break at contractual endpoints was 71.34%;
- elite same-franchise continuation at expiration was 59.21%;
- non-elite same-franchise continuation at expiration was 20.75%;
- elite assets were approximately 2.85 times as likely as non-elite assets to
  remain with the incumbent after an endpoint.

Final MLF classification:

**CORROBORATED**

## Cross-League League Health Conclusion

NFFL and MLF independently support the same practical mechanism:

**Competitive Balance**
-> **Asset Quality**
-> **Contract Duration**
-> **Expiration**
-> **FT / QO**
-> **Recirculation**

The strongest common signal is not the number of controlled assets.

It is the concentration, persistence, and recirculation behavior of elite
controlled assets.

This supports League Health as a credible product differentiator, with
commissioner-facing measures centered on:

- elite-asset concentration;
- persistent elite control;
- elite retention;
- elite expiration outcomes;
- incumbent-control break;
- recirculation of competitively meaningful assets.

The evidence is observational and does not establish that contract rules
alone cause competitive imbalance.

## Product Decision Gate

The MLF League Health conclusion is complete and discovery is closed.

A Commercial Product Decision Record must now be written.

The decision should answer whether the first commercial product is:

**Contract Keeper Commissioner Software with Trade Lab and League Health
differentiators**

or whether evidence supports a narrower or different first product.

No substantial commercial implementation should begin before this gate is
resolved.

## Commercial Validation Thresholds

Initial validation targets:

- at least 10 commissioner reactions;
- at least 5 commissioners who clearly understand the problem and product;
- at least 5 serious prospects;
- at least 3 outsider pilot leagues;
- at least 2 paying leagues at $30 or more per league-season;
- outsider operation without continuous developer handholding;
- average steady-state support below approximately 30 minutes per league;
- recurring direct cost below approximately 20% of revenue;
- no unresolved material API/data-provider issue.

Initial hobby-business economics target:

- approximately 10-25 paid league-seasons in Year 1;
- approximately $750-$2,500 gross revenue;
- less than approximately $500 recurring operating cost;
- less than approximately 3 hours/month active-season support;
- incremental net return above approximately $30/hour.

A result above approximately $2,500 annual net income at more than $50/hour
incremental effort would justify serious consideration of expansion.

## Explicit Non-Goals and Deferred Scope

Unless later evidence changes the decision, the first commercial product
should not attempt to:

- replace Yahoo, Sleeper, or another platform for weekly scoring;
- become a generic fantasy draft board;
- become a generic roster-management subscription;
- support every historical NFFL or MLF custom rule;
- build a broad salary-cap dynasty platform;
- build native mobile applications;
- support every sport at launch;
- require a large projection platform before customer validation;
- expose Apollo or internal infrastructure to customers;
- depend on undocumented commercial use of a provider API.

NPB commercialization remains parked because of its larger activation,
localization, legal, and data burden.

Generic DraftBoard commercialization remains a no-go as a standalone product.

Existing baseball and hockey roster managers remain personal R&D assets unless
future evidence materially changes their commercial attractiveness.

## Next Milestone

The next milestone is:

**Commercial Product Decision Record**

That decision will freeze the first commercial product, target customer,
positioning, standardized rules, minimum sellable feature set, Trade Lab
scope, League Health scope, onboarding model, pricing hypothesis, pilot
scope, success criteria, and stop/pivot criteria.

After that milestone:

**Minimum Sellable Product specification**
→ **build**
→ **outsider pilots**
→ **paid experiment**
→ **economics review**
→ **keep / expand / pivot / stop**
