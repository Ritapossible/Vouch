# Runtime facts to confirm before implementation

**The first build task.**

Both predecessor contracts record confirmed runtime facts with the source that established
them — "`gl.nondet.exec_prompt(..., response_format="json")` returns a `dict`, not a string
— confirmed against the GenVM Python runner source." That discipline exists because the gap
between documented and actual behaviour is where a contract silently breaks on-chain while
passing every local check.

Vouch depends on the web primitive, which neither predecessor used. Everything below is
**assumed** and must be confirmed against **SDK source or a live runtime**, not against
documentation. Record what was found, and where.

---

## Blocking

### 1. Can a web fetch and an `exec_prompt` share one nondet block?

**Assumed:** yes.

**If no:** stages 2 and 3 become separate consensus rounds, and the ruling round must either
re-fetch (expensive, doubles network cost, preserves "never trust the leader") or carry
forward what the gather round observed — which pipes a leader observation into a consensus
input, and [CONSENSUS](CONSENSUS.md#the-rule) forbids it.

Same unknown as Recourse's, and resolving it once answers it for both.

### 2. `gl.nondet.web` signature and return type

Unverified:

- call shape, and whether a render mode exists (raw / text / rendered)
- return type — bytes, `str`, or a structured response
- whether HTTP status is exposed or a non-200 raises
- whether redirects are followed automatically and whether the final URL is observable —
  [SECURITY](SECURITY.md#url-validation) depends on this
- configurable timeout, and behaviour on one

### 3. How a fetch failure surfaces

**Assumed:** catchable, so an unreachable source records as unreachable while the check
proceeds on the rest.

**If a failure aborts the nondet block instead**, a single dead link kills every check
against that source, and the partial-reachability design in
[CONSENSUS](CONSENSUS.md#reachability) is unimplementable.

### 4. Whether rendered-page fetching is available

**Materially affects viability.** Many vendor sites render content — including contact and
payment details — with JavaScript. If the primitive returns raw HTML only, a large fraction
of legitimate vendors are `unsubstantiated` for reasons unrelated to their legitimacy, and
[the operator-fatigue failure](SECURITY.md#the-confusable-character-problem) becomes the
dominant risk rather than an edge case.

If raw-only: consider guiding integrators toward machine-readable sources — a
`/.well-known/` path, a registry API, a plain-text disclosure — rather than a marketing
homepage. That is a documentation change, not a code change, and it should be made before
anyone integrates.

---

## Important

### 5. Fetch cost relative to `exec_prompt`

The cheapest-first architecture assumes fetch ≪ inference. If comparable, the stage-2/3
boundary stops earning its complexity.

### 6. Fetch latency inside a consensus round

No benchmark. Determines whether `max_sources = 3` is generous or already too many, and
whether an uncached check is usable in a payment path at all.

### 7. Block time access

The cache TTL comparison must use block time, not wall clock — wall clock differs per
validator and would turn every cache read into a source of disagreement. MandateVault reads
`gl.message_raw["datetime"]` and parses it; confirm the same path and its failure modes.

---

## Inherited from the predecessor contracts

Confirmed there; re-confirm against the SDK version this contract targets.

| Fact | Source |
|---|---|
| `gl.nondet.exec_prompt(..., response_format="json")` returns a `dict`, not a string | GenVM Python runner source (DedupRegistry) |
| `run_nondet_unsafe` is annotated `-> Lazy[T]` but the `@_lazy_api` eager wrapper calls `.get()`, so it returns `T` directly | SDK `vm.py` (DedupRegistry) |
| `gl.eq_principle.*` helpers open their own nondet block; nesting is rejected | both |
| Storage handles are unusable inside a nondet block; materialize to plain tuples first | both |
| `gl.storage.copy_to_memory` is for storage-backed composites; misuse escapes as an unclassified VM fault, not a `UserError` | MandateVault |
| `genlayer deploy --contract` reads one path and does no module bundling | DedupRegistry |
| `genlayer code <address>` is gated to localnet; use `gen_getContractCode` over JSON-RPC | DedupRegistry |
| Line endings must be pinned to LF or source digests differ between checkouts | DedupRegistry |

---

## Recording answers

Replace the Blocking and Important sections with findings as they land, in the predecessors'
format: the claim, then the source that establishes it. A fact without a source is an
assumption wearing a fact's clothes — which is the failure this document exists to prevent.
