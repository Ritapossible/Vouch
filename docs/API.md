# API reference

> Implemented and deployed. `genvm-lint schema` validates the contract against this
> document: eight methods, five views and three writes, with the constructor below.

## Constructor

```bash
genlayer deploy --contract contracts/vouch.py --args 3 200000 75 15 86400
```

Positional — `--args` is variadic, and a single JSON object would arrive as one dict in the
first parameter rather than spreading into keywords.

| # | Parameter | Default | Meaning |
|---|---|---|---|
| 1 | `max_sources` | `3` | Evidence URLs per check. Bounds fetch cost. |
| 2 | `max_source_bytes` | `200000` | Per-source truncation, applied after normalization. |
| 3 | `min_confidence` | `75` | Below this, a model claim is `unsubstantiated`. |
| 4 | `confidence_tol` | `15` | Validator tolerance, applied **only** within a decision bucket. |
| 5 | `cache_ttl` | `86400` | Seconds an attestation stays valid. The parameter deployments are most expected to tune. |

Fixed at construction. No admin setters for tuning — see
[DECISIONS](DECISIONS.md#no-admin-setters-for-tuning).

---

## Write methods

### `check(payee, claims, sources) -> dict`

The main call. Runs stages 0–3 and records an attestation.

```python
check(
    payee: str,                # address being paid
    claims: dict[str, str],    # assertions to substantiate
    sources: list[str],        # https URLs to check them against
) -> dict
```

Recognized claim keys:

| Key | Settled by | Notes |
|---|---|---|
| `payment_address` | **deterministic** | Defaults to `payee`. The address-on-site check. |
| `domain` | deterministic | Host of a source must match. |
| `legal_name` | model | Fuzzy. |
| `service` | model | Fuzzy, and the weakest claim in the set. |
| `registry_id` | deterministic if the id is a literal string on the page; model otherwise | |

An unrecognized claim key raises rather than being silently ignored — a caller who
misspells `legal_name` should find out, not receive a verdict that quietly omitted it.

Returns the attestation described under [Attestation shape](#attestation-shape). Raises on
malformed input, zero sources, more than `max_sources`, or a URL failing validation.

### `set_denylist(payee, value) -> None` / `set_allowlist(payee, value) -> None`

Owner-only. The two lists that short-circuit stage 1.

These exist because an operator sometimes has better information than the contract — a
signed contract, a bank relationship, a known-bad actor. See
[ARCHITECTURE](ARCHITECTURE.md#stage-1--screen).

Note the asymmetry with the tuning parameters: **these are mutable and the thresholds are
not.** A denylist is operational data that must change as the world does; a threshold is a
safety property that should require a redeploy to move.

---

## View methods

Free, deterministic, safe to call on every keystroke.

### `attestation(payee, claims, sources) -> dict | None`

The cached attestation for this exact `(payee, claims, sources)` triple, or `None`. **Does
not trigger a check** and never costs anything — this is the method a UI calls to show
current status without committing to a verification.

> **`sources` is part of the identity, not a filter.** An earlier revision keyed entries by
> `(payee, claims)` alone, which let any caller run a check against a page they controlled
> and seed a `substantiated` entry that every later reader was served. A verdict is only as
> good as the evidence behind it, so the evidence is half of what identifies it.
>
> An entry produced from one caller's chosen pages is therefore unreachable to someone
> asking with different ones: they get `None`, and do their own check. Order and duplicates
> are normalized, so citing the same evidence differently does not cost a second fetch.

### `is_current(payee, claims, sources) -> bool`

Whether a cached attestation exists and is within TTL. The cheap pre-flight before deciding
whether a payment will be fast or slow. Takes `sources` for the same reason `attestation`
does.

### `listed(payee) -> str`

`"allow"`, `"deny"`, or `"none"`.

### `total() -> int`

Attestations ever recorded.

---

## Attestation shape

```python
{
  # ── consensus-verified ────────────────────────────────────────────
  "verdict": "substantiated" | "unsubstantiated" | "contradicted",
  "claims": [
      {"key": "payment_address", "result": "substantiated",
       "method": "deterministic", "confidence": 100},
      {"key": "legal_name", "result": "substantiated",
       "method": "model", "confidence": 84},
      {"key": "service", "result": "unsubstantiated",
       "method": "model", "confidence": 41},
  ],
  "resolved_by": "cache" | "list" | "deterministic" | "model",
  "sources_reachable": 1,
  "checked_at": 1756108800,

  # ── provenance ────────────────────────────────────────────────────
  "sources": ["https://vendor.example/pay"],
  "requester": "0x…",

  # ── leader-observed, NOT consensus-verified ───────────────────────
  "observed": {
      "quotes": [{"claim": "legal_name", "source": "https://…", "text": "…"}],
      "source_digests": ["a1b2…"],
      "foreign_addresses": ["0x…"],
      "truncated": [false],
  },
}
```

Three fields deserve attention in any integration:

**`method`** — `deterministic` or `model`, per claim. These are different kinds of claim and
a caller should be able to tell them apart. A `substantiated` from a substring match is a
fact; a `substantiated` from a model at confidence 76 is a judgment that just cleared a
threshold. Surfacing this is the difference between an honest UI and a misleading one.

**`resolved_by`** — how the whole verdict was reached. A `cache` result may be up to
`cache_ttl` old.

**`sources` and `requester`** — what the verdict rests on, and who asked for it. Both are
recorded because a reader deciding whether to trust a `substantiated` needs to see the
evidence it came from and who chose that evidence. A verdict whose sources you cannot
inspect is one you are taking on the requester's word.

**`observed`** — leader-observed, **not** consensus-verified. Validators fetch different
bytes and quote different passages, so none of this is compared during consensus and none of
it carries a guarantee. It is recorded because a human reviewing a refusal wants it, and
segregated because an audit aid presented as a guarantee is a liability. See
[CONSENSUS](CONSENSUS.md).

## Error codes

| Code | Meaning |
|---|---|
| `EXPECTED_…` | Input validation family — type and range failures, as MandateVault uses. |
| `UNKNOWN_CLAIM` | A claim key the contract does not recognize. |
| `NO_SOURCES` | Empty source list. |
| `TOO_MANY_SOURCES` | Above `max_sources`. |
| `BAD_URL` | Not https, carries userinfo, or otherwise fails validation. |
| `NOT_OWNER` | List mutation attempted by a non-owner. |

All raised at stage 1 or earlier, before any network or model cost.

**There is no error for "could not verify".** That is a verdict — `unsubstantiated` — not an
exception, and the distinction is deliberate: an exception invites a caller to wrap it in a
`try` and continue, while a verdict has to be branched on.
