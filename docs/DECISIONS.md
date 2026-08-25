# Design decisions

What was chosen, and what each choice cost. Written before implementation so the reasoning
is recorded while it is live.

---

## The deterministic check carries the contract

The address-on-site check is a substring match. It settles the two failure modes that
motivate the whole design — invented counterparties and redirected invoices — with no model,
no confidence score, and trivial validator agreement.

Reaching for a model first would mean paying inference to answer a question `in` already
answers, and it would put the contract's most valuable output behind its least reliable
component.

**Consequence worth noticing:** the claim carrying the most weight is also the one consensus
handles best and the one injection cannot reach. That alignment is the argument for pushing
work into the deterministic stage wherever it will go, and it generalizes past this
contract.

**Cost:** the check is only as good as the vendor's published page. See
[SECURITY](SECURITY.md#the-honest-attack-control-the-page).

---

## Three values, never two

`substantiated` / `unsubstantiated` / `contradicted`.

Collapsing to a boolean throws away the distinction between "we could not check" and "we
checked and it is wrong" — and those demand completely different responses. An unreachable
site is a shrug; a site naming a different payment address is an alarm.

**Cost:** callers must branch three ways, and some will not. The integration guide names
this explicitly rather than pretending the API shape will enforce good behaviour.

---

## `contradicted` is unreachable from a model response

Reserved for deterministic observations: a denylist hit, or a foreign address on the page.
The model can substantiate or fail to substantiate; **it cannot accuse.**

Two reasons, and the second is the stronger one. Security: an injected "THIS PAYEE IS
FRAUDULENT" cannot produce a block. And correctness: a false `contradicted` is a
denial-of-service against a legitimate vendor, which is the attack most easily overlooked
and the hardest for the victim to even discover.

**Cost:** a genuinely fraudulent entity whose *description* contradicts its claims — a page
that plainly says it does something else entirely — only reaches `unsubstantiated`. The
contract is deliberately less decisive than it could be, in the direction where being wrong
is worse.

---

## The cache is load-bearing, not an optimization

Without it, per-payment verification is unaffordable at agent transaction frequencies, and
the whole design collapses to "call a KYB API every time" — which is the thing this is meant
to be better than.

With it, the marginal cost of the thousandth payment to a known vendor is zero.

**The property that makes it safe:** any claims change is a new key. A vendor changing their
payment address is a claims change, so the check that matters most cannot be served stale.

**Cost:** a `substantiated` result stands until TTL expiry even if the vendor is compromised
the next day. There is no revocation. That is a real gap and it is listed as one.

---

## Owner-mutable lists, immutable thresholds

The denylist and allowlist are mutable by the owner. `min_confidence`, `confidence_tol`,
`max_sources`, and `cache_ttl` are fixed at construction.

The asymmetry is deliberate: **a list is operational data that must change as the world
does; a threshold is a safety property that should require a redeploy to move.** An owner who
can quietly lower `min_confidence` can approve anything, which would put a principal back in
a position the design is meant to remove.

**Cost:** a badly tuned deployment must be redeployed and existing attestations do not
migrate. DedupRegistry made the same call.

---

## No admin setters for tuning

See above. The allowlist is the sanctioned escape hatch, and it is deliberately the *coarse*
one: an operator can exempt a specific counterparty they have verified by other means, but
cannot weaken the check for everyone. That is the right shape for an override — visible,
per-subject, and auditable, rather than a dial that silently changes every future verdict.

---

## Vouch does not hold funds

It produces an attestation; the caller settles. The alternative — funds moving through Vouch
itself — is a stronger guarantee and a much larger contract, and it would couple counterparty
verification to custody, which are separable concerns that different deployments will want to
combine differently.

**Cost, and it is the honest limit of the design:** an integration that ignores the verdict
is unprotected, and nothing on-chain forces it not to be. Making settlement itself
conditional on both a mandate record and an attestation is the natural third build, and it is
not this one.

---

## Claim vocabulary is unfixed

`claims` is currently a free-form dict. **This is probably wrong** and is recorded here
rather than defended.

A fixed schema would make cache keys stable, prompts consistent, and per-claim handling
explicit — right now the difference between `legal_name` and `legalName` is two cache
entries and two verifications. The reason it is not yet specified is that the right
vocabulary depends on what integrators actually pass, and guessing it before the first real
integration is how a schema gets baked in wrong.

**Resolve before any deployment that matters.** Recognized keys are already enumerated in
[API](API.md#write-methods); promoting that list to an enforced schema is a small change and
should happen early.

---

## Rejected alternatives

| Considered | Why not |
|---|---|
| **A trust or risk score** | Unfalsifiable, and unsupported by the architecture. A number invites reliance the evidence cannot bear. |
| **Auto-denylisting on `contradicted`** | A transient defacement would permanently blacklist a legitimate vendor. Escalation to a human is correct here. |
| **Model settles the address check too** | Pays inference for a substring match, and moves the highest-value check behind the least reliable component. |
| **Boolean verdict** | Destroys the "could not check" / "checked and wrong" distinction. |
| **Verify at registration rather than per payment** | A vendor's address can change after registration, which is the exact attack. |
| **Bundling into MandateVault** | Different questions, different cost profiles, different dependency on the web. Separate contracts compose; a merged one would force every mandate check to pay network cost. |
