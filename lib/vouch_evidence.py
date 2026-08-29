"""NOT AN INTELLIGENT CONTRACT -- evidence handling, inlined into contracts/vouch.py.

No SDK import, no network, no model. Everything here is a pure function of its
arguments so the whole module is testable off-chain and identical in every
validator.

The single most important function in this file is `address_present`. It settles
the check that defeats payment-redirection fraud, it costs nothing beyond the
fetch, and it uses no model at all -- see the README's "The deterministic check
that does most of the work".
"""

import unicodedata
from hashlib import blake2b

# --- limits ---------------------------------------------------------------

MAX_URL_LEN = 2048
ADDRESS_HEX_LEN = 40
DIGEST_LEN = 16

# --- URL validation -------------------------------------------------------

URL_OK = ""
URL_NOT_HTTPS = "not https"
URL_TOO_LONG = "url too long"
URL_HAS_USERINFO = "url carries userinfo"
URL_NO_HOST = "url has no host"
URL_BAD_HOST = "url host is malformed"
URL_NOT_PUBLIC = "url host is not public"

# Hosts that must never be fetched. A validator resolving one of these reaches
# its own infrastructure rather than the vendor's, so the "evidence" it gathers
# is a fact about the validator. `localhost` and the loopback range are the
# obvious cases; the link-local 169.254.169.254 is the cloud metadata endpoint
# and is the one an attacker actually reaches for.
_BLOCKED_HOSTS = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
        "metadata",
        "metadata.google.internal",
    }
)

_BLOCKED_SUFFIXES = ("localhost", ".local", ".internal", ".localdomain")


def _is_blocked_ipv4(host: str) -> bool:
    """Private, loopback, link-local and unspecified IPv4 literals.

    Written out rather than delegated to `ipaddress` because the parsing rules
    there accept forms this should reject, and because a contract benefits from
    a check whose behaviour is obvious by reading it.
    """
    parts = host.split(".")
    if len(parts) != 4:
        return False
    nums = []
    for p in parts:
        if not p.isdigit() or len(p) > 3:
            return False
        v = int(p)
        if v > 255:
            return False
        nums.append(v)
    a, b = nums[0], nums[1]
    if a == 10 or a == 127 or a == 0:
        return True
    if a == 172 and 16 <= b <= 31:
        return True
    if a == 192 and b == 168:
        return True
    if a == 169 and b == 254:  # link-local, incl. cloud metadata
        return True
    if a >= 224:  # multicast and reserved
        return True
    return False


def validate_url(url: object) -> str:
    """Return `URL_OK` or the reason this URL is unusable.

    Returns a reason rather than raising: the caller decides whether a bad URL
    is a rejection (it is, at stage 1) or a skipped source, and a function that
    raises forces that decision here.

    Redirects are not observable in the SDK's `Response` -- there is no final
    URL to re-check -- so this validates what was submitted and the fetched text
    is fenced regardless. See docs/SECURITY.md.
    """
    if not isinstance(url, str):
        return URL_BAD_HOST
    text = url.strip()
    if not text:
        return URL_NO_HOST
    if len(text) > MAX_URL_LEN:
        return URL_TOO_LONG
    lowered = text.lower()
    if not lowered.startswith("https://"):
        return URL_NOT_HTTPS

    rest = text[len("https://") :]
    # Authority ends at the first path, query or fragment delimiter.
    for sep in ("/", "?", "#"):
        idx = rest.find(sep)
        if idx != -1:
            rest = rest[:idx]
    if not rest:
        return URL_NO_HOST
    if "@" in rest:
        # `https://evil.com@vendor.example/` reads as vendor.example to a human
        # and fetches evil.com. Rejected outright rather than parsed.
        return URL_HAS_USERINFO

    host = rest
    if host.startswith("["):  # IPv6 literal
        end = host.find("]")
        if end == -1:
            return URL_BAD_HOST
        inner = host[1:end]
        if not inner:
            return URL_NO_HOST
        low = inner.lower()
        if low in ("::1", "::") or low.startswith("fe80") or low.startswith("fc") or low.startswith("fd"):
            return URL_NOT_PUBLIC
        return URL_OK

    if ":" in host:
        host, _, port = host.partition(":")
        if port and not port.isdigit():
            return URL_BAD_HOST
    if not host:
        return URL_NO_HOST

    low = host.lower().rstrip(".")
    if not low:
        return URL_NO_HOST
    if any(c.isspace() for c in low):
        return URL_BAD_HOST
    if low in _BLOCKED_HOSTS or low.endswith(_BLOCKED_SUFFIXES):
        return URL_NOT_PUBLIC
    if _is_blocked_ipv4(low):
        return URL_NOT_PUBLIC
    # A public name needs a dot; a bare label is either internal or a typo.
    if "." not in low and not _is_blocked_ipv4(low):
        return URL_NOT_PUBLIC
    for label in low.split("."):
        if not label:
            return URL_BAD_HOST
        for ch in label:
            if not (ch.isalnum() or ch == "-"):
                return URL_BAD_HOST
    return URL_OK


