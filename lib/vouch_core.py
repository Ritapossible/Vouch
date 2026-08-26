"""The decision engine: claims, screening, aggregation, and consensus.

No SDK import, no network, no model. Every rule that decides an outcome lives
here so it can be tested exhaustively off-chain, and so that reading one file
answers "what makes this contract say no?".

The invariant the whole contract exists to hold:

    No error path produces `substantiated`.

Every failure -- an unreachable source, a malformed model answer, a timeout, a
truncated page -- resolves toward `unsubstantiated`. `contradicted` is reserved
for positive evidence of conflict and is never reachable from a failure or from
a model.
"""

from hashlib import blake2b

# --- error classification -------------------------------------------------
#
# Same prefixes the predecessor contracts use. The prefix tells a caller
# whether retrying could plausibly help, which is not something a bare message
# communicates.

ERROR_EXPECTED = "[EXPECTED]"    # caller's input; retrying unchanged will not help
ERROR_EXTERNAL = "[EXTERNAL]"    # the world's fault; the source, not the caller
ERROR_TRANSIENT = "[TRANSIENT]"  # may succeed on retry
ERROR_LLM = "[LLM_ERROR]"        # the model misbehaved

REASON_UNKNOWN_CLAIM = "UNKNOWN_CLAIM"
REASON_NO_SOURCES = "NO_SOURCES"
REASON_TOO_MANY_SOURCES = "TOO_MANY_SOURCES"
REASON_BAD_URL = "BAD_URL"
REASON_NOT_OWNER = "NOT_OWNER"
REASON_BAD_PAYEE = "BAD_PAYEE"
REASON_BAD_CLAIMS = "BAD_CLAIMS"
REASON_DENIED = "DENIED"

# --- the three values -----------------------------------------------------
#
# Three, never two. Collapsing `unsubstantiated` and `contradicted` into
# "failed" throws away the distinction between "we could not check" and "we
# checked and it is wrong", and those call for different responses from the
# caller. See the README's "Three values, never two".

SUBSTANTIATED = "substantiated"
UNSUBSTANTIATED = "unsubstantiated"
CONTRADICTED = "contradicted"

RESULTS = (SUBSTANTIATED, UNSUBSTANTIATED, CONTRADICTED)

# How a whole verdict was reached.
BY_CACHE = "cache"
BY_LIST = "list"
BY_DETERMINISTIC = "deterministic"
BY_MODEL = "model"

# How a single claim was settled.
METHOD_DETERMINISTIC = "deterministic"
METHOD_MODEL = "model"

# --- claim vocabulary -----------------------------------------------------
#
# Fixed, and unknown keys raise rather than being ignored. A caller who
# misspells `legal_name` should find out, not receive a verdict that quietly
# omitted the claim they cared about -- a silently dropped claim is a verdict
# that answers a narrower question than the caller asked, while looking
# identical to one that answered all of it.

CLAIM_PAYMENT_ADDRESS = "payment_address"
CLAIM_DOMAIN = "domain"
CLAIM_LEGAL_NAME = "legal_name"
CLAIM_SERVICE = "service"
CLAIM_REGISTRY_ID = "registry_id"

CLAIM_KEYS = (
    CLAIM_PAYMENT_ADDRESS,
    CLAIM_DOMAIN,
    CLAIM_LEGAL_NAME,
    CLAIM_SERVICE,
    CLAIM_REGISTRY_ID,
)

# `registry_id` is deterministic when the id appears literally on the page and
# falls back to the model otherwise, so it is in neither fixed set.
DETERMINISTIC_CLAIMS = frozenset({CLAIM_PAYMENT_ADDRESS, CLAIM_DOMAIN})
MODEL_CLAIMS = frozenset({CLAIM_LEGAL_NAME, CLAIM_SERVICE})

MAX_CLAIM_VALUE_LEN = 512
MAX_CLAIMS = len(CLAIM_KEYS)

# --- limits ---------------------------------------------------------------

U256_MAX = (1 << 256) - 1


