# Architecture

> Specification. Nothing here is implemented.

## The organizing idea

Vouch answers a question that is **mostly deterministic and slightly fuzzy**, and the
architecture exists to keep those two parts apart — so the deterministic majority never pays
inference costs, and the fuzzy minority is clearly labelled as the weaker claim it is.

```
     free ─────────────────────────────────────────────────▶ expensive

   ┌─────────┐   ┌──────────┐   ┌──────────────┐   ┌───────────────┐
   │ 0 CACHE │──▶│ 1 SCREEN │──▶│ 2 CORROBORATE│──▶│ 3 SUBSTANTIATE│
   │ storage │   │  math    │   │   network    │   │      LLM      │
   └─────────┘   └──────────┘   └──────────────┘   └───────────────┘
        │             │                │
        │             │                └─▶ the address-on-site check settles
        │             │                    most real cases here, deterministically
        │             └─▶ denylist / malformed: rejected free
        └─▶ a known counterparty returns here, at zero cost
```

The stage that carries the most value is **2**, not 3 — which is unusual, and is the point.

---

## Stage 0 — Cache

Key: `hash(payee ‖ canonical(claims))`.

A hit within `cache_ttl` returns the stored attestation immediately. No network, no model,
no consensus round beyond the storage read.

Two properties the key must have, and both are load-bearing:

- **Claims are canonicalized before hashing** — sorted keys, normalized values — or
  semantically identical claim sets produce different keys and the cache never hits.
- **Any change to claims is a new key.** A vendor changing their payment address is a claims
  change, so *the check that matters most cannot be served stale.* This is the single most
  important property of the cache design, and it is why the payment address belongs in the
  claims rather than alongside them.

`cache_ttl` is a constructor parameter because the right value is entirely deployment
dependent: a payroll run wants days, a marketplace paying new counterparties wants hours.

## Stage 1 — Screen

Pure storage reads and integer comparisons. Ordered:

1. The payee address is well-formed.
2. The payee is not on the denylist → `contradicted`, immediately, at zero cost.
3. The payee is on the allowlist → `substantiated`, immediately, at zero cost.
4. `claims` is non-empty and every claim key is recognized.
5. `sources` is non-empty, within `max_sources`, and every URL passes validation (https, no
   userinfo — see [SECURITY](SECURITY.md#url-validation)).

The allowlist deserves a note: it is an escape hatch for counterparties an operator has
verified by means the contract cannot (a signed contract, a bank relationship, a person who
went there). Removing it would be purism — the operator has better information than the
contract in exactly those cases, and forcing a fetch would produce a worse answer at a
higher cost.

## Stage 2 — Corroborate

Fetch each source, normalize, and run the deterministic checks.

```
raw bytes → decode → strip scripts/styles → collapse whitespace
          → NFKC → confusable fold → casefold
```

The confusable fold matters more here than anywhere else in either contract. An attacker
publishing a payment address with a Cyrillic lookalike character would pass a naive
substring check against the real address while displaying something a human reads as
correct — or, worse, fail the check while looking right, producing an `unsubstantiated` that
an operator waves through. DedupRegistry's confusable map exists for the same reason and
should be reused directly.

### The address-on-site check

The core of the contract:

> Does the payment address appear in the normalized text of the fetched source?

A substring match. Deterministic, free beyond the fetch, and it settles the two failure
modes that motivate the whole design. Result per source, aggregated:

| Observation | Result |
|---|---|
| Address appears on at least one source | `substantiated` |
| Sources reachable, address absent, **a different address present** | `contradicted` |
| Sources reachable, address absent, no address present at all | `unsubstantiated` |
| No source reachable | `unsubstantiated` |

The middle two rows are the distinction the whole three-valued design exists to preserve.
"The site names a different address" is an alarm. "The site names no address" is a shrug.

### Address extraction

To distinguish those two rows the contract must recognize *some other* address on the page.
That is a format-level pattern match — chain address shapes are regular and recognizable —
not a semantic judgment, so it stays in the deterministic stage. Extraction being imprecise
is acceptable in one direction only: a missed foreign address degrades `contradicted` to
`unsubstantiated`, which is the safe direction. A *false* extraction that manufactures a
`contradicted` is not safe, so the pattern is tuned strict.

## Stage 3 — Substantiate

Only for claims stage 2 cannot settle — typically `legal_name` and `service`, the ones with
no deterministic form.

**At most one `exec_prompt`**, covering all remaining claims at once. Response is JSON, put
through a **total** canonicalization function with no raising path.

Coercion rules:

| Field | Rule |
|---|---|
| claim key | must be in the request's own claim set; unknown keys discarded |
| result | coerced to the three-valued enum; anything unrecognized → `unsubstantiated` |
| confidence | clamped 0–100; below `min_confidence` → `unsubstantiated`, never `contradicted` |
| quote | truncated, recorded leader-observed, never compared |
| missing claim | any requested claim absent from the response → `unsubstantiated` |

**A model result can never produce `contradicted` on its own.** That value is reserved for
deterministic observations: a denylist hit, or a different address found on the page. The
model can substantiate or fail to substantiate; it cannot accuse. This keeps the strongest,
most actionable output of the contract entirely out of reach of a hallucination or an
injection.

## Aggregation

The overall verdict is derived deterministically from the per-claim results:

```
any claim contradicted                          → contradicted
all claims substantiated                        → substantiated
otherwise                                       → unsubstantiated
```

**The model never produces the verdict**, only per-claim readings. Same discipline as
Recourse and for the same reason: the model is asked to perceive, never to decide, so
injected text has no lever to pull because no lever appears in the prompt.

---

## Storage model

| Structure | Contents |
|---|---|
| `attestations` | key → `{verdict, claims[], observed, checked_at, resolved_by}` |
| `denylist` | payee → bool |
| `allowlist` | payee → bool |
| `owner` | set at construction |

Attestations are **append-only per key**: a re-check writes a new record rather than
mutating the old one, so the history of what was believed and when is preserved. A payee
that was `substantiated` in March and `contradicted` in June is a story worth being able to
read.

## Cost model

Per `check()`:

| Path | Fetches | LLM calls |
|---|---|---|
| Cache hit | 0 | 0 |
| Denylist / allowlist hit | 0 | 0 |
| Address corroborated, no fuzzy claims requested | ≤ `max_sources` | **0** |
| Full check | ≤ `max_sources` | **1** |

Claim count does not appear: all remaining claims share one prompt. Attestation count does
not appear either — unlike DedupRegistry, there is no corpus scan; a check looks at one
payee's own sources.

**The expected steady state is the first row.** A deployment paying recurring counterparties
converges on approximately zero marginal cost, which is what makes per-payment verification
viable at all.

## Open architectural questions

1. **Should `claims` be a fixed schema rather than a free-form dict?** Almost certainly yes
   — a free-form dict makes cache keys fragile and prompts inconsistent. Not yet specified.
   See [DECISIONS](DECISIONS.md#claim-vocabulary-is-unfixed).
2. **Can a fetch and an `exec_prompt` share one nondet block?** If not, stages 2 and 3 are
   separate rounds. See [RUNTIME-FACTS](RUNTIME-FACTS.md).
3. **Should `contradicted` auto-populate the denylist?** Tempting and dangerous — a
   transient site defacement would permanently blacklist a legitimate vendor. Currently no.
4. **Where does revocation live** — a contract-level list, or the caller's policy layer?
   Open.
