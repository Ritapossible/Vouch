"""Prove a deployed address holds exactly the source in this repository.

The check a reviewer should not have to do by hand, and the one that caught a
real drift: `contracts/vouch.py` said `RENDER_WAIT = "0ms"` while the deployed
contract had `wait_after_loaded="1000ms"` compiled in. Both files pass every
local test in that state, because the drift is not in the repository at all --
it is between the repository and the chain, and nothing local can see it.

    python tools/verify_deployment.py                # every recorded deployment
    python tools/verify_deployment.py --network studionet

Exit status is non-zero on any mismatch, so this belongs in a pre-submission
gate rather than in a reviewer's afternoon.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import pathlib
import re
import shutil
import subprocess
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent

# The deployments this repository claims. Kept here rather than read from the
# README so that a stale README cannot make this pass.
DEPLOYMENTS = {
    "studionet": {
        "address": "0xaE6737769F331c5A47Ac64603BF523aC5a6C7271",
        "rpc": "https://studio.genlayer.com/api",
        "artifact": ROOT / "contracts" / "vouch.py",
        # studio takes a bare address; bradbury wants an object. The difference
        # is not documented anywhere and is worth having written down.
        "params": lambda addr: [addr],
    },
    "bradbury": {
        "address": "0xD82826C13cAbdc372a35E6CB5DB5466842470a51",
        "rpc": "https://rpc-bradbury.genlayer.com",
        "artifact": ROOT / "dist" / "vouch.min.py",
        "params": lambda addr: [{"address": addr}],
    },
}


def _post(rpc: str, body: bytes) -> dict:
    """POST the JSON-RPC call, preferring `curl` when it is available.

    `urllib` is the obvious choice and it is the one that breaks first: behind a
    corporate or sandbox proxy it needs the CA bundle wiring that `curl` already
    has, and the failure is a bare 403 that looks like the node rejecting you.
    Trying `curl` first and falling back keeps this runnable in both places.
    """
    curl = shutil.which("curl")
    if curl:
        completed = subprocess.run(
            [curl, "-sS", "-X", "POST", rpc,
             "-H", "content-type: application/json",
             "--data-binary", "@-", "--max-time", "120"],
            input=body, capture_output=True,
        )
        if completed.returncode == 0 and completed.stdout.strip():
            return json.loads(completed.stdout)

    request = urllib.request.Request(
        rpc, data=body, headers={"content-type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def fetch_source(rpc: str, params: list) -> str:
    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "gen_getContractCode", "params": params}
    ).encode()
    payload = _post(rpc, body)
    if payload.get("error"):
        raise SystemExit(f"{rpc}: {payload['error']}")
    result = payload.get("result")
    if isinstance(result, dict):
        result = result.get("code", "")
    if not isinstance(result, str) or not result:
        raise SystemExit(f"{rpc}: no source returned")
    if not result.strip().startswith("#"):
        result = base64.b64decode(result).decode("utf-8")
    return result


def render_wait(source: str) -> str:
    found = re.findall(r"""RENDER_WAIT ?= ?['"]([0-9]+ms)['"]""", source)
    if found:
        return found[0]
    # The pre-fix shape: the value inlined at the call site with no constant.
    inline = re.findall(r"""wait_after_loaded=['"]([0-9]+ms)['"]""", source)
    return f"{inline[0]} (inlined, no RENDER_WAIT)" if inline else "not found"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", choices=sorted(DEPLOYMENTS))
    args = parser.parse_args()

    targets = [args.network] if args.network else sorted(DEPLOYMENTS)
    failures = 0

    for name in targets:
        spec = DEPLOYMENTS[name]
        local = spec["artifact"].read_text(encoding="utf-8")
        deployed = fetch_source(spec["rpc"], spec["params"](spec["address"]))

        local_sha = hashlib.sha256(local.encode()).hexdigest()
        deployed_sha = hashlib.sha256(deployed.encode()).hexdigest()
        same = local_sha == deployed_sha

        print(f"{name}")
        print(f"  address        {spec['address']}")
        print(f"  artifact       {spec['artifact'].relative_to(ROOT)}")
        print(f"  deployed sha   {deployed_sha}")
        print(f"  repository sha {local_sha}")
        print(f"  RENDER_WAIT    deployed {render_wait(deployed)} / repo {render_wait(local)}")
        print(f"  {'MATCH' if same else 'MISMATCH'}")
        if not same:
            failures += 1
            print(f"    deployed {len(deployed)} bytes, repository {len(local)} bytes")

    if failures:
        print(f"\n{failures} deployment(s) do not match this repository", file=sys.stderr)
        return 1
    print("\nevery deployment matches the source in this repository")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
