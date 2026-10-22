from argparse import Namespace

import torch
from fairseq.data.dictionary import Dictionary

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