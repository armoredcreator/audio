"""ArmoredSpeech-owned RVC Melody engine. Routing and invocation belong to Speech."""

from __future__ import annotations

from pathlib import Path
import contextlib
import threading
import io
import os
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
SPEECH_ROOT = Path(__file__).resolve().parent
SHARED_ENV_DIR = REPO_ROOT / ".venv"
# Single canonical owner: no runtime override or fallback can silently keep
# loading model/index assets from ArmoredStudio. Local assets must be migrated
# to ArmoredSpeech/runtime/rvc before the real RVC phase is enabled.
RVC_ROOT = (SPEECH_ROOT / "runtime" / "rvc").resolve()
MODELS_DIR = RVC_ROOT / "models"
BASE_DIR = SPEECH_ROOT

_SAFE_GLOBALS_LOCK = threading.RLock()


def _trusted_safe_globals(torch_module, globals_to_allow):
    """Temporarily allowlist trusted RVC checkpoint metadata."""
    serialization = torch_module.serialization
    native_scope = getattr(serialization, "safe_globals", None)

    if callable(native_scope):
        return native_scope(globals_to_allow)

    add_globals = getattr(serialization, "add_safe_globals", None)
    get_globals = getattr(serialization, "get_safe_globals", None)
    clear_globals = getattr(serialization, "clear_safe_globals", None)

    if not all(callable(function) for function in (
        add_globals, get_globals, clear_globals
    )):
        raise RuntimeError(
            "PyTorch does not expose a supported safe-global API; "
            "refusing an unsafe checkpoint-loading fallback."
        )

    @contextlib.contextmanager
    def compatible_scope():
        with _SAFE_GLOBALS_LOCK:
            previous = list(get_globals())
            try:
                add_globals(globals_to_allow)
                yield
            finally:
                clear_globals()
                if previous:
                    add_globals(previous)

    return compatible_scope()


DEFAULT_VOICE = "melody"
DEFAULT_OUTPUT = RVC_ROOT / "output" / "audio_rvc.wav"


def resolver_rvc_python() -> Path:
    """Resolve the only operational Python environment: the repository-root .venv."""
    candidates = (
        SHARED_ENV_DIR / "Scripts" / "python.exe",
        SHARED_ENV_DIR / "bin" / "python",
        SHARED_ENV_DIR / "bin" / "python3",
    )
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate

    expected = "\n".join(str(path) for path in candidates)
    raise FileNotFoundError(
        "Python do ambiente unificado da raiz não encontrado. Caminhos esperados:\n"
        + expected
    )

def validar_arquivo(arquivo, descricao):
    arquivo = Path(arquivo)
    if not arquivo.exists():
        raise FileNotFoundError(f"{descricao} não encontrado:\n{arquivo}")
    if arquivo.stat().st_size <= 0:
        raise RuntimeError(f"{descricao} está vazio:\n{arquivo}")


def localizar_modelo(voz):
    voz = str(voz).strip()
    if not voz:
        raise ValueError("Nome da voz não pode ser vazio.")
    pasta = MODELS_DIR / voz
    if not pasta.exists():
        raise FileNotFoundError(f"Voz não encontrada:\n{pasta}")

    modelos = sorted(pasta.glob("*.pth"))
    indices = sorted(pasta.glob("*.index"))

    if not modelos:
        raise FileNotFoundError(f"Nenhum modelo .pth encontrado em:\n{pasta}")

    # O índice é opcional: RVCInference aceita index_path="".
    index = indices[0] if indices else None
    return modelos[0], index


def _backend_output_tail(stdout: str, stderr: str, limit: int = 12) -> str:
    lines = [line.strip() for line in (stdout + "\n" + stderr).splitlines() if line.strip()]
    return " | ".join(lines[-limit:])