def host_of(url: object) -> str:
    """The lowercase host, or "" if the URL is unusable."""
    if not isinstance(url, str):
        return ""
    text = url.strip()
    if not text.lower().startswith("https://"):
        return ""
    rest = text[len("https://") :]
    for sep in ("/", "?", "#"):
        idx = rest.find(sep)
        if idx != -1:
            rest = rest[:idx]
    if "@" in rest:
        return ""
    if rest.startswith("["):
        end = rest.find("]")
        return rest[1:end].lower() if end != -1 else ""
    host, _, _ = rest.partition(":")
    return host.lower().rstrip(".")


def domain_of(value: object) -> str:
    """The hostname from a `domain` claim, which may be bare or a full URL.

    An earlier revision wrote `("https://" + value.lower().lstrip("htps:/"))`,
    intending to drop a scheme if one was present. `str.lstrip` takes a *set of
    characters*, not a prefix, so it removed every leading character that
    happened to be in `htps:/` -- which mangled bare domains far more often
    than it helped:

        shop.com     -> op.com
        thing.io     -> ing.io
        pay.example  -> ay.example

    Every one of those then failed to match its own source and the `domain`
    claim came back `contradicted`, which is the *strongest* verdict this
    contract can return. A parsing slip was manufacturing accusations.

    This strips a scheme only when there is genuinely one to strip, and
    otherwise treats the value as a hostname.
    """
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text:
        return ""

    # A scheme is a prefix ending in "://", not a bag of characters.
    marker = text.find("://")
    if marker != -1:
        scheme = text[:marker].lower()
        if scheme and all(c.isalnum() or c in "+-." for c in scheme):
            return host_of("https://" + text[marker + 3 :])
        return ""

    # Bare authority: strip anything a hostname cannot carry, then validate.
    for sep in ("/", "?", "#"):
        idx = text.find(sep)
        if idx != -1:
            text = text[:idx]
    if "@" in text:
        return ""
    host, _, port = text.partition(":")
    if port and not port.isdigit():
        return ""
    low = host.strip().lower().rstrip(".")
    if not low or "." not in low:
        return ""
    for label in low.split("."):
        if not label:
            return ""
        for ch in label:
            if not (ch.isalnum() or ch == "-"):
                return ""
    return low


def registrable(host: object) -> str:
    """A coarse registrable-domain approximation: the last two labels.

    Deliberately coarse. A real public-suffix list is a data dependency this
    contract will not carry, and the consequence of the approximation is stated
    where it matters: for a host under a multi-part suffix such as
    `vendor.co.uk`, this returns `co.uk`, which is too permissive to use as a
    security boundary. It is used only to match a `domain` claim against the
    source host, never to authorize anything.
    """
    if not isinstance(host, str):
        return ""
    low = host.strip().lower().rstrip(".")
    if not low or ":" in low:
        return ""
    labels = [l for l in low.split(".") if l]
    if len(labels) < 2:
        return low
    return ".".join(labels[-2:])


