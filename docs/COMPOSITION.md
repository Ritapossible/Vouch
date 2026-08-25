# Composition with MandateVault

Vouch is the second half of a payment guard whose first half already exists as a separate
deployed-design contract:
[MandateVault](https://github.com/Ritapossible/GenLayer-Mandate-Vault).

> Specification. The pairing described here has not been implemented or tested.

## The two halves

| | MandateVault | Vouch |
|---|---|---|
| Bounds | **what** the agent may buy | **whom** the agent may pay |
| Question | does this purpose fall under clause N? | does the evidence substantiate this counterparty? |
| Input it judges | a prose mandate and a memo | a live web page |
| Uses the web | no | **yes** |
| Cost | ≤ 1 LLM call | ≤ 1 LLM call, usually 0 |
| Failure it prevents | an agent buying outside its remit | an agent paying a fiction |

Neither subsumes the other, and neither is much good alone. A mandate check with no
counterparty check approves a well-worded purchase from a company that does not exist. A
counterparty check with no mandate check confirms a real vendor and lets the agent buy
anything it likes from them.

## The test they answer together

The standard question for any agent-authority system:

> **If the agent were fully compromised, what would stop it?**

With both contracts in the payment path: it can neither buy the wrong thing nor pay a fake
entity — **and it cannot skip either check, because the checks are the payment path rather
than a wrapper around it.**

That last clause is the whole argument. An off-chain verification step is a step a
compromised agent skips. A contract that must produce a record before settlement can proceed
is not a step to skip.

## Sequence

```
     agent
       │  intent: pay <payee> <amount> for "<memo>"
       ▼
  ┌─────────────────────────────────────────┐
  │ 1. MandateVault.request(payee, amt, memo)│   cheap: no network
  │    deterministic screen → maybe 1 LLM    │
  └───────────────┬─────────────────────────┘
                  │ denied ──▶ STOP. Nothing else runs.
                  │ approved
                  ▼
  ┌─────────────────────────────────────────┐
  │ 2. Vouch.check(payee, claims, sources)  │   cache hit: free
  │    cache → screen → fetch → maybe 1 LLM │
  └───────────────┬─────────────────────────┘
                  │ contradicted   ──▶ BLOCK + alert
                  │ unsubstantiated──▶ HOLD for review
                  │ substantiated
                  ▼
              settle(payee, amount)
```

## Why MandateVault goes first

Three reasons, in order of weight:

**1. It is cheaper.** A mandate denial costs at most one LLM call and **zero network
fetches**. A Vouch check on an uncached counterparty costs fetches and possibly inference.
Running the expensive check first and then discovering the purchase was never permitted
wastes both.

**2. It fails more often.** Most refusals in practice are mandate refusals — an agent
attempting something outside its remit is a far more common event than an agent finding a
fictional vendor. Order the cheap, high-hit-rate filter first. This is the same
cheapest-first discipline both contracts use internally, applied one level up.

**3. Denial leaks less.** A mandate denial reveals only that the purchase was not permitted.
A Vouch check *fetches the vendor's site*, which is an observable action. Not checking a
counterparty for a purchase that was never allowed is the more discreet ordering, and it
avoids generating traffic that a vendor could correlate.

## The claims Vouch receives

MandateVault's output does not feed Vouch's input directly — they judge different things —
but the *caller* assembles Vouch's claims from the same purchase intent:

| Purchase intent field | Becomes |
|---|---|
| payee address | `payee`, and `claims["payment_address"]` |
| vendor name from the invoice or agent | `claims["legal_name"]` |
| vendor domain | `claims["domain"]`, and typically the source URL |
| what is being bought | `claims["service"]` |

`claims["service"]` is worth flagging: it is the weakest claim Vouch handles, it is settled
by the model rather than deterministically, and it should rarely be blocking on its own. A
GPU vendor whose page describes "accelerated compute" is not a red flag. Treat a
`service` failure as a review signal; treat a `payment_address` failure as a stop.

## Partial failure

The cases a real integration has to handle, and the ones a demo will skip:

| Situation | Correct behaviour |
|---|---|
| Mandate approved, Vouch `contradicted` | **Block and alert.** The site names a different address — this is the fraud signal, not an inconvenience. |
| Mandate approved, Vouch `unsubstantiated` | **Hold for review.** Could not check ≠ wrong. Escalate to a human, do not auto-approve. |
| Mandate denied | Stop. Do not call Vouch — it costs money and answers a question that no longer matters. |
| Vouch reverts (contract error) | **Treat as `unsubstantiated`, never as approval.** This is the fail-open bug the whole design exists to prevent; a caller's exception handler is exactly where it reappears. |
| Vouch is unreachable / not deployed | Same. Fail toward hold. |

**The last two rows are the ones that get implemented wrong**, because the natural shape of
a `try/except` around a contract call is to log and continue. An integration that catches a
Vouch failure and settles anyway has all of the cost of this contract and none of its
protection. Write the test that proves it does not.

## Fail-closed directions across the three contracts

Worth putting in one place, because the directions differ and the difference is meaningful:

| Contract | Fails closed toward | Because |
|---|---|---|
| MandateVault | **denial** | a wrongful approval spends money that does not come back |
| Vouch | **non-approval** (`unsubstantiated`) | same asymmetry: the payment is the irreversible act |
| [Recourse](https://github.com/Ritapossible/Recourse) | **no slash** | a wrongful slash *takes* money from someone who may have done nothing wrong |

Two of the three fail toward not-paying; Recourse fails toward not-taking. In every case the
rule is the same and only its direction changes:

> **Fail toward whichever outcome is reversible.**

A system that says "we fail closed" without naming a direction has not answered the
question, and the direction is derivable rather than a matter of taste — find the
irreversible action and refuse it under uncertainty.

## What this pairing does not give you

Being direct, since the combined pitch is the strongest thing about either contract and
therefore the easiest to overclaim:

- **Neither contract stops a real vendor from defrauding you.** Vouch substantiates
  existence, not honesty. MandateVault checks purpose, not value for money.
- **A compromised vendor site defeats the address check**, because the check inherits the
  security of the vendor's publication.
- **Neither contract holds the funds.** They gate a settlement the caller performs. An
  integration that ignores the verdicts is unprotected, and nothing on-chain forces the
  caller to honour them. Making settlement itself conditional — the funds moving through a
  contract that requires both records — is a stronger design and is not what either contract
  currently specifies.

That last point is the honest limit of the current pairing, and it is the natural third
build if the first two land.
