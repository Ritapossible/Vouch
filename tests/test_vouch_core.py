"""Tests for the decision engine.

The suite is organized around one question: **can anything make this contract
say `substantiated` when it should not?** `TestNothingFailsOpen` is the reason
this file exists; the rest establishes the behaviour it depends on.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import vouch_core as core  # noqa: E402


# --- aggregation ----------------------------------------------------------


class TestAggregate:
    """The verdict rule, exhaustively.

    Three values across up to three claims is small enough to enumerate rather
    than sample, so it is enumerated.
    """

    @pytest.mark.parametrize("results,expected", [
        ([], core.UNSUBSTANTIATED),
        ([core.SUBSTANTIATED], core.SUBSTANTIATED),
        ([core.UNSUBSTANTIATED], core.UNSUBSTANTIATED),
        ([core.CONTRADICTED], core.CONTRADICTED),
        ([core.SUBSTANTIATED, core.SUBSTANTIATED], core.SUBSTANTIATED),
        ([core.SUBSTANTIATED, core.UNSUBSTANTIATED], core.UNSUBSTANTIATED),
        ([core.SUBSTANTIATED, core.CONTRADICTED], core.CONTRADICTED),
        ([core.UNSUBSTANTIATED, core.CONTRADICTED], core.CONTRADICTED),
        ([core.CONTRADICTED, core.CONTRADICTED], core.CONTRADICTED),
    ])
    def test_table(self, results, expected):
        assert core.aggregate(results) == expected

    def test_exhaustive_three_claims(self):
        """Every combination of three results, checked against the stated rule."""
        from itertools import product

        for combo in product(core.RESULTS, repeat=3):
            got = core.aggregate(list(combo))
            if core.CONTRADICTED in combo:
                assert got == core.CONTRADICTED, combo
            elif all(r == core.SUBSTANTIATED for r in combo):
                assert got == core.SUBSTANTIATED, combo
            else:
                assert got == core.UNSUBSTANTIATED, combo

    def test_contradicted_beats_a_full_house_of_substantiated(self):
        """The substituted-address case: everything checks out but the address.

        A real company with a redirected payment address substantiates its name,
        its domain and its service, and is exactly the fraud this contract
        exists to catch. One `contradicted` has to outweigh all of it.
        """
        results = [core.SUBSTANTIATED] * 8 + [core.CONTRADICTED]
        assert core.aggregate(results) == core.CONTRADICTED

    def test_empty_is_not_vacuously_true(self):
        assert core.aggregate([]) == core.UNSUBSTANTIATED
        assert core.aggregate(iter([])) == core.UNSUBSTANTIATED


# --- the invariant --------------------------------------------------------


class TestNothingFailsOpen:
    """**The point of this contract.**

    Every one of these feeds a failure, a malformation, or an attack into the
    engine and asserts the result is never `substantiated`. If any of these ever
    goes red, the contract is worse than useless -- it is a rubber stamp that
    reads like a safety check.
    """

    JUNK = [
        None, "", "  ", 0, 1, -1, [], {}, (), True, False, 3.14,
        "substantiated ", "SUBSTANTIATED", "yes!", "maybe", "unknown",
        "error", "timeout", "null", "None", "undefined", "NaN",
        b"substantiated", ["substantiated"], {"result": "substantiated"},
        float("inf"), float("nan"), "0", "1", "true ", "  yes",
    ]

    @pytest.mark.parametrize("junk", JUNK)
    def test_canonicalize_never_substantiates_junk(self, junk):
        out = core.canonicalize_attestation(junk, (core.CLAIM_PAYMENT_ADDRESS,), 75)
        assert out["verdict"] != core.SUBSTANTIATED

    @pytest.mark.parametrize("junk", JUNK)
    def test_canonicalize_never_raises(self, junk):
        core.canonicalize_attestation(junk, core.CLAIM_KEYS, 75)
        core.canonicalize_attestation({"claims": junk}, core.CLAIM_KEYS, 75)
        core.canonicalize_attestation({"claims": [junk]}, core.CLAIM_KEYS, 75)

    @pytest.mark.parametrize("junk", JUNK)
    def test_coerce_result_is_total(self, junk):
        assert core.coerce_result(junk) in core.RESULTS

    def test_omitted_claim_is_unsubstantiated_not_absent(self):
        """Omission must not be a way to dodge a claim that would have failed."""
        out = core.canonicalize_attestation(
            {"claims": [{"key": core.CLAIM_PAYMENT_ADDRESS,
                         "result": core.SUBSTANTIATED,
                         "method": core.METHOD_DETERMINISTIC}]},
            (core.CLAIM_PAYMENT_ADDRESS, core.CLAIM_LEGAL_NAME),
            75,
        )
        keys = [c["key"] for c in out["claims"]]
        assert keys == [core.CLAIM_PAYMENT_ADDRESS, core.CLAIM_LEGAL_NAME]
        missing = [c for c in out["claims"] if c["key"] == core.CLAIM_LEGAL_NAME][0]
        assert missing["result"] == core.UNSUBSTANTIATED
        assert out["verdict"] == core.UNSUBSTANTIATED

    def test_empty_claim_list_does_not_substantiate(self):
        out = core.canonicalize_attestation(
            {"claims": []}, (core.CLAIM_PAYMENT_ADDRESS,), 75
        )
        assert out["verdict"] == core.UNSUBSTANTIATED

    def test_a_leader_cannot_assert_a_verdict_directly(self):
        """The verdict is derived, never read from the leader's answer.

        A leader claiming `substantiated` while every claim under it failed gets
        the verdict its claims imply, not the one it asked for.
        """
        out = core.canonicalize_attestation(
            {"verdict": core.SUBSTANTIATED,
             "claims": [{"key": core.CLAIM_PAYMENT_ADDRESS,
                         "result": core.UNSUBSTANTIATED,
                         "method": core.METHOD_DETERMINISTIC}]},
            (core.CLAIM_PAYMENT_ADDRESS,),
            75,
        )
        assert out["verdict"] == core.UNSUBSTANTIATED

    def test_confidence_above_100_cannot_buy_a_pass(self):
        out = core.canonicalize_attestation(
            {"claims": [{"key": core.CLAIM_LEGAL_NAME, "result": "substantiated",
                         "method": core.METHOD_MODEL, "confidence": 10 ** 9}]},
            (core.CLAIM_LEGAL_NAME,),
            75,
        )
        assert out["claims"][0]["confidence"] == 100

    def test_negative_confidence_does_not_underflow_into_a_pass(self):
        result, conf = core.coerce_model_result("substantiated", -5, 75)
        assert result == core.UNSUBSTANTIATED
        assert conf == 0


# --- the model's ceiling --------------------------------------------------


class TestModelCannotContradict:
    """A model may substantiate or fail to. It may never contradict.

    `contradicted` is the contract's strongest signal and it is reserved for
    deterministic checks. If a model could reach it, any page that says "this
    company is a fraud" becomes a weapon -- see docs/SECURITY.md on injected
    accusations.
    """

    @pytest.mark.parametrize("answer", [
        "contradicted", "CONTRADICTED", " contradicted ", "conflicts",
        "conflicting", "false", "no", "definitely contradicted",
    ])
    def test_model_answer_never_reaches_contradicted(self, answer):
        result, _ = core.coerce_model_result(answer, 100, 75)
        assert result != core.CONTRADICTED

    def test_injected_accusation_cannot_flip_a_verdict(self):
        """Page text accusing the vendor produces `unsubstantiated`, not worse."""
        out = core.canonicalize_attestation(
            {"claims": [{"key": core.CLAIM_LEGAL_NAME, "result": "contradicted",
                         "method": core.METHOD_MODEL, "confidence": 100}]},
            (core.CLAIM_LEGAL_NAME,),
            75,
        )
        assert out["claims"][0]["result"] == core.UNSUBSTANTIATED
        assert out["verdict"] == core.UNSUBSTANTIATED

    def test_a_deterministic_claim_may_contradict(self):
        """The privilege the model lacks, the substring check has."""
        out = core.canonicalize_attestation(
            {"claims": [{"key": core.CLAIM_PAYMENT_ADDRESS, "result": "contradicted",
                         "method": core.METHOD_DETERMINISTIC}]},
            (core.CLAIM_PAYMENT_ADDRESS,),
            75,
        )
        assert out["claims"][0]["result"] == core.CONTRADICTED
        assert out["verdict"] == core.CONTRADICTED

    @pytest.mark.parametrize("conf", [0, 1, 50, 74])
    def test_below_threshold_is_unsubstantiated(self, conf):
        assert core.coerce_model_result("substantiated", conf, 75)[0] == core.UNSUBSTANTIATED

    @pytest.mark.parametrize("conf", [75, 76, 99, 100])
    def test_at_or_above_threshold_substantiates(self, conf):
        assert core.coerce_model_result("substantiated", conf, 75)[0] == core.SUBSTANTIATED

    def test_unsubstantiated_carries_no_confidence(self):
        """Reporting a high confidence behind a failure reads as a finding."""
        assert core.coerce_model_result("substantiated", 40, 75)[1] == 0
        assert core.coerce_model_result("contradicted", 99, 75)[1] == 0


# --- claims and cache keys -----------------------------------------------


class TestClaims:
    def test_unknown_key_is_rejected_not_ignored(self):
        pairs, err = core.canonical_claims({"legal_nmae": "Acme"})
        assert err == core.REASON_UNKNOWN_CLAIM
        assert pairs == ()

    def test_known_keys_accepted(self):
        pairs, err = core.canonical_claims({"legal_name": "Acme", "service": "hosting"})
        assert err == ""
        assert pairs == (("legal_name", "Acme"), ("service", "hosting"))

    def test_ordering_does_not_change_the_key(self):
        a, _ = core.canonical_claims({"legal_name": "Acme", "service": "hosting"})
        b, _ = core.canonical_claims({"service": "hosting", "legal_name": "Acme"})
        assert core.cache_key("0xabc", a) == core.cache_key("0xabc", b)

    def test_any_claim_change_changes_the_key(self):
        """The property that makes caching safe."""
        base, _ = core.canonical_claims({"payment_address": "0xaaa", "legal_name": "Acme"})
        moved, _ = core.canonical_claims({"payment_address": "0xbbb", "legal_name": "Acme"})
        assert core.cache_key("0xabc", base) != core.cache_key("0xabc", moved)

    def test_different_payee_changes_the_key(self):
        pairs, _ = core.canonical_claims({"legal_name": "Acme"})
        assert core.cache_key("0xaaa", pairs) != core.cache_key("0xbbb", pairs)

    def test_values_cannot_be_arranged_to_collide(self):
        """Length-prefixing, not separator-joining.

        Without it, `{"legal_name": "a:b"}` and `{"legal_name": "a", ...}` could
        be made to serialize identically.
        """
        a, _ = core.canonical_claims({"legal_name": "acme", "service": "hosting"})
        b, _ = core.canonical_claims({"legal_name": "acmeservice", "service": "hosting"})
        assert core.cache_key("0x1", a) != core.cache_key("0x1", b)

    @pytest.mark.parametrize("bad", [
        None, "", [], 0, {"legal_name": None}, {"legal_name": ""},
        {"legal_name": "  "}, {"legal_name": 5}, {5: "x"},
        {"legal_name": "x" * 513},
    ])
    def test_malformed_claims_rejected(self, bad):
        _, err = core.canonical_claims(bad)
        assert err != ""


# --- consensus ------------------------------------------------------------


class TestVerdictsAgree:
    def _att(self, result=core.SUBSTANTIATED, conf=90, method=core.METHOD_MODEL, reach=1):
        return core.canonicalize_attestation(
            {"claims": [{"key": core.CLAIM_LEGAL_NAME, "result": result,
                         "method": method, "confidence": conf}],
             "sources_reachable": reach},
            (core.CLAIM_LEGAL_NAME,), 75,
        )

    def test_identical_agree(self):
        assert core.verdicts_agree(self._att(), self._att(), 15)

    def test_confidence_within_tolerance_agrees(self):
        assert core.verdicts_agree(self._att(conf=90), self._att(conf=80), 15)

    def test_confidence_outside_tolerance_disagrees(self):
        assert not core.verdicts_agree(self._att(conf=100), self._att(conf=76), 15)

    def test_tolerance_does_not_span_the_threshold(self):
        """74 and 76 are 2 apart and mean opposite things.

        A tolerance applied across buckets would let them agree. Applied within
        a bucket, they land in different buckets and disagree.
        """
        low = self._att(conf=74)   # -> unsubstantiated
        high = self._att(conf=76)  # -> substantiated
        assert low["verdict"] != high["verdict"]
        assert not core.verdicts_agree(low, high, 15)

    def test_different_verdicts_disagree(self):
        assert not core.verdicts_agree(
            self._att(result=core.SUBSTANTIATED), self._att(result=core.UNSUBSTANTIATED), 15
        )

    def test_deterministic_claims_have_no_tolerance(self):
        a = self._att(method=core.METHOD_DETERMINISTIC, result=core.SUBSTANTIATED)
        b = dict(a)
        b["claims"] = [dict(a["claims"][0])]
        b["claims"][0]["confidence"] = 50
        assert not core.verdicts_agree(a, b, 99)

    def test_validator_that_reached_nothing_does_not_ratify(self):
        """The reachability gate.

        A validator whose own fetches all failed derived `unsubstantiated` as a
        fact about its network, not about the vendor. It must vote to rotate
        rather than ratify a leader that actually reached the page.
        """
        blind = self._att(result=core.UNSUBSTANTIATED, reach=0)
        sighted = self._att(result=core.UNSUBSTANTIATED, reach=2)
        assert not core.verdicts_agree(blind, sighted, 15)

    def test_both_blind_may_agree(self):
        assert core.verdicts_agree(
            self._att(result=core.UNSUBSTANTIATED, reach=0),
            self._att(result=core.UNSUBSTANTIATED, reach=0),
            15,
        )

    def test_sighted_validator_may_reject_a_blind_leader(self):
        sighted = self._att(result=core.UNSUBSTANTIATED, reach=2)
        blind = self._att(result=core.UNSUBSTANTIATED, reach=0)
        # Same verdict, and the gate is one-directional by design: reaching more
        # than the leader is not itself a disagreement.
        assert core.verdicts_agree(sighted, blind, 15)

    @pytest.mark.parametrize("junk", [None, "", 0, [], "x", {"verdict": "substantiated"}])
    def test_junk_never_agrees(self, junk):
        assert not core.verdicts_agree(self._att(), junk, 15)
        assert not core.verdicts_agree(junk, self._att(), 15)


class TestLimits:
    def test_defaults_are_valid(self):
        assert core.validate_limits(core.Limits(3, 200000, 75, 15, 86400)) == ""

    @pytest.mark.parametrize("args", [
        (0, 200000, 75, 15, 86400),
        (17, 200000, 75, 15, 86400),
        (3, 999, 75, 15, 86400),
        (3, 200000, 101, 15, 86400),
        (3, 200000, 75, -1, 86400),
        (3, 200000, -1, 15, 86400),
    ])
    def test_out_of_range_rejected(self, args):
        assert core.validate_limits(core.Limits(*args)) != ""