# --- text normalization ---------------------------------------------------

# Ported unchanged from DedupRegistry, as docs/BUILD-PLAN.md requires. The map
# folds characters that render as their Latin twin: a vendor page spelling its
# own name with a Cyrillic `e` tokenizes differently from the Latin spelling,
# and an address written with one is a different string that looks identical.
#
# Keys are \uXXXX escapes, not literal glyphs, so this module and the generated
# contract stay pure ASCII. Several of these characters are invisible or render
# as their twin, which makes raw bytes silently fragile in transit -- an editor
# or a diff view can substitute one with no visible trace. Escapes survive that.
_CONFUSABLES = {
    "\u0430": "a", "\u0435": "e", "\u043e": "o", "\u0440": "p", "\u0441": "c",
    "\u0445": "x", "\u0443": "y", "\u0456": "i", "\u0455": "s", "\u0458": "j",
    "\u04bb": "h", "\u0432": "b", "\u043c": "m", "\u043d": "h", "\u0442": "t",
    "\u03b1": "a", "\u03bf": "o", "\u03b5": "e", "\u03c1": "p", "\u03c4": "t",
    "\u03bd": "v", "\u03b9": "i", "\u03ba": "k", "\u03c7": "x", "\u03b2": "b",
    "\u0131": "i", "\u0130": "i", "\u017f": "s", "\u212a": "k", "\u212b": "a",
}

_ZERO_WIDTH = frozenset("\u00ad\u180e\u200b\u200c\u200d\u2060\ufeff")


_NAMED_ENTITIES = {
    "amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'", "nbsp": " ",
}


def _unescape(text: str) -> str:
    """Decode numeric and the five standard named HTML entities.

    Hand-rolled rather than importing `html`, to keep this module's import
    surface to what the predecessor contracts already proved available in the
    runner. The set is deliberately small: these are the references that appear
    in real markup around an address, and an unrecognized reference is left
    alone rather than guessed at.
    """
    if "&" not in text:
        return text
    out = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch != "&":
            out.append(ch)
            i += 1
            continue
        end = text.find(";", i + 1, i + 12)
        if end == -1:
            out.append(ch)
            i += 1
            continue
        ref = text[i + 1 : end]
        if ref.startswith("#"):
            body = ref[1:]
            try:
                code = int(body[1:], 16) if body[:1] in ("x", "X") else int(body)
            except ValueError:
                out.append(ch)
                i += 1
                continue
            if 0 < code < 0x110000:
                out.append(chr(code))
                i = end + 1
                continue
            out.append(ch)
            i += 1
            continue
        repl = _NAMED_ENTITIES.get(ref.lower())
        if repl is None:
            out.append(ch)
            i += 1
            continue
        out.append(repl)
        i = end + 1
    return "".join(out)


_DROPPED_ELEMENTS = ("script", "style", "template", "noscript")


