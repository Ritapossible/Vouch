# Evidence fixtures

Two pages that stand in for a vendor's published payment details, served over
real HTTPS from `raw.githubusercontent.com` so the demo exercises the actual
fetch path rather than a mock.

| File | Names | Demonstrates |
|---|---|---|
| `acme-real.html` | `0x1111…1111` | The vendor's own page confirms the address being paid. `substantiated`. |
| `acme-moved.html` | `0x2222…2222` | The company is real and the page is live, and it names a **different** payment address. `contradicted`. |

The third demo case needs no fixture: a hallucinated vendor has no site, so the
fetch fails and the verdict is `unsubstantiated`.

`acme-moved.html` is the invoice-redirection case and the one worth reading
closely. Nothing about it is malformed. The company exists, the page is up, the
markup is ordinary, and an agent paying against an emailed invoice would find
every check it knows how to run satisfied. The only thing wrong is that the
address on the invoice is not the address on the page, and that is a substring
comparison -- no model is consulted for this verdict.

Both files put the address in an `href` as well as in body text, because that is
how a real payment page tends to mark one up and because it is the case that
`mode="text"` fetching would miss entirely. See `docs/RUNTIME-FACTS.md` item 4.