class Limits:
    """Constructor parameters, fixed at deployment.

    Deliberately not settable afterwards. A denylist is operational data that
    must change as the world does; a threshold is a safety property, and moving
    one silently changes what every past verdict would have meant. See
    docs/DECISIONS.md.
    """

    __slots__ = ("max_sources", "max_source_bytes", "min_confidence", "confidence_tol", "cache_ttl")

    def __init__(self, max_sources, max_source_bytes, min_confidence, confidence_tol, cache_ttl):
        self.max_sources = int(max_sources)
        self.max_source_bytes = int(max_source_bytes)
        self.min_confidence = int(min_confidence)
        self.confidence_tol = int(confidence_tol)
        self.cache_ttl = int(cache_ttl)


def validate_limits(limits) -> str:
    """Empty if the limits are usable, else the reason they are not."""
    if limits.max_sources < 1 or limits.max_sources > 16:
        return "max_sources must be 1..16"
    if limits.max_source_bytes < 1000 or limits.max_source_bytes > 2_000_000:
        return "max_source_bytes must be 1000..2000000"
    if limits.min_confidence < 0 or limits.min_confidence > 100:
        return "min_confidence must be 0..100"
    if limits.confidence_tol < 0 or limits.confidence_tol > 100:
        return "confidence_tol must be 0..100"
    if limits.cache_ttl < 0 or limits.cache_ttl > U256_MAX:
        return "cache_ttl out of range"
    return ""


# --- claim canonicalization ----------------------------------------------


def canonical_claims(claims: object) -> tuple:
    """Validated claims as a sorted tuple of `(key, value)`.

    Sorted because the cache key is derived from this, and a dict's insertion
    order must not decide whether two identical requests hit the same cache
    entry.

    Returns `(pairs, error)`. The error is a reason string, empty when valid.
    """
    if not isinstance(claims, dict):
        return ((), REASON_BAD_CLAIMS)
    if not claims:
        return ((), REASON_BAD_CLAIMS)
    if len(claims) > MAX_CLAIMS:
        return ((), REASON_BAD_CLAIMS)

    pairs = []
    for key in claims:
        if not isinstance(key, str):
            return ((), REASON_BAD_CLAIMS)
        if key not in CLAIM_KEYS:
            return ((), REASON_UNKNOWN_CLAIM)
        value = claims[key]
        if not isinstance(value, str):
            return ((), REASON_BAD_CLAIMS)
        text = value.strip()
        if not text or len(text) > MAX_CLAIM_VALUE_LEN:
            return ((), REASON_BAD_CLAIMS)
        pairs.append((key, text))
    return (tuple(sorted(pairs)), "")


def cache_key(payee: str, claim_pairs: tuple) -> str:
    """The cache key for `(payee, claims)`.

    Any change to any claim value produces a different key and forces
    re-verification. That is what makes it safe to cache at all: a vendor
    changing their payment address is a claims change, so the check that matters
    most can never be served stale.

    Fields are length-prefixed rather than joined by a separator, so no
    combination of claim values can be arranged to collide with a different set.
    """
    h = blake2b(digest_size=16)
    parts = [payee]
    for key, value in claim_pairs:
        parts.append(key)
        parts.append(value)
    for part in parts:
        raw = part.encode("utf-8")
        h.update(str(len(raw)).encode("ascii"))
        h.update(b":")
        h.update(raw)
    return h.hexdigest()


# --- aggregation ----------------------------------------------------------


def aggregate(results) -> str:
    """The verdict, from the per-claim results. By fixed rule, never by model.

    Precedence, and the order matters:

    1. **Any `contradicted` wins.** Positive evidence of conflict on one claim
       is not outweighed by other claims checking out. A real company with a
       substituted payment address substantiates its name and its domain and is
       still exactly the fraud this contract exists to catch.
    2. Otherwise, **every** claim must be `substantiated`.
    3. Otherwise `unsubstantiated`.

    An empty result set is `unsubstantiated`, not `substantiated`. Vacuous truth
    is the wrong default when the question is "did we check anything?".
    """
    seen = False
    all_sub = True
    for r in results:
        seen = True
        if r == CONTRADICTED:
            return CONTRADICTED
        if r != SUBSTANTIATED:
            all_sub = False
    if not seen:
        return UNSUBSTANTIATED
    return SUBSTANTIATED if all_sub else UNSUBSTANTIATED