def converter_interno(entrada, saida, modelo, index, item_id=None):
    import scipy.io.wavfile as wavfile

    prefix = f"[SPEECH][ITEM {item_id}] " if item_id else "[SPEECH] "
    print(f"{prefix}RVC voz={modelo.parent.name}")

    captured_stdout = io.StringIO()
    captured_stderr = io.StringIO()
    try:
        with contextlib.redirect_stdout(captured_stdout), contextlib.redirect_stderr(captured_stderr):
            from rvc_python.infer import RVCInference
            rvc = RVCInference(
                model_path=str(modelo),
                index_path=str(index) if index else "",
                version="v2",
                device="cpu:0",
            )
        # rvc-python's infer_file() writes its result with scipy internally.
        # When vc_single() fails, the library returns a traceback string
        # instead of an audio array; infer_file() then raises the misleading
        # "str has no attribute dtype". Call the underlying conversion
        # contract directly so failures remain deterministic and actionable.
        model_info = rvc.models[rvc.current_model]
        file_index = model_info.get("index", "")

        # PyTorch >= 2.6 defaults torch.load(weights_only=True). Legacy
        # Fairseq HuBERT checkpoints contain this metadata class, which is
        # required for RVC inference. Allowlist only this known Fairseq class
        # for the duration of inference instead of disabling weights_only,
        # which would permit arbitrary pickle globals from a checkpoint.
        import argparse
        import torch
        from fairseq.data.dictionary import Dictionary

        # Legacy Fairseq checkpoints commonly include argparse.Namespace
        # alongside Dictionary metadata. Both are explicitly allowlisted;
        # we do not switch torch.load to unrestricted pickle loading.
        with _trusted_safe_globals(torch, [Dictionary, argparse.Namespace]):
            wav_opt = rvc.vc.vc_single(
                sid=0,
                input_audio_path=str(entrada),
                f0_up_key=rvc.f0up_key,
                f0_method=rvc.f0method,
                file_index=file_index,
                index_rate=rvc.index_rate,
                filter_radius=rvc.filter_radius,
                resample_sr=rvc.resample_sr,
                rms_mix_rate=rvc.rms_mix_rate,
                protect=rvc.protect,
                f0_file="",
                file_index2="",
            )
        if (
            isinstance(wav_opt, tuple)
            and len(wav_opt) == 2
            and isinstance(wav_opt[0], str)
            and isinstance(wav_opt[1], tuple)
        ):
            details = wav_opt[0].strip()
            Path(saida).unlink(missing_ok=True)
            raise RuntimeError(
                "RVC inference falhou no backend"
                + (f": {details}" if details else "")
            )

        if not hasattr(wav_opt, "dtype"):
            Path(saida).unlink(missing_ok=True)
            raise RuntimeError("RVC retornou áudio inválido")

        wavfile.write(str(saida), int(rvc.vc.tgt_sr), wav_opt)
    except Exception as exc:
        Path(saida).unlink(missing_ok=True)
        backend = _backend_output_tail(captured_stdout.getvalue(), captured_stderr.getvalue())
        if backend:
            raise RuntimeError(f"{exc} | backend: {backend}") from exc
        raise

    try:
        validar_arquivo(saida, "Áudio RVC")
    except Exception:
        Path(saida).unlink(missing_ok=True)
        raise
    return Path(saida)


def executar_no_rvc(entrada, saida, voz, item_id=None):
    modelo, index = localizar_modelo(voz)
    rvc_python = resolver_rvc_python()

    prefix = f"[SPEECH][ITEM {item_id}]" if item_id else "[STUDIO]"
    print(f"\n{prefix} RVC voz={voz}")

    current_python = Path(sys.executable).resolve()
    if current_python == rvc_python.resolve():
        return converter_interno(entrada, saida, modelo, index, item_id)

    comando = [
        str(rvc_python),
        str(Path(__file__).resolve()),
        str(entrada),
        str(saida),
        voz,
        str(item_id or ""),
    ]
    resultado = subprocess.run(
        comando,
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if resultado.returncode != 0:
        backend = _backend_output_tail(resultado.stdout or "", resultado.stderr or "")
        reason = "Falha na conversão RVC."
        if backend:
            reason += f" | backend: {backend}"
        raise RuntimeError(reason)
    validar_arquivo(saida, "Áudio RVC")
    return Path(saida)


def converter_voz(entrada, saida=None, voz=DEFAULT_VOICE, item_id=None):
    entrada = Path(entrada)
    saida = Path(saida) if saida is not None else DEFAULT_OUTPUT
    validar_arquivo(entrada, "Áudio de entrada")
    saida.parent.mkdir(parents=True, exist_ok=True)
    if saida.exists():
        saida.unlink()
    return executar_no_rvc(entrada, saida, voz, item_id)


def main():
    if len(sys.argv) < 3:
        print("Uso: python rvc.py entrada.wav saida.wav [voz]")
        sys.exit(1)

    entrada = Path(sys.argv[1])
    saida = Path(sys.argv[2])
    voz = sys.argv[3] if len(sys.argv) >= 4 else DEFAULT_VOICE
    item_id = sys.argv[4] if len(sys.argv) >= 5 and sys.argv[4] else None

    if Path(sys.executable).resolve() == resolver_rvc_python().resolve():
        modelo, index = localizar_modelo(voz)
        converter_interno(entrada, saida, modelo, index, item_id)
        return

    converter_voz(entrada, saida, voz, item_id)


if __name__ == "__main__":
    main()
