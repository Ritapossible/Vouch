"""Produce the evidence that `contracts/vouch.py` is the only contract.

Three questions a reviewer has to answer about a submission, answered here in
one command and recorded to `evidence/validation.json`:

1. *Which file is the contract?* Every `.py` file in the tree is classified as
   contract or build input, by the same rule the GenVM linter uses. Exactly one
   may come back a contract.
2. *Is that file lint-clean?* `genvm-lint check` runs against it and only it.
   Pointing the linter at a build input is a category error -- it has no
   contract class, so it reports E105 -- and this script never does it.
3. *Does the deployed source match?* The contract's SHA-256 is recorded, so the
   source shown on the Explorer can be compared byte for byte.

    python tools/verify.py            # write evidence/validation.json
    python tools/verify.py --check    # verify without rewriting the record

Exit status is non-zero if any answer is wrong, which makes this usable as the
pre-deploy and pre-submission gate.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys

import build_contract

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "contracts" / "vouch.py"
EVIDENCE = ROOT / "evidence" / "validation.json"

CONTRACT_REL = "contracts/vouch.py"


# `dist/` holds a generated, prose-stripped copy of the contract in `contracts/`,
# emitted by `tools/build_min.py` for networks that reject the full source on
# size. It is the *same* contract, so counting it as a second one would be
# wrong -- but it is a real contract source, so silently ignoring it would be
# worse. It is excluded from the uniqueness count here and checked separately by
# `check_compact_artifact`, which proves it is a faithful copy rather than an
# independent thing that happens to live nearby.
GENERATED_DIRS = {"dist"}


def python_files(include_generated: bool = False) -> list[pathlib.Path]:
    """Every Python file in the repository, ignoring dotted directories."""
    return sorted(
        p
        for p in ROOT.rglob("*.py")
        if not any(
            part.startswith(".") or part == "__pycache__"
            for part in p.relative_to(ROOT).parts
        )
        and (include_generated or not (set(p.relative_to(ROOT).parts) & GENERATED_DIRS))
    )


def check_compact_artifact(failures: list) -> dict | None:
    """Prove `dist/` is a faithful copy of the contract, not a second contract."""
    min_path = ROOT / "dist" / "recourse.min.py"
    if not min_path.exists():
        return None

    import importlib.util

    spec = importlib.util.spec_from_file_location("build_min", ROOT / "tools" / "build_min.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    canonical_src = CONTRACT.read_text(encoding="utf-8")
    compact_src = min_path.read_text(encoding="utf-8")

    if mod.public_surface(canonical_src) != mod.public_surface(compact_src):
        failures.append("dist/recourse.min.py exposes a different public surface")
    if mod.strip_prose(canonical_src) != compact_src:
        failures.append("dist/recourse.min.py is stale - run `python tools/build_min.py`")
    if canonical_src.splitlines()[0] != compact_src.splitlines()[0]:
        failures.append("dist/recourse.min.py pins a different runner")

    return {
        "path": "dist/recourse.min.py",
        "sha256": hashlib.sha256(min_path.read_bytes()).hexdigest(),
        "bytes": len(min_path.read_bytes()),
        "canonical_bytes": len(CONTRACT.read_bytes()),
    }


def declares_contract(path: pathlib.Path) -> bool:
    """Whether the linter would recognize this file as a contract source.

    Mirrors `genvm_linter.validate.sdk_loader.find_contract_class`: a class that
    derives from `Contract`, or one carrying a `@gl.public`-decorated method.
    Done statically, because classifying a file must not require importing it.
    """
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.ClassDef):
            continue

        for base in node.bases:
            name = base.attr if isinstance(base, ast.Attribute) else getattr(base, "id", "")
            if name == "Contract":
                return True

        for item in ast.walk(node):
            for dec in getattr(item, "decorator_list", []):
                target = dec.func if isinstance(dec, ast.Call) else dec
                while isinstance(target, ast.Attribute):
                    if target.attr == "public":
                        return True
                    target = target.value
    return False


# The linter loads the GenVM SDK to run its `validate` stage, and that SDK uses
# PEP 695 generic syntax (`class Lazy[T]`). On Python 3.11 the import is a
# SyntaxError and the linter reports "Failed to load SDK: invalid syntax
# (types.py, line 87)" -- which reads like a broken SDK rather than the version
# complaint it actually is. Lint still passes there, so a run on 3.11 looks
# almost fine while silently skipping every semantic check.
#
# So the interpreter is chosen deliberately rather than inherited, and the one
# used is recorded in the evidence file.
MIN_PYTHON = (3, 12)


def find_linter() -> list[str] | None:
    """Return the argv prefix that runs `genvm-lint` on a new enough Python."""
    if sys.version_info >= MIN_PYTHON:
        found = shutil.which("genvm-lint")
        if found:
            return [found]
        scripts = pathlib.Path(sys.executable).parent
        for candidate in (scripts / "Scripts" / "genvm-lint.exe", scripts / "genvm-lint"):
            if candidate.exists():
                return [str(candidate)]

    # This interpreter is too old, or has no linter. Look for one that is not.
    for name in ("python3.13", "python3.12", "python3"):
        exe = shutil.which(name)
        if not exe:
            continue
        probe = subprocess.run(
            [exe, "-c", "import sys, genvm_linter; print(sys.version_info[:2])"],
            capture_output=True,
            text=True,
        )
        if probe.returncode == 0 and probe.stdout.strip() >= "(3, 12)":
            return [exe, "-m", "genvm_linter.cli"]

    if sys.version_info < MIN_PYTHON:
        return None
    found = shutil.which("genvm-lint")
    return [found] if found else None


def run_linter(linter: list[str], command: str) -> dict:
    """Run one `genvm-lint` subcommand against the contract and parse its JSON."""
    proc = subprocess.run(
        [*linter, command, str(CONTRACT), "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=ROOT,
    )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = {"ok": False, "raw_stdout": proc.stdout, "raw_stderr": proc.stderr}
    return {"command": f"genvm-lint {command} {CONTRACT_REL} --json", "exit_code": proc.returncode, "result": payload}


def linter_version() -> str:
    try:
        from importlib.metadata import version

        return f"genvm-linter {version('genvm-linter')}"
    except Exception:
        return "genvm-linter (version unavailable)"


def git_commit() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=ROOT
    )
    return proc.stdout.strip() if proc.returncode == 0 else "unknown"


def collect() -> tuple[dict, list[str]]:
    """Build the evidence record; return it alongside any failures found."""
    failures: list[str] = []

    sources = []
    for path in python_files():
        rel = path.relative_to(ROOT).as_posix()
        is_contract = declares_contract(path)
        sources.append(
            {
                "path": rel,
                "role": "contract" if is_contract else "build input / tooling / test",
                "declares_contract_class": is_contract,
            }
        )

    contracts = [s["path"] for s in sources if s["declares_contract_class"]]
    if contracts != [CONTRACT_REL]:
        failures.append(f"expected exactly one contract source, found {contracts}")

    in_contracts_dir = sorted(p.name for p in (ROOT / "contracts").iterdir() if p.is_file())
    if in_contracts_dir != ["vouch.py"]:
        failures.append(f"contracts/ should hold only vouch.py, found {in_contracts_dir}")

    if build_contract.render() != CONTRACT.read_text(encoding="utf-8"):
        failures.append("contract is stale - run `python tools/build_contract.py`")

    compact = check_compact_artifact(failures)

    raw = CONTRACT.read_bytes()
    linter = find_linter()
    validation: list[dict] = []
    if linter is None:
        failures.append(
            "genvm-lint not found on a Python >= 3.12 - "
            "`python3.12 -m pip install genvm-linter`"
        )
    else:
        for command in ("check", "schema"):
            record = run_linter(linter, command)
            validation.append(record)
            if record["exit_code"] != 0:
                failures.append(f"{record['command']} exited {record['exit_code']}")

    record = {
        "contract": CONTRACT_REL,
        "contract_sha256": hashlib.sha256(raw).hexdigest(),
        "contract_bytes": len(raw),
        "compact_artifact": compact,
        # The digest above is the anchor: it identifies the exact bytes that were
        # validated, and it is what a deployed contract's source should be
        # compared against. `git_head_at_verification` is context only -- this
        # record is written before it is committed, so it names the *previous*
        # commit whenever verification and commit happen together.
        "git_head_at_verification": git_commit(),
        "linter": linter_version(),
        "sources": sources,
        "validation": validation,
    }
    return record, failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify without rewriting evidence/validation.json",
    )
    args = parser.parse_args()

    record, failures = collect()

    print(f"contract      {record['contract']}")
    print(f"sha256        {record['contract_sha256']}")
    print(f"linter        {record['linter']}")
    if record.get("compact_artifact"):
        c = record["compact_artifact"]
        print(
            f"compact       {c['path']}  {c['bytes']:,} bytes "
            f"({c['canonical_bytes'] - c['bytes']:,} smaller)  {c['sha256'][:16]}..."
        )
    for source in record["sources"]:
        mark = "CONTRACT" if source["declares_contract_class"] else "        "
        print(f"  {mark}  {source['path']}")

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1

    print("all checks passed")

    if not args.check:
        EVIDENCE.parent.mkdir(exist_ok=True)
        EVIDENCE.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"wrote {EVIDENCE.relative_to(ROOT).as_posix()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
