"""Loads the generated contract exactly once for the whole test session.

The SDK refuses a second contract class in one process -- `only one contract is
allowed` -- so two test modules each calling `spec.loader.exec_module` on
`contracts/vouch.py` is a collection error, not two independent loads. This
module owns the single load and everything else imports `V` from here.

What is imported is the **generated artifact**, not the `lib/` sources. That is
the point: these tests exercise the file that deploys.
"""

import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "vouch.py"

_spec = importlib.util.spec_from_file_location("vouch_contract", CONTRACT)
V = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(V)

ADDR = "0xabc1234567890123456789012345678901234567"
OTHER = "0xdef1234567890123456789012345678901234567"


def flatten(html: str) -> str:
    """The tail of the real fetch pipeline: markup -> text -> normalized."""
    return V.normalize(V.html_to_text(html or ""))