def coerce_result(raw: object) -> str:
    """Map any value onto the three results. Unknown means `unsubstantiated`.

    Total: there is no input for which this raises. That is the point -- it sits
    between a model's free-form answer and the contract's decision, and a
    raising path there would turn a weird model response into a fault instead of
    a safe verdict.
    """
    if not isinstance(raw, str):
        return UNSUBSTANTIATED
    text = raw.strip().casefold()
    if text in (SUBSTANTIATED, "true", "yes", "supported", "confirmed"):
        return SUBSTANTIATED
    if text in (CONTRADICTED, "conflicts", "conflicting"):
        return CONTRADICTED
    return UNSUBSTANTIATED


def coerce_model_result(raw: object, confidence: object, min_confidence: int) -> tuple:
    """A model's answer for one claim, as `(result, confidence)`.

    Two rules, both load-bearing:

    **A model can never produce `contradicted`.** It may only substantiate or
    fail to. `contradicted` is a strong, actionable claim that the evidence
    positively conflicts, and it is reserved for deterministic checks -- a
    foreign payment address on the page, a host that does not match. Allowing a
    model to reach it would put the contract's strongest signal at the mercy of
    text on a fetched page, so a page saying "this company is a fraud" moves
    nothing. See docs/SECURITY.md on injected accusations.

    **Below `min_confidence` is `unsubstantiated`.** A low-confidence
    substantiation is not a weak yes, it is a no.
    """
    try:
        conf = int(confidence)
    except (TypeError, ValueError):
        conf = 0
    if conf < 0:
        conf = 0
    elif conf > 100:
        conf = 100

    result = coerce_result(raw)
    if result == CONTRADICTED:
        # Downgraded, never honoured.
        result = UNSUBSTANTIATED
    if result == SUBSTANTIATED and conf < min_confidence:
        result = UNSUBSTANTIATED
    if result != SUBSTANTIATED:
        # An unsubstantiated claim carries no confidence. Reporting "we did not
        # substantiate this, at confidence 99" reads as a strong finding and is
        # not one -- the confidence belonged to an answer that was discarded.
        conf = 0
    return (result, conf)


# --- attestation canonicalization ----------------------------------------


