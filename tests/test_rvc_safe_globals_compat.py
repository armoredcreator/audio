"""Compatibility check for the optional RVC runtime.

The repository's base CI intentionally installs lightweight project dependencies
only. RVC's PyTorch/fairseq stack is validated in its dedicated runtime
environment, so absence of either dependency should skip this module rather
than prevent the rest of the repository test suite from being collected.
"""

from argparse import Namespace

import pytest

torch = pytest.importorskip(
    "torch",
    reason="optional RVC compatibility test requires the RVC PyTorch runtime",
)
dictionary_module = pytest.importorskip(
    "fairseq.data.dictionary",
    reason="optional RVC compatibility test requires fairseq",
)
Dictionary = dictionary_module.Dictionary

from ArmoredSpeech.rvc_engine import _trusted_safe_globals


def test_safe_globals_scope_restores_the_previous_registry():
    serialization = torch.serialization

    assert callable(getattr(serialization, "add_safe_globals", None))
    assert callable(getattr(serialization, "get_safe_globals", None))
    assert callable(getattr(serialization, "clear_safe_globals", None))

    previous = list(serialization.get_safe_globals())
    approved = [Dictionary, Namespace]

    try:
        with _trusted_safe_globals(torch, approved):
            active = list(serialization.get_safe_globals())
            assert all(entry in active for entry in approved)
    finally:
        # Assert the helper restored its own changes before test cleanup.
        assert list(serialization.get_safe_globals()) == previous
