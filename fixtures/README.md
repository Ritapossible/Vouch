# Evidence fixtures

Two pages standing in for a vendor's published payment details, served over real
HTTPS from `raw.githubusercontent.com`, so the demo exercises the actual fetch
path rather than a mock.

| File | Names | Verdict on-chain |
|---|---|---|
| `acme-real.html` | `0x1111…1111` | **`substantiated`** -- the vendor's own page confirms the address being paid |
| `acme-moved.html` | `0x2222…2222` | **`contradicted`** -- the company is real and the page is live, and it names a *different* address |

Both verdicts are recorded on studionet at
`0x42AA00A139652737285d70f3a4Fda32b478eac98`. The `contradicted` result came back
carrying the address it objected to:

```json
"foreign_addresses": ["0x2222222222222222222222222222222222222222"]
"verdict": "contradicted"
```

The third demo case needs no fixture: a hallucinated vendor has no site, so the
fetch fails and the verdict is `unsubstantiated` with `sources_reachable: 0`.

## Why `acme-moved.html` is the one to read closely

Nothing about it is malformed. The company exists, the page is up, the markup is
ordinary, and an agent paying against an emailed invoice would find every check
it knows how to run satisfied. The only thing wrong is that the address on the
invoice is not the address on the page.

That is invoice-redirection fraud, and the verdict is reached by a substring
comparison. **No model is consulted for it.** The distinction it draws is the one
the three-valued design exists for: this is `contradicted`, not
`unsubstantiated` -- the difference between "we checked and it is wrong" and "we
could not check", which call for opposite responses from the caller.

## Why the address appears twice in each file

Once in body text and once in an `href`. That is how a real payment page tends to
mark one up, and the `href` is the case a `mode="text"` fetch would drop
entirely -- which is why the contract fetches `mode="html"`. See
[`docs/RUNTIME-FACTS.md`](../docs/RUNTIME-FACTS.md) item 4.

## Reproducing

```bash
F=https://raw.githubusercontent.com/Ritapossible/vouch/main/fixtures

# substantiated
check 0x1111111111111111111111111111111111111111 \
  '{"payment_address":"0x1111111111111111111111111111111111111111"}' \
  "[\"$F/acme-real.html\"]"

# contradicted
check 0x1111111111111111111111111111111111111111 \
  '{"payment_address":"0x1111111111111111111111111111111111111111"}' \
  "[\"$F/acme-moved.html\"]"
```

Repeating either call within `cache_ttl` returns `resolved_by: "cache"` with no
fetch at all, which is also confirmed on-chain.
