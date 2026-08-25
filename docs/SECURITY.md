# Security and threat model

> Specification. Nothing has been tested, because nothing has been built.

## The bug this contract exists to not have

> **`unsubstantiated` must never become `substantiated` because something failed.**

An unreachable site, a timeout, a malformed model response, a truncated page, a contract
revert — every one of these produces `unsubstantiated`. None of them produces approval.

This is the fail-open bug, and it is worth naming first because it is the *only* way this
contract becomes worse than useless: a verification gate that approves on error is a gate
that approves precisely when an attacker arranges for something to fail. **It should be the
first thing the test suite tries to cause**, and it reappears one layer up in the caller's
exception handler — see [COMPOSITION](COMPOSITION.md#partial-failure).

## What an attacker wants

| Attacker | Wants | Method |
|---|---|---|
| Fraudulent payee | `substantiated` for an entity that is not what it claims | control a source, or exploit a weak claim |
| Attacker redirecting an invoice | the address check to pass for their address | compromise the vendor's site, or get their address onto it |
| Attacker denying service to a rival | `contradicted` for a legitimate vendor | manufacture a foreign address on a page they can influence |

The third is the one most easily overlooked, and it is why `contradicted` is
deliberately hard to trigger — deterministic observations only, strict address-extraction
pattern, and no auto-promotion to the denylist.

## Prompt injection

The stage-3 prompt carries text fetched from the public internet. That text is hostile by
default.

### Containment properties

1. **The model is asked to perceive, never to decide.** It answers "does this evidence
   support this claim?" It is never told a payment exists, what it is worth, or what follows
   from its answer. **The verdict is derived by fixed rule from coerced per-claim readings**
   — the model does not produce it. Injected text has no lever to pull because no lever
   appears in the prompt.
2. **The model cannot accuse.** `contradicted` is unreachable from a model response. The
   strongest output of the contract is entirely out of reach of both hallucination and
   injection — an injected "THIS PAYEE IS FRAUDULENT" cannot produce a block.
3. **Claim keys are coerced against the request's own set.** A response naming a claim that
   was not requested is discarded; a requested claim absent from the response is
   `unsubstantiated`.
4. **Untrusted spans are fenced with unguessable delimiters**, derived from a hash over the
   payee and source index — unpredictable to whoever wrote the page, identical on every
   validator. You cannot close a fence you cannot predict.
5. **Canonicalization is total.** Every hostile response collapses to a definite verdict
   rather than raising, so leader and validators always agree on the coercion.

### The asymmetry that makes this tractable

The most an injection can achieve is to move a **model-settled** claim from
`unsubstantiated` to `substantiated`. It cannot touch a deterministic claim, and it cannot
produce `contradicted`.

Since the deterministic claims are the ones carrying the weight — the address check above
all — **a successful injection cannot fabricate the check that matters.** An attacker who
wants the address check to pass has to actually put the address on the page, which is not an
injection at all; it is the honest attack, and it is the one addressed below.

This is a materially stronger position than a design where the model settles everything, and
it is the main security argument for pushing work into the deterministic stage.

## The honest attack: control the page

**The address-on-site check inherits the security of the vendor's publication.** An attacker
who can edit the vendor's page puts their own address on it and the check passes.

Vouch does not claim otherwise, and any user-facing surface should not either. What it
changes is the *cost* of the attack: redirecting a payment now requires compromising the
vendor's published site rather than spoofing an email. That is a substantial escalation —
from a spoofed invoice to a site compromise — but it is a raised bar, not a wall.

Mitigations available to a caller, none of which the contract enforces:

- Require **more than one independent source** — a site plus a registry listing.
- Use a source the vendor cannot edit unilaterally (a registry, a signed disclosure).
- Treat a **change** in a previously-substantiated address as `contradicted` at the policy
  layer, even when the current page agrees with itself. The contract's append-only
  attestation history is what makes this possible, and it is the strongest available answer
  to a site compromise.

That last one is worth building into any serious integration. A vendor's payment address
changing is rare and consequential; the append-only history is there precisely so the
change is visible.

## URL validation

Enforced at stage 1, before any fetch:

- **https only.** No http, file, or data schemes.
- **No userinfo component.** `https://user:pass@host/` is a host-confusion vector with no
  legitimate use here.
- **At most `max_sources` URLs.**
- **Redirects followed to a bounded depth, final host recorded.** A source redirecting off
  its declared host does not satisfy a `domain` claim under the original host.

## Denial of service

| Vector | Bound |
|---|---|
| Huge page | truncated to `max_source_bytes` after normalization |
| Many sources | capped at `max_sources`, enforced before fetching |
| Slow or hanging fetch | per-source timeout; a timeout is `unsubstantiated`, never fatal |
| Repeated checks on the same payee | cache absorbs them at zero cost |
| Cache-key grinding — varying claims slightly to force re-verification | **unmitigated.** See below. |

Cache-key grinding is the real gap: an attacker who can call `check()` with arbitrary claims
can force a fresh fetch-and-inference every time by varying a claim value. Whether
`check()` should require a deposit, be permissioned, or rate-limit per payee is **open and
should be resolved before any deployment that pays for its own inference.**

## The confusable-character problem

More acute here than anywhere else in either predecessor contract.

An attacker publishing a payment address containing a Cyrillic lookalike character produces
a string a human reads as correct and a naive substring match rejects. The result is
`unsubstantiated` on a page that looks right — and an operator who has learned that
`unsubstantiated` is usually noise waves it through.

**The attack succeeds through operator fatigue rather than through the contract.** This is
why normalization folds confusables before matching, and why DedupRegistry's confusable map
should be reused directly rather than reimplemented — it exists for exactly this class of
evasion and has already been tested against it.

## What is explicitly out of scope

- **Trustworthiness, quality, solvency, intent.** Vouch substantiates existence and claims.
  A real company can defraud you and Vouch will substantiate it correctly right up until it
  does.
- **Sanctions and regulatory screening.** Adjacent, deliberately not attempted; regulated
  screening carries legal requirements a consensus verdict does not satisfy.
- **Private or credentialed evidence.** Every validator fetches independently. Credentials
  in the contract would be credentials in every validator's hands.
- **Holding the funds.** Vouch gates a settlement the caller performs. An integration that
  ignores the verdict is unprotected and nothing on-chain forces it not to be. See
  [COMPOSITION](COMPOSITION.md#what-this-pairing-does-not-give-you).
