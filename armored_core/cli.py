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
        description="Coordinator com lote limitado ou operação contínua CATCH-UP → LIVE sob orçamento diário persistente.",
    )
    parser.add_argument("--root", type=Path, default=project_root(), help="raiz isolada do projeto e do storage")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--validate-config", action="store_true", help="validar configuração sem abrir banco ou Telegram")
    mode.add_argument("--preflight", action="store_true", help="verificar composição real e SQLite clonado sem I/O externo")
    mode.add_argument("--run", action="store_true", help="executar um lote limitado e encerrar sem entrar em LIVE")
    mode.add_argument("--continuous", action="store_true", help="executar lotes limitados sucessivos, concluir CATCH-UP e monitorar LIVE")
    parser.add_argument("--evidence", type=Path, help="caminho do relatório JSON exigido por --preflight")
    parser.add_argument("--publish", action="store_true", help="abrir publicação real no Hub; sem esta opção o envio fica em dry-run")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--max-live-cycles", type=int, help="limite opcional de ciclos LIVE após o CATCH-UP")
    parser.add_argument("--catch-up-run-id", help="ID estável para retomar o mesmo lote após interrupção")
    parser.add_argument("--max-catch-up-items", type=int, help="limite global durável de itens do lote; 1 a 1000")
    parser.add_argument("--continuous-operation-id", help="identidade imutável do orçamento diário contínuo")
    parser.add_argument("--max-items-per-day", type=int, help="teto persistente de itens únicos por dia UTC; 1 a 10000")
    parser.add_argument("--max-items-per-batch", type=int, help="teto de cada lote automático; 1 a 1000")
    args = parser.parse_args(argv)

    root = args.root.expanduser().resolve()
    if not root.is_dir():
        parser.error(f"raiz do projeto não existe: {root}")
    if args.poll_seconds <= 0:
        parser.error("--poll-seconds deve ser maior que zero")
    if args.max_live_cycles is not None and args.max_live_cycles < 1:
        parser.error("--max-live-cycles deve ser positivo")
    if args.run:
        if not args.catch_up_run_id or args.max_catch_up_items is None:
            parser.error("--run exige --catch-up-run-id e --max-catch-up-items; a execução é sempre limitada")
        if any(value is not None for value in (
            args.continuous_operation_id, args.max_items_per_day, args.max_items_per_batch,
        )):
            parser.error("--run não pode ser combinado com orçamento --continuous")
        if not 1 <= args.max_catch_up_items <= 1000:
            parser.error("--max-catch-up-items deve estar entre 1 e 1000")
        if args.max_live_cycles is not None:
            parser.error("execuções com orçamento durável encerram após o lote e não aceitam --max-live-cycles")
    elif args.continuous:
        if not args.continuous_operation_id or args.max_items_per_day is None or args.max_items_per_batch is None:
            parser.error("--continuous exige --continuous-operation-id, --max-items-per-day e --max-items-per-batch")
        if not 1 <= args.max_items_per_day <= 10000:
            parser.error("--max-items-per-day deve estar entre 1 e 10000")
        if not 1 <= args.max_items_per_batch <= min(1000, args.max_items_per_day):
            parser.error("--max-items-per-batch deve estar entre 1 e min(1000, max-items-per-day)")
        if args.max_live_cycles is not None:
            parser.error("--continuous permanece ativo; --max-live-cycles não se aplica")
        if args.catch_up_run_id is not None or args.max_catch_up_items is not None:
            parser.error("--continuous usa orçamento diário; não combine com opções --run")
    elif any(value is not None for value in (
        args.catch_up_run_id, args.max_catch_up_items,
        args.continuous_operation_id, args.max_items_per_day, args.max_items_per_batch,
    )):
        parser.error("opções de orçamento exigem --run ou --continuous")
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
            catch_up_run_id=args.catch_up_run_id,
            max_catch_up_items=args.max_catch_up_items,
            continuous_operation_id=args.continuous_operation_id,
            max_items_per_day=args.max_items_per_day,
            max_items_per_batch=args.max_items_per_batch,
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
