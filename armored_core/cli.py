from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from .routing import RouteConfig, load_routes
from .storage import project_root


def _load_project_configuration(root: Path) -> None:
    credentials = root / "credentials" / "project.env"
    config = root / ".env"
    if credentials.is_file():
        load_dotenv(credentials, override=True)
    if config.is_file():
        load_dotenv(config, override=False)


def _validate_routes(routes: tuple[RouteConfig, ...]) -> None:
    if len(routes) != 3:
        raise RuntimeError(f"O Coordinator final exige exatamente 3 fontes configuradas; encontradas {len(routes)}.")
    source_ids = [route.source.source_id for route in routes]
    destinations = [(route.hub.chat_id, route.hub.topic_id) for route in routes]
    if len(set(source_ids)) != 3:
        raise RuntimeError("Cada uma das três fontes deve ter um source_id único.")
    if len(set(destinations)) != 3:
        raise RuntimeError("Cada fonte deve apontar para um destino Hub próprio (chat e tópico únicos).")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="armoredcreator",
        description="Inicialização do Coordinator com CATCH-UP durável seguido de LIVE.",
    )
    parser.add_argument("--root", type=Path, default=project_root(), help="raiz isolada do projeto e do storage")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--validate-config", action="store_true", help="validar configuração sem abrir banco ou Telegram")
    mode.add_argument("--preflight", action="store_true", help="verificar composição real e SQLite clonado sem I/O externo")
    mode.add_argument("--run", action="store_true", help="iniciar Coordinator e concluir CATCH-UP antes de LIVE")
    parser.add_argument("--evidence", type=Path, help="caminho do relatório JSON exigido por --preflight")
    parser.add_argument("--publish", action="store_true", help="abrir publicação real no Hub; sem esta opção o envio fica em dry-run")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--max-live-cycles", type=int, help="limite opcional de ciclos LIVE após o CATCH-UP")
    args = parser.parse_args(argv)

    root = args.root.expanduser().resolve()
    if not root.is_dir():
        parser.error(f"raiz do projeto não existe: {root}")
    if args.poll_seconds <= 0:
        parser.error("--poll-seconds deve ser maior que zero")
    if args.max_live_cycles is not None and args.max_live_cycles < 1:
        parser.error("--max-live-cycles deve ser positivo")
    if args.preflight and args.evidence is None:
        parser.error("--evidence é obrigatório com --preflight")
    if args.evidence is not None and not args.preflight:
        parser.error("--evidence só pode ser usado com --preflight")
    _load_project_configuration(root)
    try:
        routes = load_routes()
        _validate_routes(routes)
        if os.getenv("ARMORED_REAL_TELEGRAM", "0").strip() != "1":
            raise RuntimeError("ARMORED_REAL_TELEGRAM=1 é obrigatório para o Coordinator Telegram")
        if not os.getenv("TELEGRAM_API_ID") or not os.getenv("TELEGRAM_API_HASH"):
            raise RuntimeError("TELEGRAM_API_ID e TELEGRAM_API_HASH precisam estar configurados")
        if args.preflight:
            from .preflight import run_preflight

            report = run_preflight(root, args.evidence)
            print(f"PREFLIGHT={report['status']}")
            print(f"EVIDENCE={args.evidence.resolve()}")
            return 0 if report["status"] == "PASS" else 2
        if args.validate_config:
            print("CONFIGURATION=VALID")
            print("SOURCE_COUNT=3")
            print("HUB_DESTINATIONS=3 unique")
            print("TELEGRAM_CREDENTIALS=present (values hidden)")
            return 0
        from .coordinator import Coordinator

        logging.basicConfig(
            level=(os.getenv("ARMORED_LOG_LEVEL") or "INFO").upper(),
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
        coordinator = Coordinator.build(
            root,
            acquire_runtime_lock=True,
            allow_real_publication=bool(args.publish),
        )
        try:
            logging.getLogger(__name__).info(
                "Starting ArmoredCreator with %d routes; Hub dry-run=%s",
                len(routes),
                "no" if args.publish else "yes",
            )
            coordinator.run_forever(
                poll_seconds=args.poll_seconds,
                max_cycles=args.max_live_cycles,
            )
        finally:
            coordinator.close()
        return 0
    except Exception as exc:
        print(f"ARMOREDCREATOR_STARTUP=FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
