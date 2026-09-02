"""Command-line entry points: update / solve / report / backtest."""

from __future__ import annotations

import argparse
import sys


def cmd_update(args: argparse.Namespace) -> None:
    from .api import clean_old_snapshots, download_history, update_snapshot
    from .config import load_settings

    from .history import settle

    settings = load_settings()
    update_snapshot(settings.team_id)
    download_history()
    clean_old_snapshots()
    settle(settings.team_id)
    print("Data update complete.")


def cmd_due(args: argparse.Namespace) -> None:
    from .due import is_due

    due, why = is_due()
    print(f"{'true' if due else 'false'} ({why})", file=sys.stderr)
    print("true" if due else "false")


def cmd_solve(args: argparse.Namespace) -> None:
    from .pipeline import run_solve

    run_solve(skip_chips=args.no_chips, skip_robustness=args.no_robustness)


def cmd_report(args: argparse.Namespace) -> None:
    from .pipeline import run_report

    run_report()


def cmd_backtest(args: argparse.Namespace) -> None:
    from .backtest import run_backtest

    run_backtest()


def main() -> None:
    parser = argparse.ArgumentParser(prog="mudchute")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("update", help="refresh FPL API data")
    p_solve = sub.add_parser("solve", help="run xP engine + solver")
    p_solve.add_argument("--no-chips", action="store_true",
                         help="skip chip value analysis (faster)")
    p_solve.add_argument("--no-robustness", action="store_true",
                         help="skip perturbed re-solves (faster)")
    sub.add_parser("report", help="write report.html")
    sub.add_parser("backtest", help="validate xP model on 2025-26 data")
    sub.add_parser("due", help="print true/false: is a scheduled run due?")

    args = parser.parse_args()
    {"update": cmd_update, "solve": cmd_solve,
     "report": cmd_report, "backtest": cmd_backtest,
     "due": cmd_due}[args.command](args)


if __name__ == "__main__":
    main()
