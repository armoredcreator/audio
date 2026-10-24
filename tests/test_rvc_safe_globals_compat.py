from __future__ import annotations

import contextlib
from types import SimpleNamespace

import pytest

from ArmoredSpeech.rvc_engine import _trusted_safe_globals


def _legacy_torch(initial):
    registry = list(initial)

    def add_safe_globals(values):
        registry.extend(values)

    def get_safe_globals():
        return list(registry)

    def clear_safe_globals():
        registry.clear()

    serialization = SimpleNamespace(
        add_safe_globals=add_safe_globals,
        get_safe_globals=get_safe_globals,
        clear_safe_globals=clear_safe_globals,
    )
    return SimpleNamespace(serialization=serialization), registry


def test_legacy_safe_global_scope_restores_registry_even_when_inference_fails():
    previous = object()
    approved = object()
    fake_torch, registry = _legacy_torch([previous])

    with pytest.raises(RuntimeError, match="simulated inference failure"):
        with _trusted_safe_globals(fake_torch, [approved]):
            assert registry == [previous, approved]
            raise RuntimeError("simulated inference failure")

    assert registry == [previous]


def test_legacy_safe_global_scope_restores_registry_after_success():
    previous = object()
    approved = object()
    fake_torch, registry = _legacy_torch([previous])

    with _trusted_safe_globals(fake_torch, [approved]):
        assert registry == [previous, approved]

    assert registry == [previous]


def test_scope_fails_closed_when_no_public_safe_global_api_exists():
    fake_torch = SimpleNamespace(serialization=SimpleNamespace())

    with pytest.raises(RuntimeError, match="supported safe-global API"):
        _trusted_safe_globals(fake_torch, [object])


def test_native_scope_delegates_to_pytorch_context_manager():
    observed = []

    @contextlib.contextmanager
    def native_scope(values):
        observed.extend(values)
        yield

    fake_torch = SimpleNamespace(
        serialization=SimpleNamespace(safe_globals=native_scope)
    )
    approved = [object, str]

    with _trusted_safe_globals(fake_torch, approved):
        assert observed == approved

    assert observed == approved


def test_installed_pytorch_safe_globals_scope_when_available():
    torch = pytest.importorskip("torch")
    serialization = torch.serialization
    native_scope = getattr(serialization, "safe_globals", None)
    get_globals = getattr(serialization, "get_safe_globals", None)
    if not callable(native_scope) or not callable(get_globals):
        pytest.skip("PyTorch não oferece a API pública scoped safe_globals")

    class LocalCheckpointMetadata:
        pass

    previous = list(get_globals())
    with _trusted_safe_globals(torch, [LocalCheckpointMetadata]):
        assert LocalCheckpointMetadata in get_globals()
    assert list(get_globals()) == previous


def test_rvc_metadata_types_are_allowed_when_optional_runtime_is_installed():
    from argparse import Namespace

    torch = pytest.importorskip(
        "torch", reason="optional RVC runtime is not installed"
    )
    dictionary_module = pytest.importorskip(
        "fairseq.data.dictionary", reason="optional fairseq runtime is not installed"
    )
    Dictionary = dictionary_module.Dictionary
    serialization = torch.serialization
    get_globals = getattr(serialization, "get_safe_globals", None)
    if not callable(get_globals):
        pytest.skip("PyTorch does not expose its public safe-global registry")

    previous = list(get_globals())
    approved = [Dictionary, Namespace]
    with _trusted_safe_globals(torch, approved):
        active = list(get_globals())
        assert all(entry in active for entry in approved)
    assert list(get_globals()) == previous
