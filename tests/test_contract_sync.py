"""Guards on the generated artifact and the shape of the repository.

These do not test the algorithm -- `test_vouch_core.py` does that. They test
the thing the algorithm tests cannot see: that the fix which landed in `lib/`
actually reached the file that ships, and that a validator pointed at this
repository finds exactly one contract.
"""

import ast
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "contracts" / "vouch.py"
LIB = ROOT / "lib"
MODULES = ("vouch_core.py", "vouch_evidence.py", "vouch_prompts.py")

SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", ".pytest_cache"}


def python_files():
    for path in ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def declares_contract(path: pathlib.Path) -> bool:
    """The linter's own rule: a `Contract` base, or a `@gl.public` method."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                if isinstance(base, ast.Attribute) and base.attr == "Contract":
                    return True
                if isinstance(base, ast.Name) and base.id == "Contract":
                    return True
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                text = ast.unparse(dec)
                if text.startswith("gl.public"):
                    return True
    return False


class TestArtifactIsCurrent:
    def test_contract_is_not_stale(self):
        """A fix in `lib/` that never reached the deployed file is the failure
        this repository's whole build split exists to make impossible."""
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "build_contract.py"), "--check"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr or result.stdout

    def test_contract_imports_no_local_module(self):
        """`import vouch_core` resolves on a dev box and finds nothing
        on-chain -- invisible to every check that runs with the repo on
        sys.path, which is what makes it worth a test."""
        tree = ast.parse(CONTRACT.read_text(encoding="utf-8"))
        local = {m[:-3] for m in MODULES}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in local
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in local

    def test_inlining_introduced_no_duplicate_definitions(self):
        tree = ast.parse(CONTRACT.read_text(encoding="utf-8"))
        seen = {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                assert node.name not in seen, f"duplicate top-level {node.name}"
                seen[node.name] = True

    def test_contract_has_no_unresolved_names(self):
        """Everything the inlined code references must be defined or imported
        in the contract. The build strips each module's own prologue, so a name
        that only ever resolved through a library import resolves to nothing
        on-chain."""
        source = CONTRACT.read_text(encoding="utf-8")
        tree = ast.parse(source)

        defined = {"gl", "u256", "Address", "DynArray", "TreeMap", "allow_storage"}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defined.add(node.name)
            elif isinstance(node, ast.Import):
                for a in node.names:
                    defined.add(a.asname or a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                for a in node.names:
                    if a.name != "*":
                        defined.add(a.asname or a.name)
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        defined.add(t.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                defined.add(node.target.id)

        # Names used at module scope in a call position that are neither builtins
        # nor defined here would be NameErrors on-chain.
        import builtins

        known = defined | set(dir(builtins))
        missing = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id not in known:
                    missing.add(node.func.id)
        assert not missing, f"unresolved names: {sorted(missing)}"


class TestRepositoryShape:
    def test_contracts_dir_holds_only_the_contract(self):
        found = sorted(p.name for p in (ROOT / "contracts").glob("*.py"))
        assert found == ["vouch.py"]

    def test_exactly_one_contract_outside_dist(self):
        """`dist/` holds a generated copy of the same contract, so it is excluded
        here rather than counted as a second one. `contracts/` still holds exactly
        one file and it is the one to read."""
        declaring = [
            p for p in python_files()
            if declares_contract(p) and "dist" not in p.relative_to(ROOT).parts
        ]
        assert [p.name for p in declaring] == ["vouch.py"]

    def test_library_modules_import_no_sdk(self):
        """They must stay pure Python: they are inlined, and an SDK import in a
        build input would be a second copy of the SDK inside the contract."""
        for name in MODULES:
            source = (LIB / name).read_text(encoding="utf-8")
            assert "from genlayer" not in source
            assert "import genlayer" not in source

    def test_library_modules_are_marked(self):
        for name in MODULES:
            first = (LIB / name).read_text(encoding="utf-8").splitlines()[0]
            assert "NOT AN INTELLIGENT CONTRACT" in first

    def test_contract_pins_the_runner(self):
        """All GenLayer networks reject `test`, `latest`, and unversioned
        aliases. The pin is the first line or the deploy fails."""
        first = CONTRACT.read_text(encoding="utf-8").splitlines()[0]
        assert first.startswith("# {") and '"Depends"' in first
        assert "py-genlayer:" in first
        for alias in ("py-genlayer:test", "py-genlayer:latest"):
            assert alias not in first

    def test_contract_is_pure_ascii(self):
        """Non-ASCII on the deploy path is silently fragile: a re-encoding tool
        or a diff view can substitute a character with no visible trace."""
        raw = CONTRACT.read_bytes()
        assert all(b < 128 for b in raw), "contract must be pure ASCII"

    def test_no_python_at_repository_root(self):
        assert not list(ROOT.glob("*.py"))