def canonical_claim_entry(entry: object, min_confidence: int) -> dict:
    """One claim result, coerced into shape. Total -- never raises.

    An entry that is malformed in any way becomes an `unsubstantiated`
    entry rather than an error, because this runs on the leader's output and a
    leader that returns nonsense must produce a safe verdict, not a fault that
    rotates forever.
    """
    key = ""
    result = UNSUBSTANTIATED
    method = METHOD_MODEL
    confidence = 0
    if isinstance(entry, dict):
        raw_key = entry.get("key")
        if isinstance(raw_key, str) and raw_key in CLAIM_KEYS:
            key = raw_key
        raw_method = entry.get("method")
        if raw_method == METHOD_DETERMINISTIC:
            method = METHOD_DETERMINISTIC
        raw_result = coerce_result(entry.get("result"))
        try:
            confidence = int(entry.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0
        if confidence < 0:
            confidence = 0
        elif confidence > 100:
            confidence = 100
        if method == METHOD_DETERMINISTIC:
            # A deterministic claim's confidence is not a judgment: the check
            # either matched or it did not.
            result = raw_result
            confidence = 100 if result != UNSUBSTANTIATED else 0
        else:
            result, confidence = coerce_model_result(
                entry.get("result"), confidence, min_confidence
            )
    return {"key": key, "result": result, "method": method, "confidence": confidence}


def canonicalize_attestation(raw: object, expected_keys: tuple, min_confidence: int) -> dict:
    """The consensus-verified half of an attestation, in canonical form.

    **Total: there is no input for which this raises.** It runs on the leader's
    output, which is untrusted by construction, and every validator must derive
    the same canonical form from the same bytes for consensus to mean anything.
    A raising path here would let a malformed leader answer become a fault
    rather than a verdict.

    Every expected claim appears in the output. A claim the leader omitted comes
    back `unsubstantiated` -- omission cannot be a way to avoid a claim that
    would have failed.
    """
    entries = {}
    if isinstance(raw, dict):
        listed = raw.get("claims")
        if isinstance(listed, list):
            for item in listed:
                entry = canonical_claim_entry(item, min_confidence)
                if entry["key"]:
                    entries[entry["key"]] = entry

    claims = []
    for key in expected_keys:
        found = entries.get(key)
        if found is None:
            method = (
                METHOD_DETERMINISTIC if key in DETERMINISTIC_CLAIMS else METHOD_MODEL
            )
            found = {
                "key": key,
                "result": UNSUBSTANTIATED,
                "method": method,
                "confidence": 0,
            }
        claims.append(found)

    reachable = 0
    if isinstance(raw, dict):
        try:
            reachable = int(raw.get("sources_reachable", 0))
        except (TypeError, ValueError):
            reachable = 0
        if reachable < 0:
            reachable = 0

    verdict = aggregate([c["result"] for c in claims])

    resolved_by = BY_DETERMINISTIC
    if isinstance(raw, dict):
        raw_by = raw.get("resolved_by")
        if raw_by in (BY_CACHE, BY_LIST, BY_DETERMINISTIC, BY_MODEL):
            resolved_by = raw_by
    if any(c["method"] == METHOD_MODEL and c["result"] != UNSUBSTANTIATED for c in claims):
        resolved_by = BY_MODEL

    return {
        "verdict": verdict,
        "claims": claims,
        "resolved_by": resolved_by,
        "sources_reachable": reachable,
    }


# --- consensus ------------------------------------------------------------


def _bucket(result: str) -> str:
    """After coercion the result *is* the decision bucket."""
    return result if result in RESULTS else UNSUBSTANTIATED


def verdicts_agree(mine: dict, theirs: dict, tol: int) -> bool:
    """Does a validator's own derivation agree with the leader's?

    Compared: the verdict, and every claim's result and method. Confidence is
    compared **only within a shared bucket** -- two validators that both
    substantiate a claim may legitimately differ on how confident they were, and
    a tolerance across buckets would let 74 and 76 "agree" while meaning
    opposite things on either side of the threshold.

    The reachability gate is the rule that cost a deployment to learn in
    Recourse and is repeated here deliberately: a validator that reached nothing
    must not ratify a leader that reached something. Its own all-`unsubstantiated`
    derivation is a fact about its network, not about the vendor, and agreeing
    would let one validator's outage silently become the answer.
    """
    if not isinstance(mine, dict) or not isinstance(theirs, dict):
        return False
    if mine.get("verdict") != theirs.get("verdict"):
        return False

    mine_reach = int(mine.get("sources_reachable", 0) or 0)
    theirs_reach = int(theirs.get("sources_reachable", 0) or 0)
    if mine_reach == 0 and theirs_reach > 0:
        return False

    a = mine.get("claims")
    b = theirs.get("claims")
    if not isinstance(a, list) or not isinstance(b, list) or len(a) != len(b):
        return False

    by_key = {}
    for entry in b:
        if isinstance(entry, dict) and isinstance(entry.get("key"), str):
            by_key[entry["key"]] = entry

    for entry in a:
        if not isinstance(entry, dict):
            return False
        key = entry.get("key")
        other = by_key.get(key)
        if other is None:
            return False
        if _bucket(entry.get("result", "")) != _bucket(other.get("result", "")):
            return False
        if entry.get("method") != other.get("method"):
            return False
        if entry.get("method") == METHOD_DETERMINISTIC:
            # A deterministic check has no tolerance to spend. Two validators
            # that ran the same substring match on the same text either agree
            # exactly or one of them read a different page.
            if int(entry.get("confidence", 0)) != int(other.get("confidence", 0)):
                return False
            continue
        if _bucket(entry.get("result", "")) == UNSUBSTANTIATED:
            # Already the safe value; the confidence behind it is not load-bearing.
            continue
        if abs(int(entry.get("confidence", 0)) - int(other.get("confidence", 0))) > tol:
            return False
    return True
