"""Make `contracts/vouch.py` importable off-chain.

Two things stand between a test process and the real contract module:

1. `gl.Contract` imports `_genlayer_wasi`, the VM host module. `tests/stubs/`
   supplies a stand-in whose every entry point raises.
2. The SDK decodes its message from **file descriptor 0 at import time**
   (`genlayer/_internal/msg.py`). An empty stdin is a `DecodingError`, so fd 0
   is pointed at a recorded message before anything imports genlayer.

Both are done here rather than in the test module because conftest runs before
collection, which is when the import happens.
"""

import os
import pathlib
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
MESSAGE = HERE / "fixtures" / "message.calldata"


def _find_sdk() -> pathlib.Path | None:
    """Locate py-lib-genlayer-std, wherever this machine keeps it.

    This used to be the single hard-coded path `/tmp/std`, which is a scratch
    directory that exists only on the machine that happened to unpack the SDK
    there. Anyone cloning the repository got two collection errors instead of a
    test run, which is a poor first impression and says nothing about the
    contract. The SDK is looked up rather than assumed now, and its absence is
    a skip with a reason rather than a crash.
    """
    if env := os.environ.get("GENLAYER_STD"):
        candidate = pathlib.Path(env)
        if (candidate / "genlayer").is_dir():
            return candidate

    candidates = [pathlib.Path("/tmp/std")]
    # `genvm-lint` unpacks the same library; reuse it rather than ask for a
    # second copy.
    cache = pathlib.Path.home() / ".cache" / "genvm-linter" / "extracted"
    if cache.is_dir():
        candidates.extend(sorted(cache.glob("*/py-lib-genlayer-std/*")))

    for candidate in candidates:
        if (candidate / "genlayer").is_dir():
            return candidate
    return None


SDK = _find_sdk()

# The SDK uses PEP 695 generics (`class Lazy[T]:`), which is a syntax error
# before 3.12. Importing it on an older interpreter fails during collection,
# so the requirement is stated here instead.
SDK_USABLE = SDK is not None and sys.version_info >= (3, 12)

if SDK_USABLE:
    SKIP_REASON = ""
elif SDK is None:
    SKIP_REASON = (
        "py-lib-genlayer-std not found. Set GENLAYER_STD to its directory, or "
        "run `genvm-lint` once to populate ~/.cache/genvm-linter."
    )
else:
    SKIP_REASON = (
        f"py-lib-genlayer-std needs Python 3.12 or newer for PEP 695 generics; "
        f"this is {sys.version_info.major}.{sys.version_info.minor}."
    )

# Available to the modules that import the contract itself:
#     pytestmark = pytest.mark.skipif(not SDK_USABLE, reason=SKIP_REASON)
requires_sdk = pytest.mark.skipif(not SDK_USABLE, reason=SKIP_REASON)

_paths = [str(HERE), str(HERE / "stubs")]
if SDK is not None:
    _paths.append(str(SDK))
for path in _paths:
    if path not in sys.path:
        sys.path.insert(0, path)

if MESSAGE.exists():
    # Replace fd 0 itself, not `sys.stdin`: the SDK reads the descriptor
    # directly with `io.FileIO(0)`, which ignores anything done to `sys.stdin`.
    _fh = open(MESSAGE, "rb")
    os.dup2(_fh.fileno(), 0)