def html_to_text(raw: object) -> str:
    """Flatten rendered HTML to searchable text, keeping attribute values.

    Vouch fetches `mode="html"` rather than `mode="text"`, so this runs on every
    page, and it does two things that a naive tag strip does not:

    **Attribute values are kept.** A payment address very often lives in an
    attribute -- `<a href="ethereum:0x...">` -- and rendered text mode drops it
    entirely. Losing the address there would blind the contract's most valuable
    check on exactly the pages that markup it properly.

    **Tags become a space, and their names are discarded.** This is what makes
    `compact` safe. An address split as `0x12<b>34</b>` must not compact to
    `0x12b34`; dropping the tag name leaves `0x12 34`, which compacts to the
    real address.

    `<script>` and `<style>` bodies are dropped wholesale. They are noise, they
    routinely contain hex blobs, and a hex blob read as an address would
    manufacture a `contradicted` verdict out of a minified bundle.
    """
    if not isinstance(raw, str) or not raw:
        return ""

    # Drop dropped-element bodies first, so their contents never reach the scan.
    lowered = raw.lower()
    for name in _DROPPED_ELEMENTS:
        open_tag = "<" + name
        cursor = 0
        while True:
            start = lowered.find(open_tag, cursor)
            if start == -1:
                break
            after = lowered[start + len(open_tag) : start + len(open_tag) + 1]
            if after and (after.isalnum() or after == "-"):
                cursor = start + len(open_tag)
                continue
            close = lowered.find("</" + name, start)
            if close == -1:
                raw = raw[:start]
                lowered = lowered[:start]
                break
            end = lowered.find(">", close)
            end = len(lowered) if end == -1 else end + 1
            raw = raw[:start] + " " + raw[end:]
            lowered = lowered[:start] + " " + lowered[end:]
            cursor = start + 1

    out = []
    i = 0
    n = len(raw)
    while i < n:
        ch = raw[i]
        if ch != "<":
            out.append(ch)
            i += 1
            continue
        # Inside a tag: harvest quoted attribute values, discard the rest.
        i += 1
        quote = ""
        attr = []
        harvested = []
        while i < n:
            c = raw[i]
            if quote:
                if c == quote:
                    harvested.append("".join(attr))
                    attr = []
                    quote = ""
                else:
                    attr.append(c)
                i += 1
                continue
            if c in ('"', "'"):
                quote = c
                i += 1
                continue
            if c == ">":
                i += 1
                break
            i += 1
        out.append(" ")
        for value in harvested:
            out.append(value)
            out.append(" ")
    return _unescape("".join(out))


def normalize(text: object) -> str:
    """Fold text to a canonical comparison form.

    NFKC -> casefold -> confusable fold -> NFKC -> strip zero-width ->
    non-alphanumeric to space -> collapse runs.

    **The map is ported from DedupRegistry unchanged; the order is not.**
    DedupRegistry folds confusables before casefolding, which silently misses
    every uppercase confusable: a Cyrillic capital A is not in a lowercase-only
    map, passes through untouched, and only then casefolds to a Cyrillic small
    a that nothing maps afterwards. `normalize("Acme")` with a Cyrillic capital
    therefore did not equal `normalize("Acme")` in Latin -- which is precisely
    the attack the map exists to stop, arriving in the one position a vendor
    name is most likely to use.

    Casefolding first fixes it and keeps the map lowercase-only, which is what
    that comment wanted in the first place.
    """
    if not isinstance(text, str):
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.casefold()
    text = "".join(_CONFUSABLES.get(ch, ch) for ch in text)
    text = unicodedata.normalize("NFKC", text)

    out = []
    for ch in text:
        if ch in _ZERO_WIDTH:
            continue
        out.append(ch if ch.isalnum() else " ")
    return " ".join("".join(out).split())


def compact(normalized: object) -> str:
    """The normalized text with every space removed.

    This exists for one reason. Vouch fetches rendered HTML, so a payment
    address is routinely broken up by markup -- `<span>0x12</span><span>34</span>`
    normalizes to `0x12 34`, and a substring search for the address fails on a
    page that plainly contains it. Searching the de-spaced form too closes that
    hole.

    Safe precisely because the thing being searched for is 40 hex characters: a
    coincidental match across an unrelated boundary is not a practical concern
    at that length. It would not be safe for short needles, and nothing here
    searches for short needles.
    """
    if not isinstance(normalized, str):
        return ""
    return "".join(normalized.split())


def truncate(text: object, limit: int) -> tuple:
    """`(text, was_truncated)`, cut to `limit` characters.

    Applied after normalization, so the limit bounds what the model and the
    substring check actually see rather than bounding raw bytes.
    """
    if not isinstance(text, str):
        return ("", False)
    if limit <= 0:
        return ("", bool(text))
    if len(text) <= limit:
        return (text, False)
    return (text[:limit], True)


