# Consensus model

> Specification. The block structure depends on SDK behaviour not yet confirmed — see
> [RUNTIME-FACTS](RUNTIME-FACTS.md).

## The rule

> **The leader's result is never trusted input.**

Validators re-run the whole computation — their own fetches, their own model call — and
compare field by field. Nothing is taken from the leader except the values being compared.

## What is compared

| Field | Comparison | Rationale |
|---|---|---|
| `verdict` | **exact** | The outcome. |
| `claims[].result` | **exact, in key order** | The verdict decomposed. |
| `claims[].method` | **exact** | A deterministic result and a model result are different claims; a validator that settled a claim deterministically while the leader used a model has not reproduced the same computation. |
| `claims[].confidence` | within `confidence_tol`, **only inside the same bucket** | See below. |
| `resolved_by` | **exact** | |
| `claims[].quote` | **not compared** | Validators quote different passages legitimately. |
| `source_digests`, `foreign_addresses` | **not compared** | Fetched bytes differ; see below. |

## Fetched bytes are not compared

Two validators fetching the same URL seconds apart routinely receive different bytes — CDN
edges, rotating banners, embedded timestamps, personalization. **Requiring byte agreement
would make consensus fail constantly on pages telling both validators the same thing.**

So the *result* is compared, not the bytes. Content drift matters only if it flips a claim's
result, which is exactly when settlement should halt.

**Consequence:** digests and quotes in an attestation are the leader's observation. They are
stored under `observed` and the API documents them as leader-observed.

### The address check is the stable one

Worth noting how much better the deterministic majority behaves here than the fuzzy
minority. "Does this address appear in the normalized text?" is a substring match over
normalized content — it is insensitive to almost everything that makes raw bytes differ, so
validators agree on it essentially always.

**The claim carrying the most weight is also the one consensus handles best.** That is not a
coincidence; it is the reason to push work into the deterministic stage wherever it will go,
rather than a happy accident of this particular design.

## The threshold discipline

Inherited as a rule from MandateVault, where getting it wrong got an earlier revision
rejected: `confidence` was compared *before* the `min_confidence` threshold, so a leader
reporting 74 and a leader reporting 94 were both "within tolerance" of an independently
computed 94 — and the contract stored opposite outcomes for the same request.

> **Tolerance applies only when both numbers already sit on the same side of the threshold.**
> It absorbs sampling spread among answers that already agree; it must never carry agreement
> across the line that decides an outcome.

Enforced twice, independently, so the invariant does not depend on one call site remembering:

1. **Canonicalization first.** A leader value is admitted only in canonical form — one the
   coercion leaves unchanged — so a sub-threshold confidence has already become
   `unsubstantiated` before any comparison happens.
2. **Bucketed tolerance.** `confidence_tol` applies only when both values sit in the same
   bucket. Across buckets there is no tolerance at all.

## `contradicted` is out of the model's reach

A model result can never produce `contradicted`. That value comes only from deterministic
observations — a denylist hit, or a different address found on the page.

This is a consensus property as much as a security one: deterministic observations agree
across validators trivially, so **the contract's strongest and most actionable output is
also its most reproducible.** The fuzzy stage can only substantiate or fail to substantiate,
and neither of those is an accusation.

## Reachability

A validator reaching **zero** sources votes to rotate rather than rule — it cannot check the
leader and ratifying it would be exactly the failure the design exists to prevent.

Partial reachability is tolerated. `sources_reachable` is compared as a count, not a set:
which sources a validator reached is a property of its network position, not of the
evidence.

## Cache reads are not nondeterministic

A stage-0 cache hit involves no network and no model. It is a storage read and a timestamp
comparison, settled deterministically like any ordinary contract call — **the majority of
production calls never open a nondet block at all**, which is worth stating plainly because
it means the consensus cost of this contract in steady state is close to that of a
conventional smart contract.

The timestamp comparison uses block time, not wall clock. Wall clock would differ per
validator and turn every cache read into a source of disagreement.

## Total canonicalization

The coercion function has **no raising path**. Every possible response — malformed, hostile,
truncated, wrong-typed, empty — produces a definite verdict.

Leader and validators must agree on the *coercion* as well as the content. A coercion that
raises on some inputs and not others can differ between nodes, and a contract whose
agreement depends on well-formed model responses halts the first time it does not get one.

## Open questions

- Whether a fetch and an `exec_prompt` can share a nondet block. If not, stages 2 and 3 are
  separate rounds, and the ruling round must either re-fetch (expensive, preserves the rule
  above) or carry forward the gather round's observation (cheap, but pipes a leader
  observation into a consensus input, which the rule forbids). **The most consequential
  unknown.**
- Whether the cache read and the verification round can be one call from the caller's
  perspective, or whether a cache miss must return and be retried.
