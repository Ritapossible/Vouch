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

HERE = pathlib.Path(__file__).resolve().parent
SDK = pathlib.Path("/tmp/std")
MESSAGE = HERE / "fixtures" / "message.calldata"

for path in (str(HERE), str(HERE / "stubs"), str(SDK)):
    if path not in sys.path:
        sys.path.insert(0, path)

if MESSAGE.exists():
    # Replace fd 0 itself, not `sys.stdin`: the SDK reads the descriptor
    # directly with `io.FileIO(0)`, which ignores anything done to `sys.stdin`.
    _fh = open(MESSAGE, "rb")
    os.dup2(_fh.fileno(), 0)