def digest(text: object) -> str:
    """A stable short digest.

    `blake2b`, never the builtin `hash()`: the builtin is salted per process and
    would give a different answer in every validator.
    """
    if not isinstance(text, str):
        text = ""
    return blake2b(text.encode("utf-8"), digest_size=DIGEST_LEN).hexdigest()


# --- addresses ------------------------------------------------------------

_HEX = frozenset("0123456789abcdef")


def canonical_address(addr: object) -> str:
    """A 0x-prefixed 40-hex-character address, lowercased, or "" if malformed.

    Case is discarded deliberately. EIP-55 checksum casing carries information,
    but comparing addresses case-sensitively would make `0xAB\u2026` and `0xab\u2026`
    different payees, and a caller who lowercases their input would get a
    different cache key for the same counterparty.
    """
    if not isinstance(addr, str):
        return ""
    text = addr.strip().lower()
    if not text.startswith("0x"):
        return ""
    body = text[2:]
    if len(body) != ADDRESS_HEX_LEN:
        return ""
    for ch in body:
        if ch not in _HEX:
            return ""
    return text


def address_present(address: object, page_normalized: object) -> bool:
    """Does this address literally appear in the page's normalized text?

    The check the README calls the highest-value one in the contract. Searched
    both with and without the `0x` prefix, and in both the spaced and de-spaced
    forms, because a page may write the address bare, may split it across
    markup, or both.
    """
    canon = canonical_address(address)
    if not canon:
        return False
    if not isinstance(page_normalized, str) or not page_normalized:
        return False
    bare = canon[2:]
    squashed = compact(page_normalized)
    return (
        canon in page_normalized
        or bare in page_normalized
        or canon in squashed
        or bare in squashed
    )


def foreign_addresses(page_normalized: object, own: object) -> tuple:
    """Addresses on the page that are **not** the one being paid.

    This is what separates `contradicted` from `unsubstantiated`, and it is
    tuned strict on purpose. A page naming a different payment address is
    positive evidence that the payment is misdirected; a page naming no address
    at all is merely silent, and silence is `unsubstantiated`.

    Scanned over the de-spaced form so an address broken by markup is still
    found, and returned sorted and de-duplicated so every validator that saw the
    same page produces the same tuple in the same order.
    """
    if not isinstance(page_normalized, str) or not page_normalized:
        return ()
    canon_own = canonical_address(own)

    found = set()
    # Scanned in both forms, and the union matters in both directions.
    #
    # The spaced form keeps token boundaries, which is what makes the long-run
    # guard below correct: two addresses printed side by side stay separate.
    # The de-spaced form reassembles an address broken up by markup. Scanning
    # only the de-spaced form would glue `0xAAA\u2026 0xBBB\u2026` into one 80-character
    # hex run and find neither; scanning only the spaced form would miss the
    # split one. Each covers the other's blind spot.
    for haystack in (page_normalized, compact(page_normalized)):
        idx = haystack.find("0x")
        while idx != -1:
            body = haystack[idx + 2 : idx + 2 + ADDRESS_HEX_LEN]
            if len(body) == ADDRESS_HEX_LEN and all(c in _HEX for c in body):
                # Reject a longer hex run: a 41st hex character means this is
                # not a bare address but a slice of some larger blob, and
                # treating it as an address would manufacture a `contradicted`
                # verdict out of a hash or a commit id.
                after = haystack[idx + 2 + ADDRESS_HEX_LEN : idx + 3 + ADDRESS_HEX_LEN]
                if not after or after not in _HEX:
                    candidate = "0x" + body
                    if candidate != canon_own:
                        found.add(candidate)
            idx = haystack.find("0x", idx + 2)
    return tuple(sorted(found))
