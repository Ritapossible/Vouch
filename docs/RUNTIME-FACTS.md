# Runtime facts

**Milestone 0. Answered before implementation began, as the build plan requires.**

Both predecessor contracts record confirmed runtime facts with the source that established
them. That discipline exists because the gap between documented and actual behaviour is
where a contract silently breaks on-chain while passing every local check.

Each fact below names the source that establishes it. Where a fact was established from SDK
source but not yet from a live node, it says so — an SDK signature proves the API exists,
not that every validator supports it.

SDK read at `py-lib-genlayer-std` revision
`11rhn002yfajawsz7fai6mykznbxkxs6l91iskj5cm82c92qhy3v`, the library paired with the runner
this contract pins.

---

## Answered

### 1. A web fetch and an `exec_prompt` can share one nondet block

**Confirmed** — Recourse does exactly this in production. Its `compute` closure fetches
every source with `gl.nondet.web.get` and then calls `gl.nondet.exec_prompt` on the
assembled text, inside a single `gl.vm.run_nondet_unsafe` block, and it is deployed and
working on two networks.

This matters more than it looks. Had the answer been no, stages 2 and 3 would have become
separate consensus rounds, and the ruling round would have had to either re-fetch or carry a
leader observation into a consensus input — which [CONSENSUS](CONSENSUS.md#the-rule)
forbids. Vouch keeps one round.

### 2. `gl.nondet.web` signature and return type

**Confirmed against SDK source** (`gl/nondet/web.py`):

```python
@dataclasses.dataclass
class Response:
    status: int
    headers: dict[str, bytes]
    body: bytes | None
```

`get(url, *, headers={})`, and `request(url, *, method, body, headers)` underneath it, with
`post` / `delete` / `head` / `patch` alongside.

- **HTTP status is exposed, and a non-200 does not raise.** The status is a field to branch
  on. Vouch treats anything outside 200–299 as unreachable.
- **`body` is `bytes | None`**, decoded with `errors="replace"` rather than trusted to be
  valid UTF-8.
- **Redirects and the final URL are not observable** in the response — there is no `url`
  field. [SECURITY](SECURITY.md#url-validation) cannot rely on inspecting a post-redirect
  host, so validation happens on the submitted URL and the fetched text is fenced
  regardless.

### 3. A fetch failure is catchable and non-fatal

**Confirmed** — Recourse's `_fetch` wraps the call in `try / except Exception` and returns
`None` for unreachable, and this path runs on-chain today.

A dead link therefore degrades one source rather than aborting the stage, which is what
makes the partial-reachability design in [CONSENSUS](CONSENSUS.md#reachability)
implementable. Vouch inherits the shape exactly, including the rule that unreachable is
`unsubstantiated` and never `contradicted`.

### 4. Rendered-page fetching **is** available

**Confirmed against SDK source** (`gl/nondet/web.py`) — and this is the finding that most
changes the product:

```python
def render(
    url: str,
    *,
    mode: typing.Literal['html', 'text', 'screenshot'] = 'text',
    wait_after_loaded: str | None = None,
) -> Lazy[str | Image]: ...
```

`wait_after_loaded` takes `"1000ms"` or `"1s"`, for content JavaScript emits after DOM load.

The build plan treated a raw-HTML-only primitive as the bad case, the one that would push a
large fraction of legitimate vendors into `unsubstantiated` for reasons unrelated to their
legitimacy and force integration guidance toward machine-readable sources. **That case did
not materialize.** No documentation change is owed.

Vouch fetches with `mode="html"` rather than `"text"`. Rendered *text* would drop the
address out of an `href` — `<a href="ethereum:0x…">` is exactly where a payment address
tends to live — and the address-on-site check is the highest-value check in the contract.
Rendered HTML carries both post-JavaScript content and attribute values, so it strictly
dominates both raw `get()` and `mode="text"` for the check that matters.

> **Confirmed on live nodes.** `render` works on studionet and on testnet-bradbury
> validators: checks against a real page return `sources_reachable: 1` and settle. The
> `get()` fallback remains in `_fetch` for a node without `WebRender`, where it can only
> cost a substantiation, never manufacture one. What a live deployment additionally
> revealed is that rendering is expensive enough to matter -- see item 6.

### 7. Block time is read from `gl.message_raw["datetime"]`

**Confirmed** — MandateVault established it and Recourse ships it, parsing with
`datetime.fromisoformat` and reading `.timestamp()` only after forcing UTC, because a naive
datetime is otherwise interpreted in the validator's local zone and every validator would
disagree.

The cache TTL comparison uses this and never wall clock. A missing or malformed value is a
classified `[EXPECTED]` rejection rather than an unclassified fault.

---

## Measured during the build

### 6. Fetch latency inside a consensus round is the binding constraint

**Answered the hard way, on testnet-bradbury.**

Every validator runs its own browser render, inside the consensus round, once
per source. That cost is charged against the round's timeout, and it is paid
five times over rather than once.

With `wait_after_loaded="1000ms"`, checks against a single source on bradbury
came back with `TIMEOUT` votes -- `["AGREE","TIMEOUT","AGREE","TIMEOUT","AGREE"]`
on one round -- and a second identical check rotated through six rounds without
settling. The same checks on studionet settled first time. Dropping the wait to
`"0ms"` (`RENDER_WAIT` in the contract) made all three demo checks settle on
bradbury on the first attempt.

Two things follow, and the second is the one that matters for anyone tuning this:

- **The wait is not free and is not per-check.** It is per validator per source.
  `max_sources = 3` at a one-second wait is three seconds of idle waiting added
  to every validator in the round.
- **Zero does not mean unrendered.** The page still loads in a browser-like
  environment and its scripts still run during load; what is skipped is the
  extra idle wait for content that arrives *after* load. A deployment that needs
  that wait can raise `RENDER_WAIT`, with the round timeout as its budget.

The `TIMEOUT` votes are also worth reading correctly: consensus tolerated them.
Rounds settled with three of five agreeing. A validator that times out does not
corrupt a verdict, it just costs a rotation -- which is the reachability gate
in [CONSENSUS](CONSENSUS.md#reachability) behaving as designed.

### 5. Fetch cost relative to `exec_prompt`

Still unmeasured as a number, and the cheapest-first architecture does not
depend on the exact figure -- only on fetch being much cheaper than inference,
which item 6 does not contradict. Worth a benchmark before anyone runs uncached
checks at volume.

---

## Inherited from the predecessor contracts

Confirmed there, and re-confirmed against the SDK revision this contract targets.

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

## Learned building Recourse

Two facts that cost a deployment each, recorded so this contract does not repeat them.

| Fact | Consequence |
|---|---|
| The runner pin must be an exact hash. `test`, `latest`, and an unversioned name are rejected by the networks. | Deployment fails outright. |
| A contract must be pure ASCII. A literal zero-width or typographic character in a docstring is enough to break it. | Tested by `test_contract_is_pure_ascii`. |
| The GenVM SDK uses PEP 695 (`class Lazy[T]`), so Python 3.11 cannot import it. Linting still passes while validation silently skips. | `tools/verify.py` selects a 3.12+ interpreter itself rather than trusting `python3`. |
| Bradbury enforces a per-transaction pubdata limit; a full-size contract is rejected with `BlockPubdataLimitReached`. | A minified artifact is built for that network. |
