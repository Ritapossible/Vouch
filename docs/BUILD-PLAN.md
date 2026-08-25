# Build plan

> Vouch is the **second** of two contracts.
> [Recourse](https://github.com/Ritapossible/Recourse) is scheduled first — not because it is
> more useful, but because its demo is louder. Vouch's demo is a correct refusal, which is
> the right outcome and a quiet one.

---

## The demo

Two payment requests side by side.

> One names a real vendor with a live site and a payment address that appears on it. The
> other names a plausible, well-written, entirely **hallucinated** company — the kind a model
> invents when it cannot find a supplier.
>
> Fire both. **One payment clears. One is refused, and the refusal cites which check failed
> and quotes what it found.** No human in either path.

The second beat, if there is time for one, is the stronger technical point:

> A third request names a *real* vendor with a *substituted* payment address. The site is up,
> the company exists, the invoice is perfect. Vouch returns **`contradicted`** — not
> "unverified" — because the vendor's own page names a different address.
>
> That is invoice-redirection fraud, caught by a substring match, with no model involved.

Lead with the hallucinated vendor because it is legible in one sentence. Keep the
substituted-address case for the first technical question, because it is the one that shows
the architecture rather than the idea.

## Milestone 0 — Runtime facts (blocking)

Work [RUNTIME-FACTS](RUNTIME-FACTS.md), at minimum items 1–4. If Recourse is built first,
items 1–3 are already answered and this milestone is nearly free — which is a real argument
for that ordering beyond the demo.

**Item 4 (rendered-page fetching) is specific to Vouch and can change the product.** If the
primitive returns raw HTML only, a large fraction of legitimate vendor sites become
unreadable, and the integration guidance has to steer toward machine-readable sources before
anyone integrates. Answer it before writing the prompt builder.

## Milestone 1 — The deterministic engine

`lib/vouch_core.py`, `lib/vouch_evidence.py`. No SDK import, no network, no model.

- cache key canonicalization and hashing
- the stage-1 screen, in documented order
- `normalize()` with the confusable fold — **port DedupRegistry's map directly**, do not
  reimplement it
- the address-on-site check
- foreign-address extraction, tuned strict
- the three-valued aggregation
- verdict canonicalization — total, no raising path
- `verdicts_agree` with bucketed tolerance

**Exit condition:** the aggregation table is exhaustively tested, and there is a test whose
entire purpose is to try to make a failure produce `substantiated`. That test is the point of
this contract.

## Milestone 2 — The contract shell

`contracts/vouch.py` with the views, the lists, and the cache. `check()` raises "not
implemented" beyond a cache hit.

Set up the build split and `tests/test_contract_sync.py` now. The splice-vs-import trap is
invisible locally and only bites on-chain.

**Exit condition:** deploys to studionet, views answer from a live address, `genvm-lint`
passes, `tools/verify.py` writes `evidence/validation.json`.

## Milestone 3 — Corroborate

Add stage 2: fetch, normalize, address check, foreign-address extraction.

Fence fetched text here, before it has anywhere to go — fencing added after the prompt exists
is fencing that gets forgotten on one path.

**Exit condition:** **the whole demo works with no model in it.** Hallucinated vendor →
`unsubstantiated`. Substituted address → `contradicted`. Real vendor → `substantiated`.

This is the milestone worth noticing: Vouch is demoable, and arguably complete, before stage
3 exists. If everything after this slips, there is still a working product — and one whose
every result is deterministic.

## Milestone 4 — Substantiate

Add stage 3 for the fuzzy claims. One `exec_prompt`, canonicalization, bucketed tolerance,
and the rule that a model response can never yield `contradicted`.

**Exit condition:** `legal_name` and `service` resolve against a real page, and a test
confirms an injected accusation in page text cannot produce `contradicted`.

## Milestone 5 — Composition

Wire MandateVault in front, per [COMPOSITION](COMPOSITION.md). Demonstrate the sequence end
to end.

**Worth its own milestone** — two of your own deployed contracts composing in one demo is a
different credibility class from either alone, and it is the thing that makes the pair a
portfolio rather than two projects.

**Exit condition:** mandate denial short-circuits without a Vouch call; mandate approval plus
`contradicted` blocks; and there is a test proving a Vouch revert does **not** settle.

## Milestone 6 — Evidence and verification

Port `tools/verify.py` and `tools/verify_deployment.py` from DedupRegistry. Close the chain
*validated bytes → local file → deployed source*.

---

## Cut list, in order

1. **`registry_id` claim** — narrow, and the least-used.
2. **Foreign-address extraction** — drop `contradicted` to `unsubstantiated` and lose the
   second demo beat. Painful; survivable.
3. **The model stage entirely (Milestone 4)** — deterministic checks alone are a complete,
   demoable product. **This is the safety cut and it is worth knowing it exists early.**
4. **Multiple sources** — one source per check.
5. **Allowlist / denylist** — the cache alone is enough for a demo.

Never cut:

- fail-toward-`unsubstantiated` on every error path
- the three-valued verdict
- `method` on each claim result — a deterministic pass and a model pass at confidence 76 are
  different claims and a UI that conflates them is dishonest
- the consensus-verified / leader-observed split

---

## Open before deployment

Two things that should not reach a real deployment unresolved:

- **The claim schema.** Free-form dicts make cache keys fragile. See
  [DECISIONS](DECISIONS.md#claim-vocabulary-is-unfixed).
- **Cache-key grinding.** An attacker varying claim values can force unbounded fetch and
  inference cost. Deposit, permissioning, or per-payee rate limiting — unresolved. See
  [SECURITY](SECURITY.md#denial-of-service).

Neither blocks a demo. Both block anything paying for its own inference.
