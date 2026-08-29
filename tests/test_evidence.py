"""Tests for URL validation, markup flattening, and the address check.

The address check is the highest-value check in the contract and the one that
defeats payment redirection, so it gets the most adversarial attention here.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import vouch_evidence as ev  # noqa: E402

ADDR = "0xabc1234567890123456789012345678901234567"
OTHER = "0xdef1234567890123456789012345678901234567"


def page(html: str) -> str:
    """The pipeline a fetched source goes through before any check runs."""
    return ev.normalize(ev.html_to_text(html))


# --- URL validation -------------------------------------------------------


class TestValidateUrl:
    @pytest.mark.parametrize("url", [
        "https://vendor.example",
        "https://vendor.example/pay",
        "https://sub.vendor.example/a/b?c=d#e",
        "https://vendor.example:8443/pay",
    ])
    def test_accepts_public_https(self, url):
        assert ev.validate_url(url) == ev.URL_OK

    @pytest.mark.parametrize("url,reason", [
        ("http://vendor.example", ev.URL_NOT_HTTPS),
        ("ftp://vendor.example", ev.URL_NOT_HTTPS),
        ("//vendor.example", ev.URL_NOT_HTTPS),
        ("", ev.URL_NO_HOST),
        ("https://", ev.URL_NO_HOST),
    ])
    def test_rejects_by_scheme(self, url, reason):
        assert ev.validate_url(url) == reason

    def test_rejects_userinfo(self):
        """`https://evil.com@vendor.example/` reads as the vendor and fetches evil."""
        assert ev.validate_url("https://evil.com@vendor.example/") == ev.URL_HAS_USERINFO
        assert ev.validate_url("https://a:b@vendor.example/") == ev.URL_HAS_USERINFO

    @pytest.mark.parametrize("host", [
        "localhost", "127.0.0.1", "10.0.0.1", "192.168.1.1", "172.16.0.1",
        "169.254.169.254", "0.0.0.0", "metadata.google.internal",
        "thing.local", "box.internal", "239.1.1.1",
    ])
    def test_rejects_non_public_hosts(self, host):
        """A validator fetching these reaches its own infrastructure.

        169.254.169.254 is the cloud metadata endpoint and is the one an
        attacker actually reaches for.
        """
        assert ev.validate_url(f"https://{host}/x") == ev.URL_NOT_PUBLIC

    @pytest.mark.parametrize("url", ["https://[::1]/x", "https://[fe80::1]/x", "https://[fc00::1]/x"])
    def test_rejects_non_public_ipv6(self, url):
        assert ev.validate_url(url) == ev.URL_NOT_PUBLIC

    def test_rejects_overlong(self):
        assert ev.validate_url("https://vendor.example/" + "a" * 3000) == ev.URL_TOO_LONG

    @pytest.mark.parametrize("bad", [None, 5, [], {}, b"https://x.com"])
    def test_rejects_non_strings(self, bad):
        assert ev.validate_url(bad) != ev.URL_OK

    def test_public_ipv4_allowed(self):
        assert ev.validate_url("https://93.184.216.34/x") == ev.URL_OK


class TestHost:
    @pytest.mark.parametrize("url,host", [
        ("https://Vendor.Example/pay", "vendor.example"),
        ("https://vendor.example:8443/pay", "vendor.example"),
        ("https://vendor.example./pay", "vendor.example"),
    ])
    def test_host_of(self, url, host):
        assert ev.host_of(url) == host

    @pytest.mark.parametrize("host,reg", [
        ("vendor.example", "vendor.example"),
        ("shop.vendor.example", "vendor.example"),
        ("a.b.c.vendor.example", "vendor.example"),
        ("localhost", "localhost"),
    ])
    def test_registrable(self, host, reg):
        assert ev.registrable(host) == reg


# --- markup flattening ----------------------------------------------------


class TestHtmlToText:
    def test_keeps_attribute_values(self):
        """A payment address very often lives in an href, and text mode drops it."""
        out = ev.html_to_text(f'<a href="ethereum:{ADDR}">Pay</a>')
        assert ADDR in out

    def test_drops_tag_names(self):
        """The bug that makes `compact` safe: `0x12<b>34</b>` must not be `0x12b34`."""
        out = ev.html_to_text("0x12<b>34</b>")
        assert "b" not in out.replace(" ", "")

    def test_drops_script_bodies(self):
        out = ev.html_to_text(f'<script>var k="{OTHER}"</script>hello')
        assert OTHER not in out

    def test_drops_style_bodies(self):
        out = ev.html_to_text("<style>.a{color:red}</style>hello")
        assert "color" not in out

    @pytest.mark.parametrize("raw,want", [
        ("&amp;", "&"), ("&lt;", "<"), ("&#48;", "0"), ("&#x30;", "0"),
        ("&nbsp;", " "), ("&unknown;", "&unknown;"),
    ])
    def test_entities(self, raw, want):
        assert ev.html_to_text(raw) == want

    @pytest.mark.parametrize("bad", [None, 5, [], b"x"])
    def test_non_strings(self, bad):
        assert ev.html_to_text(bad) == ""

    def test_unterminated_tag_does_not_hang(self):
        assert isinstance(ev.html_to_text("<a href='x"), str)


class TestNormalize:
    def test_confusables_folded(self):
        """A vendor name spelled with a Cyrillic `e` is a different string."""
        assert ev.normalize("\u0410cme") == ev.normalize("Acme")

    def test_zero_width_stripped(self):
        assert ev.normalize("Ac\u200bme") == "acme"

    def test_case_and_punctuation(self):
        assert ev.normalize("ACME, Inc.") == "acme inc"

    def test_runs_collapsed(self):
        assert ev.normalize("a    b\n\nc") == "a b c"


# --- the address check ----------------------------------------------------


class TestAddressPresent:
    """The check that defeats payment-redirection fraud."""

    @pytest.mark.parametrize("html", [
        f"Pay us at {ADDR}",
        f"Pay us at {ADDR.upper()}",
        f'<a href="ethereum:{ADDR}">Pay</a>',
        f"<b>{ADDR}</b>",
        f"0xabc12345678901234567<b>89012345678901234567</b>",
        f"<div><span>{ADDR}</span></div>",
        f"Wallet:&nbsp;{ADDR}",
        f"  {ADDR}  ",
    ])
    def test_finds_the_address(self, html):
        assert ev.address_present(ADDR, page(html))

    @pytest.mark.parametrize("html", [
        "we accept bank transfer only",
        "",
        f"Pay us at {OTHER}",
        "0xabc123",
    ])
    def test_absent_when_absent(self, html):
        assert not ev.address_present(ADDR, page(html))

    def test_bare_form_without_prefix(self):
        assert ev.address_present(ADDR, page(f"address: {ADDR[2:]}"))

    @pytest.mark.parametrize("bad", [None, "", "0x", "0xzz", "not-an-address", 5, []])
    def test_malformed_needle_never_matches(self, bad):
        assert not ev.address_present(bad, page(f"Pay {ADDR}"))

    def test_a_script_only_address_does_not_count(self):
        assert not ev.address_present(ADDR, page(f'<script>k="{ADDR}"</script>'))


class TestCanonicalAddress:
    def test_lowercases(self):
        assert ev.canonical_address(ADDR.upper()) == ADDR

    @pytest.mark.parametrize("bad", [
        None, "", "0x", "abc", "0x" + "z" * 40, "0x" + "a" * 39, "0x" + "a" * 41, 5, [],
    ])
    def test_rejects_malformed(self, bad):
        assert ev.canonical_address(bad) == ""


class TestForeignAddresses:
    """What separates `contradicted` from `unsubstantiated`."""

    def test_finds_a_substituted_address(self):
        assert ev.foreign_addresses(page(f"Send to {OTHER}"), ADDR) == (OTHER,)

    def test_own_address_is_not_foreign(self):
        assert ev.foreign_addresses(page(f"Send to {ADDR}"), ADDR) == ()

    def test_silence_is_not_contradiction(self):
        """A page naming no address at all is silent, and silence is not conflict."""
        assert ev.foreign_addresses(page("call us"), ADDR) == ()

    def test_finds_address_split_by_markup(self):
        html = "0xdef12345678901234567<b>89012345678901234567</b>"
        assert ev.foreign_addresses(page(html), ADDR) == (OTHER,)

    def test_longer_hex_run_is_not_an_address(self):
        """A 41st hex character means this is a hash, not an address.

        Reading one as an address would manufacture a `contradicted` verdict out
        of a minified bundle or a commit id.
        """
        assert ev.foreign_addresses(page("0x" + "d" * 48), ADDR) == ()

    def test_script_hex_is_excluded(self):
        assert ev.foreign_addresses(page(f'<script>k="{OTHER}"</script>'), ADDR) == ()

    def test_sorted_and_deduplicated(self):
        """Every validator that saw the same page must produce the same tuple."""
        third = "0x1111111111111111111111111111111111111111"
        out = ev.foreign_addresses(page(f"{OTHER} {third} {OTHER}"), ADDR)
        assert out == tuple(sorted({OTHER, third}))

    @pytest.mark.parametrize("bad", [None, "", 5, []])
    def test_junk_page(self, bad):
        assert ev.foreign_addresses(bad, ADDR) == ()


class TestTruncateAndDigest:
    def test_truncate_reports_the_cut(self):
        assert ev.truncate("abcdef", 3) == ("abc", True)
        assert ev.truncate("abc", 10) == ("abc", False)

    def test_digest_is_stable_across_processes(self):
        """blake2b, never the builtin `hash()`, which is salted per process."""
        assert ev.digest("abc") == ev.digest("abc")
        assert ev.digest("abc") != ev.digest("abd")
        assert ev.digest("abc") == "cf4ab791c62b8d2b2109c90275287816"


class TestRegressions:
    """Bugs found while building this module, kept fixed."""

    @pytest.mark.parametrize("spoofed", [
        "\u0410cme",   # Cyrillic capital \u0410
        "\u0430cme",   # Cyrillic small \u0430
        "ACME",   # plain, for the control
        "\u0410\u0441me",   # Cyrillic \u0410 and \u0441
    ])
    def test_uppercase_confusables_fold(self, spoofed):
        """The ported ordering folded lowercase confusables only.

        DedupRegistry applies the confusable map before casefolding, so an
        uppercase Cyrillic A passed through a lowercase-only map untouched and
        casefolded afterwards into a Cyrillic small a that nothing remapped.
        A vendor name is exactly where a capital appears.
        """
        assert ev.normalize(spoofed) == "acme"

    def test_adjacent_addresses_are_both_found(self):
        """De-spacing glued neighbouring addresses into one 80-char hex run.

        The long-run guard then rejected both, so a page listing several payment
        addresses -- one of them substituted -- produced no foreign addresses at
        all and downgraded a `contradicted` to `unsubstantiated`.
        """
        third = "0x1111111111111111111111111111111111111111"
        out = ev.foreign_addresses(page(f"{OTHER} {third}"), ADDR)
        assert out == tuple(sorted({OTHER, third}))

    def test_split_and_adjacent_together(self):
        """Both scan forms pulling their weight in one page."""
        third = "0x1111111111111111111111111111111111111111"
        html = f"{OTHER} {third} 0x2222222222222222<b>222222222222222222222222</b>"
        fourth = "0x2222222222222222222222222222222222222222"
        assert ev.foreign_addresses(page(html), ADDR) == tuple(sorted({OTHER, third, fourth}))


class TestDomainOf:
    """The `domain` claim's hostname parsing.

    Reported by a reviewer. The old code tried to remove a leading scheme by
    stripping its characters from the front, which removes a *set of
    characters* rather than a prefix, so bare domains were silently mangled.
    """

    @pytest.mark.parametrize("value,expected", [
        # The exact cases the old code broke. `shop.com` became `op.com`.
        ("shop.com", "shop.com"),
        ("thing.io", "thing.io"),
        ("pay.example", "pay.example"),
        ("stripe.com", "stripe.com"),
        ("post.co.uk", "post.co.uk"),
        ("https.example", "https.example"),
        ("ttt.com", "ttt.com"),
    ])
    def test_bare_domains_survive(self, value, expected):
        assert ev.domain_of(value) == expected

    @pytest.mark.parametrize("value,expected", [
        ("https://shop.com", "shop.com"),
        ("http://shop.com/pay", "shop.com"),
        ("https://a.b.co.uk/x?y#z", "a.b.co.uk"),
        ("HTTPS://Shop.COM", "shop.com"),
        ("shop.com:8443", "shop.com"),
        ("shop.com/pay", "shop.com"),
        ("shop.com.", "shop.com"),
    ])
    def test_urls_and_authorities(self, value, expected):
        assert ev.domain_of(value) == expected

    @pytest.mark.parametrize("value", [
        "", "   ", "x", "localhost", "not a host", "a@b.com",
        "shop.com:not-a-port", None, 5, [], "://shop.com",
    ])
    def test_unusable_values_return_empty(self, value):
        assert ev.domain_of(value) == ""

    @pytest.mark.parametrize("value,old_output", [
        ("shop.com", "op.com"),
        ("thing.io", "ing.io"),
        ("pay.example", "ay.example"),
        ("stripe.com", "ripe.com"),
    ])
    def test_the_old_bug_would_have_failed_this(self, value, old_output):
        """The regression, stated against what the old code actually returned.

        The broken outputs are recorded here rather than recomputed, so this
        test does not carry a copy of the defect it exists to prevent. Each
        pair is what the character-stripping version produced against what the
        parser returns now.
        """
        assert old_output != value, "the recorded output should differ from the input"
        assert ev.domain_of(value) == value

    def test_a_mangled_domain_manufactured_a_contradiction(self):
        """Why this mattered rather than merely being untidy.

        A `domain` claim that does not match its source is `contradicted`, the
        strongest verdict here. Mangling the claim turned a correct claim into
        an accusation.
        """
        assert ev.registrable(ev.domain_of("shop.com")) == "shop.com"
        assert ev.registrable(ev.host_of("https://shop.com/pay")) == "shop.com"
