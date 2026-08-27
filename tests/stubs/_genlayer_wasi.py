"""Off-chain stand-in for the GenVM host module.

`contracts/vouch.py` cannot be imported off-chain without this: `gl.Contract`
pulls in `_genlayer_wasi`, which only exists inside the VM. Importing the real
artifact is worth the stub, because it means the pipeline tests exercise the
file that actually deploys rather than the `lib/` sources it was built from.

Every entry point raises. Nothing under test here may call into the host: the
functions the tests drive are pure, and a test that reached the VM would be
testing the stub instead of the contract. A `RuntimeError` naming the call is
how that mistake announces itself.
"""


def _unavailable(name):
    def call(*args, **kwargs):
        raise RuntimeError(
            f"_genlayer_wasi.{name} was called off-chain -- this test is "
            f"exercising VM behaviour the stub cannot provide"
        )

    return call


_NAMES = (
    "gl_call",
    "get_message_data",
    "get_entrypoint",
    "contract_return",
    "rollback",
    "run_nondet",
    "storage_read",
    "storage_write",
    "get_balance",
    "get_self_balance",
)

for _name in _NAMES:
    globals()[_name] = _unavailable(_name)


def __getattr__(name):
    return _unavailable(name)
