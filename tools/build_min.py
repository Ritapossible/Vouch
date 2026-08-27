"""Generate the compact deploy artifact for size-constrained networks.

`contracts/vouch.py` is the canonical contract: the file to read, review and
deploy wherever it fits. On Testnet Bradbury it does not fit -- at ~61 KB the
deploy transaction is rejected with `BlockPubdataLimitReached` before gas
estimation can even complete, because the whole source is published as
transaction data.

Roughly 44% of that source is prose. This tool emits the same contract with
comments and docstrings removed:

    python tools/build_min.py            # regenerate dist/vouch.min.py
    python tools/build_min.py --check    # exit non-zero if it is stale

The output is checked in, so a deployment made from it is still verifiable byte
for byte against a file in this repository -- the property that matters is
"the source on the explorer is a source you can review", and both artifacts keep
it. `dist/` rather than `contracts/` because `contracts/` holds exactly one file
and that invariant is worth more than the convenience of putting them together.

Semantics are preserved by construction: the AST is parsed, docstring
expressions are dropped, and the tree is unparsed. No identifier, literal or
statement is rewritten. `tests/test_contract_sync.py` asserts both files expose
the same public surface.
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "contracts" / "vouch.py"
OUT = ROOT / "dist" / "vouch.min.py"


def strip_prose(source: str) -> str:
    """Return the contract with comments and docstrings removed.

    The runner header on line 1 is a comment and is *not* optional -- every
    GenLayer network rejects a contract without a pinned `Depends`. It is taken
    off before parsing and put back verbatim.
    """
    lines = source.splitlines(keepends=True)
    if not lines or not lines[0].startswith("# {"):
        raise SystemExit("contract does not begin with a pinned runner header")
    header, body = lines[0], "".join(lines[1:])

    tree = ast.parse(body)
    for node in ast.walk(tree):
        block = getattr(node, "body", None)
        if not isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ) or not block:
            continue
        first = block[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            # A function whose only statement was its docstring still needs a body.
            node.body = block[1:] or [ast.Pass()]

    unparsed = ast.unparse(ast.fix_missing_locations(tree))
    return header + _to_ascii(unparsed) + "\n"


def _to_ascii(source: str) -> str:
    """Re-escape non-ASCII characters as `\\uXXXX`, and prove nothing moved.

    `ast.unparse` renders a string literal with its characters *literal*, so the
    `\\uXXXX` escapes the confusable map is written with come back out as the
    glyphs themselves -- several of which are invisible or render as their Latin
    twin. That breaks the contract's pure-ASCII property in the one artifact
    least likely to be read closely, and those bytes are exactly the ones an
    editor or a re-encoding tool can silently drop in transit.

    The escape is only sound if it changes no semantics, so this does not take
    that on faith: both forms are parsed and their dumped ASTs compared. A
    mismatch is a build failure rather than a deployed surprise.
    """
    escaped = "".join(ch if ord(ch) < 128 else "\\u%04x" % ord(ch) for ch in source)
    if escaped != source:
        if ast.dump(ast.parse(escaped)) != ast.dump(ast.parse(source)):
            raise SystemExit("ASCII escaping changed the parse tree; refusing to write")
    return escaped


def public_surface(source: str) -> list[str]:
    """Decorated public method names, in order. Used to prove the two agree."""
    names = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if ast.unparse(dec).startswith("gl.public"):
                    names.append(node.name)
    return sorted(names)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if stale")
    args = parser.parse_args()

    source = CONTRACT.read_text(encoding="utf-8")
    want = strip_prose(source)

    if public_surface(source) != public_surface(want):
        raise SystemExit("public surface differs between the two artifacts")

    have = OUT.read_text(encoding="utf-8") if OUT.exists() else None
    if have == want:
        print(f"{OUT.name} is up to date ({len(want.encode()):,} bytes)")
        return 0
    if args.check:
        print(f"{OUT.name} is STALE - run `python tools/build_min.py`", file=sys.stderr)
        return 1

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(want, encoding="utf-8", newline="\n")
    saved = len(source.encode()) - len(want.encode())
    print(
        f"wrote {OUT.name}: {len(want.encode()):,} bytes "
        f"({saved:,} smaller, {saved * 100 // len(source.encode())}% off)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
