# Vouch

**A pre-payment reality check: an agent cannot pay a counterparty that does not
demonstrably exist.**

> Before an autonomous agent sends money, the chain checks that the payee is a real, live,
> matching entity — and refuses to release the payment if it cannot show that.

---

> ### Status: deployed and verified on two networks
>
> | Network | Address | Artifact |
> |---|---|---|
> | Studionet | `0x42AA00A139652737285d70f3a4Fda32b478eac98` | `contracts/vouch.py` |
> | Testnet Bradbury | `0x91d27530546ABa4886cA9A93D80DB4C8B16EB156` | `dist/vouch.min.py` |
>
> All three demo cases are confirmed live on **both** networks, each transaction
> ACCEPTED, each verdict decided by deterministic code with no model involved.
> 369 tests pass. See [Status and known gaps](#status-and-known-gaps).

---

## Table of contents

- [The claim](#the-claim)
- [The problem](#the-problem)
- [Why this has to be an Intelligent Contract](#why-this-has-to-be-an-intelligent-contract)
- ["Isn't this just a KYB API?"](#isnt-this-just-a-kyb-api)
- [How it works: four stages, cheapest first](#how-it-works-four-stages-cheapest-first)
- [The deterministic check that does most of the work](#the-deterministic-check-that-does-most-of-the-work)
- [Three values, never two](#three-values-never-two)
- [Vouch does not say "safe"](#vouch-does-not-say-safe)
- [Composition with MandateVault](#composition-with-mandatevault)
- [The cache is what makes it economic](#the-cache-is-what-makes-it-economic)
- [Use cases](#use-cases)
- [Where it is not a fit](#where-it-is-not-a-fit)
- [Integration sketch](#integration-sketch)
- [Documentation](#documentation)
- [Planned layout](#planned-layout)
- [Status and known gaps](#status-and-known-gaps)

---

## The claim

> **There is no code path by which the agent pays an entity it invented.**

Stated as an absence, like every claim worth making about a safety property. "We check
vendors carefully" is a promise. "The payment cannot be constructed without a
substantiation record" is a structure.

## The problem

By 2026 agents hold payment authority routinely, and two failure modes have arrived with
them:

**1. Invented counterparties.** A model asked to source a supplier and unable to find one
does not reliably say so. It produces a plausible name, a plausible domain, and a plausible
invoice. Everything downstream — the mandate check, the approval workflow, the accounting
entry — processes it perfectly, because every one of those systems validates *form* and
none validates *existence*.

**2. Redirected payment details.** The oldest fraud in commerce, now automated. The vendor
is real; the payment address is not theirs. Nothing in a conventional payment path notices,
because the address is well-formed and the vendor is legitimate.

Both are the same gap: **agent payment infrastructure validates that a payment is
well-formed and permitted, and never validates that the recipient is who the payment thinks
it is.** Human commerce closes this with relationships, invoices matched against known
suppliers, and a person who would notice. None of those survive contact with an agent
transacting at machine speed with a counterparty it found four seconds ago.

## Why this has to be an Intelligent Contract

The removal test:

| Remove | What breaks |
|---|---|
| **The web inside consensus** | Existence is a fact about the live internet. There is nothing on-chain to check it against. |
| **The model inside consensus** | Some of the question is fuzzy — does this site actually represent this entity offering this service? Bytes cannot answer that. |
| **Consensus itself** | A verdict that only the agent's owner can see is worthless to the vendor, and a verdict only the vendor can see is worthless to the owner. Three mutually distrusting parties need the same answer. |

But note what makes Vouch structurally different from a "check the vendor" service, and it
is the load-bearing point:

> **The check is *in* the payment path, not beside it.**

An off-chain verification step is a step a compromised agent skips. A contract that must
produce a substantiation record before settlement can proceed is not a step — it is the
path. Apply the standard test: *if the agent were fully compromised, what would stop it?*
If the answer is "our verification service", the answer is nothing.

## "Isn't this just a KYB API?"

The first question any sharp reviewer asks, and the idea lives or dies on the answer. Four
differences, in descending order of importance:

**1. It is in the payment path.** A KYB API sits beside the payment, so a compromised or
buggy agent skips it and nothing notices. Vouch produces a record that settlement requires.

**2. Three parties get the same answer.** A KYB provider tells its customer. The vendor
never sees the assessment that rejected them and cannot contest it; the payment rail has
only the customer's word. A consensus verdict is one artifact all three read.

**3. The vendor can contest it.** An API's answer is unappealable because it is a private
opinion. An on-chain verdict with cited evidence is a claim with a shape — it names which
check failed and what it found, so a vendor whose site was briefly down can point at that
and re-run.

**4. The reasoning is auditable, not just the score.** Vouch returns per-claim results with
the passage each relied on. A KYB API returns a risk score whose derivation is the
provider's product and therefore not disclosed.

There is a fifth, weaker point worth *not* overstating: Vouch has no incentive to
over-reject to protect itself. That is true and it is not much of an argument, because Vouch
also has no liability, which is part of what a KYB provider is actually selling. Say the
four; leave the fifth.

## How it works: four stages, cheapest first

The discipline inherited from
[DedupRegistry](https://github.com/Ritapossible/GenLayer-Dedup-Registry): never reach for an
expensive tool until deterministic code has proven it necessary.

| Stage | Method | Cost | Network? | LLM? |
|-------|--------|------|----------|------|
| 0. **Cache** | prior attestation within TTL | O(1) storage read | no | no |
| 1. **Screen** | address validity, allow/denylist, URL validation | O(1) | no | no |
| 2. **Corroborate** | fetch sources; **does the payment address appear on them?** | ≤ `max_sources` fetches | **yes** | no |
| 3. **Substantiate** | the fuzzy claims only | ≤ 1 call | no | **yes** |

Most calls never reach stage 3, and a large fraction never reach stage 2 — see
[the cache](#the-cache-is-what-makes-it-economic).

## The deterministic check that does most of the work

**The highest-value check in this contract uses no model at all.**

> Does the payment address literally appear in the normalized text of the vendor's own
> published page?

That is a substring match. It costs nothing beyond the fetch, it is perfectly
deterministic, every validator agrees on it trivially, and **it defeats payment-redirection
fraud outright** — the second of the two failure modes above, and the one that moves the
most money in practice.

It is worth being clear about how much this single check carries. An agent paying an
address that appears nowhere on the vendor's site is the overwhelming shape of both a
hallucinated counterparty and a redirected invoice. The model is not needed to notice that,
and a design that reached for one first would be paying inference costs to answer a question
`in` already answers.

The model's job is only the part that genuinely resists a substring match: *does this site
plausibly represent the claimed entity offering the claimed service?* That is the weakest,
most subjective claim in the set, it is reported separately, and **it is never sufficient on
its own** — see [Three values, never two](#three-values-never-two).

## Three values, never two

Every claim resolves to one of three, and collapsing them to two is the failure mode that
would make this contract dangerous:

| Value | Meaning |
|---|---|
| `substantiated` | The evidence positively supports the claim. |
| `unsubstantiated` | The evidence does not support it. **Not the same as false.** |
| `contradicted` | The evidence positively conflicts with it. |

An unreachable site is `unsubstantiated`, never `contradicted`. A site that is up and names
a *different* payment address is `contradicted`, which is a far stronger and more
actionable signal. Flattening those two into "failed" throws away the distinction between
"we could not check" and "we checked and it is wrong" — and those call for completely
different responses from the caller.

Symmetrically: `unsubstantiated` must never quietly become `substantiated` on a fetch
failure. That is the fail-open bug this whole design exists to prevent, and it is the first
thing the test suite should try to cause.

## Vouch does not say "safe"

**Existence is not trustworthiness**, and the contract is scoped to the first.

Vouch answers: *is this entity real, live, and does the evidence support these specific
claims about it?* It does not answer whether the vendor delivers, whether the price is fair,
whether they will still exist next month, or whether they are a front. A real company can
defraud you, and Vouch will substantiate it correctly right up until it does.

This scoping is deliberate and it should stay in every user-facing surface. The value is in
answering a narrow question *reliably* rather than a broad question *approximately* — and a
contract that returned a trust score would be making exactly the unfalsifiable claim its
architecture cannot support. DedupRegistry takes the same line about its anti-Sybil
usefulness: **one signal among several, stated plainly.**

## Composition with MandateVault

Vouch is the second half of a payment guard whose first half already exists.

| | [MandateVault](https://github.com/Ritapossible/GenLayer-Mandate-Vault) | Vouch |
|---|---|---|
| Bounds | **what** the agent may buy | **whom** the agent may pay |
| Question | does this purpose fall under the mandate? | does this counterparty actually exist? |
| Fails on | a purchase outside the mandate's clauses | a payee the evidence does not substantiate |
| Cost | ≤ 1 LLM call | ≤ 1 LLM call, often 0 |

Run in sequence, the pair answers the standard question — *if the agent were fully
compromised, what would stop it?* — with: **it can neither buy the wrong thing nor pay a
fake entity, and it cannot skip either check, because the checks are the payment path
rather than a wrapper around it.**

Order matters: **MandateVault first.** It is cheaper — it may deny with zero network
fetches, and there is no point substantiating a counterparty for a purchase that was never
permitted.

Full sequence, failure modes, and the partial-failure cases in
[`docs/COMPOSITION.md`](docs/COMPOSITION.md).

## The cache is what makes it economic

A TTL cache keyed by `(payee, claims digest)`.

Verify a counterparty once; reuse it until the TTL expires or the claims change. **The
marginal cost of the thousandth payment to a known vendor is zero fetches and zero
inference.**

Without this, a per-payment verification contract is unusable at agent transaction
frequencies — which is most of why "just call a KYB API on every payment" is not a real
alternative either. The cache is not an optimization bolted on afterwards; it is the reason
the design is affordable at all, and it is why the TTL is a constructor parameter that
deployments are expected to tune rather than a constant.

Any change to the claims produces a different key and forces re-verification. A vendor
changing their payment address is a claims change, so the check that matters most cannot be
served stale.

## Use cases

### Agent procurement
The motivating case. An agent sources a supplier and pays. Vouch sits between those and
refuses the second when the first produced something that does not exist.

### Invoice payment
An invoice arrives naming a known vendor and an address. The address-on-site check settles
it: an address appearing nowhere on the vendor's published page does not get paid. This is
the highest-value, lowest-cost path in the contract.

### Agent-to-agent commerce
Two agents transacting with no human on either side and no shared registry. There is no
existing mechanism here at all — not a weak one, none — and the amounts are far too small
for any human process to be worth invoking.

### Payroll and contractor disbursement
Bulk payouts where one substituted address is invisible in a batch. Per-payee
substantiation with a cache makes recurring payouts cheap after the first cycle.

### Grant and treasury disbursement
A DAO paying an external party. The verdict is on-chain and citable, so the treasury's
diligence is a public artifact rather than a claim in a forum post.

### Marketplace and directory hygiene
Substantiating listed vendors on a schedule rather than per-payment, with `contradicted`
results promoted for review. Different call pattern, same contract.

## Where it is not a fit

- **Private or credentialed evidence.** Every validator fetches independently; anything
  behind authentication is invisible, and giving the contract credentials would put them in
  every validator's hands.
- **Entities with no web presence.** A real business with no site is `unsubstantiated`, and
  that is the correct answer to the question asked — but it means Vouch is a poor gate for
  populations where that is common. Know your payee distribution before making it blocking.
- **Trust, quality, or solvency.** Out of scope by design. See
  [Vouch does not say "safe"](#vouch-does-not-say-safe).
- **Sanctions and regulatory screening.** Adjacent and deliberately not attempted.
  Regulated screening has legal requirements a consensus verdict does not satisfy.
- **Sub-second settlement.** Uncached checks run fetches and possibly a model call inside a
  consensus round. Cached checks are fast; the first one is not.
- **Sites that block datacenter traffic.** Validators fetch from infrastructure, not
  browsers. Some legitimate vendors will be unreachable, and they will be
  `unsubstantiated` — correctly, and unhelpfully.

## Integration sketch

```python
# 1. Mandate first — cheaper, and may deny with zero network cost.
allowed = mandate_vault.request(payee=payee, amount=amount, memo=memo)
if allowed["outcome"] != "approved":
    return refuse(allowed["reason"])

# 2. Then substantiate the counterparty.
att = vouch.check(
    payee=payee,
    claims={
        "legal_name": "Example Compute Ltd",
        "domain": "example-compute.com",
        "service": "GPU rental",
    },
    sources=["https://example-compute.com/contact"],
)

# 3. The caller decides policy. Vouch never decides for you.
if att["verdict"] == "contradicted":
    return block_and_alert(att)            # the site names a different address
if att["verdict"] == "unsubstantiated":
    return hold_for_review(att)            # could not check — not the same as wrong
settle(payee, amount)
```

Note the two branches for the two non-passing values. A caller that collapses them into one
`else` has thrown away the contract's most useful distinction — and if that is going to
happen anyway, the honest response is to say so in the integration rather than let the
three-valued return imply a rigour the caller does not apply.

## Documentation

| Document | What is in it |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | The four stages, storage, cache design, cost model |
| [`docs/API.md`](docs/API.md) | Every method, parameter, return shape, error code |
| [`docs/COMPOSITION.md`](docs/COMPOSITION.md) | The MandateVault pairing — sequence, ordering, partial failure |
| [`docs/CONSENSUS.md`](docs/CONSENSUS.md) | What validators compare, and why fetched bytes are not it |
| [`docs/SECURITY.md`](docs/SECURITY.md) | Threat model, injection, and the fail-open bug this design exists to prevent |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Decisions worth defending and what each cost |
| [`docs/RUNTIME-FACTS.md`](docs/RUNTIME-FACTS.md) | SDK behaviours to confirm before implementation |
| [`docs/BUILD-PLAN.md`](docs/BUILD-PLAN.md) | Milestones, the demo, the cut list |

## Planned layout

```
contracts/
  vouch.py              THE CONTRACT          <- the only contract; deploy this
lib/
  vouch_core.py         screen, cache keys, verdict coercion
  vouch_evidence.py     fetch, normalize, address corroboration
  vouch_prompts.py      prompt builder
tools/
  build_contract.py     regenerates the contract from lib/
  verify.py             proves one contract, lint-clean; writes evidence/
  verify_deployment.py  proves a deployed address holds exactly this source
tests/
  test_vouch_core.py
  test_evidence.py
  test_contract_sync.py
evidence/
  validation.json
  deployment.json
docs/
  …
```

**GenLayer deploys exactly one file.** `genlayer deploy --contract` reads a single path and
does no module bundling, so a local `import vouch_core` resolves on a dev box and fails
on-chain — invisibly to every check that runs with the repo on `sys.path`. The build step
splices `lib/` into marked regions of the contract and a sync test fails on drift. Inherited
from DedupRegistry, where the trap is documented in full.

## Status and known gaps

Written before the build so it would be filled in honestly rather than after the fact.
It has been.

**Done:**

- [x] Contract built, deployed and verified on studionet and testnet-bradbury.
- [x] 369 tests. `genvm-lint` validates the contract: 8 methods, 5 views, 3 writes,
      and a schema matching [`docs/API.md`](docs/API.md).
- [x] All three demo cases confirmed live on both networks -- `substantiated`,
      `unsubstantiated` and `contradicted`, three different answers, no model involved.
- [x] Every blocking runtime fact answered against SDK source or a live node, with the
      source recorded -- see [`docs/RUNTIME-FACTS.md`](docs/RUNTIME-FACTS.md). Item 4
      landed favourably: `web.render` exists, so JavaScript-rendered vendor pages are
      readable and the fallback guidance the build plan owed is not owed.
- [x] Fetch latency inside a consensus round measured, and it changed the contract --
      `RENDER_WAIT` is `0ms` because a one-second wait timed validators out on bradbury.

**Exercised on a live network, not only in tests.** Every claim type, every
resolution path and every verdict has run on studionet and been read back out of
contract storage:

| Path | On-chain result |
|---|---|
| `payment_address` on the page | `substantiated`, deterministic, confidence 100 |
| `payment_address` absent, page unreachable | `unsubstantiated`, `sources_reachable: 0` |
| `payment_address` absent, page names others | `contradicted`, deterministic |
| `domain` | `substantiated`, deterministic |
| `registry_id` literal on page | `substantiated`, deterministic |
| `legal_name` / `service` | resolved by **model**, with a quote recorded under `observed` |
| allowlisted payee | `substantiated`, `resolved_by: list`, zero fetches |
| denylisted payee | `contradicted`, `resolved_by: list`, zero fetches |
| cache hit within TTL | `resolved_by: cache` |
| unknown claim key | rejected `[EXPECTED] UNKNOWN_CLAIM` |

The model stage is the one worth singling out. Mixing a deterministic claim and
two model claims in one call returned `payment_address` substantiated at 100,
`service` substantiated at 95 with the passage it relied on, and `legal_name`
unsubstantiated -- and the transaction still reached consensus. That is the
harder consensus case, because it is the one where validators run their own
model and the bucketed tolerance in `verdicts_agree` has to absorb the spread
without absorbing a disagreement.

**Still open:**

- [ ] **Cache-key grinding is unmitigated.** An attacker varying claim values forces
      unbounded fetch and inference cost, since every distinct claims dict is a cache miss.
      Deposit, permissioning or per-payee rate limiting -- undecided. This blocks anything
      paying for its own inference; it does not block a demo. See
      [`docs/SECURITY.md`](docs/SECURITY.md#denial-of-service).
- [ ] **Claim vocabulary is fixed in code but not in the caller's contract.** The five keys
      are enforced and an unknown key raises, which closes the silent-drop hole. What is
      still open is whether values should be schema'd rather than free strings.
- [ ] No benchmark for fetch cost relative to `exec_prompt` as an absolute number.
- [ ] Milestone 5 (composition with MandateVault) is specified in
      [`docs/COMPOSITION.md`](docs/COMPOSITION.md) and not yet wired end to end.

**Known design gaps, independent of implementation:**

**Known design gaps, independent of implementation:**

- **The address-on-site check is defeated by a compromised vendor site.** If an attacker can
  edit the vendor's page, they can put their own address on it and the check passes. Vouch
  substantiates against published evidence and inherits the security of that publication.
  Nothing in the design claims otherwise, and it should not.
- **`unsubstantiated` will be common and callers will be tempted to ignore it.** A vendor
  with no site, a JS-rendered page, or datacenter-blocking hosting all produce it. If
  operators learn to wave it through, the contract is decorative. The mitigation is
  operational rather than technical, which is worth admitting rather than designing around.
- **No revocation.** A cached `substantiated` result stands until TTL expiry even if the
  vendor is compromised the next day. A revocation list is not designed; whether it belongs
  here or in the caller's policy layer is open.
- **Claim vocabulary is unfixed.** `claims` is currently a free-form dict, which makes the
  cache key fragile and the prompt inconsistent. A fixed claim schema is probably right and
  is not yet specified — see [`docs/DECISIONS.md`](docs/DECISIONS.md).

---

## License

MIT. See [`LICENSE`](LICENSE).
