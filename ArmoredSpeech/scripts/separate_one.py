from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


DEFAULT_MODEL = "UVR-MDX-NET-Voc_FT.onnx"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Separa um clipe para um diretório isolado usando audio-separator; "
            "não altera o vídeo de entrada."
        )
    )
    parser.add_argument("input", type=Path, help="Caminho do clipe original/de teste")
    parser.add_argument(
        "--output-root",
        type=Path,
        required=True,
        help="Diretório de laboratório separado da pasta dos originais",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Modelo MDX-Net/UVR")
    parser.add_argument(
        "--timeout",
        type=int,
        default=7200,
        help="Tempo limite em segundos (padrão: 7200)",
    )
    args = parser.parse_args()

    source = args.input.expanduser().resolve()
    output_root = args.output_root.expanduser().resolve()

    if not source.is_file():
        parser.error(f"Arquivo de entrada não encontrado: {source}")
    if args.timeout <= 0:
        parser.error("--timeout precisa ser maior que zero")

    # Avoid placing outputs beside, inside, or above the source tree.
    if (
        source == output_root
        or source in output_root.parents
        or output_root in source.parents
    ):
        parser.error(
            "A saída precisa ficar em uma árvore de diretórios separada da entrada. "
            "Use, por exemplo, C:\\ArmoredAudioLab\\outputs."
        )

    executable = shutil.which("audio-separator")
    if executable is None:
        print(
            "ERRO: comando audio-separator não encontrado. "
            "Ative o ambiente virtual do laboratório e instale "
            "requirements-separation.txt.",
            file=sys.stderr,
        )
        return 2

    before_hash = sha256(source)
    run_dir = output_root / (
        datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + source.stem
    )
    run_dir.mkdir(parents=True, exist_ok=False)

    command = [
        executable,
        str(source),
        "--model_filename",
        args.model,
        "--output_dir",
        str(run_dir),
        "--output_format",
        "WAV",
    ]
    print(f"Entrada: {source}")
    print(f"SHA-256 antes: {before_hash}")
    print(f"Modelo: {args.model}")
    print(f"Saída isolada: {run_dir}")
    print("Iniciando separação. A primeira execução pode baixar o modelo.")

    try:
        completed = subprocess.run(command, check=False, timeout=args.timeout)
    except subprocess.TimeoutExpired:
        print(
            f"ERRO: tempo limite atingido ({args.timeout}s). "
            f"Arquivos parciais, se houver, permanecem em: {run_dir}",
            file=sys.stderr,
        )
        return 3

    after_hash = sha256(source)
    print(f"SHA-256 depois: {after_hash}")
    if before_hash != after_hash:
        print("ERRO CRÍTICO: o hash da entrada mudou durante o teste.", file=sys.stderr)
        return 4
    print("Verificação do original: OK (hash inalterado).")

    outputs = sorted(
        path for path in run_dir.rglob("*")
        if path.is_file() and path.resolve() != source
    )
    print(f"Código de saída: {completed.returncode}")
    print(f"Arquivos produzidos: {len(outputs)}")
    for output in outputs:
        print(f"  {output} ({output.stat().st_size} bytes)")

    if completed.returncode != 0:
        print("Separação falhou; não usar nenhuma saída em produção.", file=sys.stderr)
        return completed.returncode or 1
    if not outputs:
        print("Nenhuma saída foi encontrada; teste não aprovado.", file=sys.stderr)
        return 5

    print("Teste técnico concluído. Ainda falta avaliação auditiva; não integrar ao RVC.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
