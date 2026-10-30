"""Command line for the customer data platform.

  python -m cdp validate                     check all config files, list problems
  python -m cdp run [--tenant ID ...] [--full-refresh]
  python -m cdp schedule --every-minutes 360  run forever on a fixed interval
  python -m cdp status                       last runs and per-source results
  python -m cdp sample-data                  (re)generate the demo source files
"""
from __future__ import annotations

import argparse
import sys

from cdp.config import ConfigError, brand, load_source_types, load_tenants
from cdp.logs import setup_logging


def cmd_validate(_args) -> int:
    try:
        tenants = load_tenants()
    except ConfigError as exc:
        print(exc)
        return 1
    print(f"Configuration OK: {len(tenants)} tenant(s), {len(load_source_types())} source type(s).")
    for t in tenants:
        print(f"  {t['tenant_id']:<14} {t['name']:<24} {len(t['sources'])} source(s)")
    return 0


def cmd_run(args) -> int:
    from cdp.pipeline import run
    try:
        status = run(args.tenant or None, full_refresh=args.full_refresh)
    except ConfigError as exc:
        print(exc)
        return 1
    except Exception as exc:  # already logged with traceback by the pipeline
        print(f"Pipeline failed: {exc}", file=sys.stderr)
        return 1
    return 0 if status == "success" else 2  # 2 = partial: some sources failed


def cmd_schedule(args) -> int:
    from cdp.pipeline import schedule
    schedule(args.every_minutes)
    return 0


def cmd_status(_args) -> int:
    from cdp.pipeline import connect
    con = connect(read_only=True)
    print(con.sql("SELECT run_id, started_at, status, tenants, message FROM meta.runs ORDER BY started_at DESC LIMIT 5"))
    print(con.sql("""SELECT tenant_id, source_name, connector, load_mode, status, rows_loaded, error
                     FROM meta.source_runs WHERE run_id = (SELECT max(run_id) FROM meta.source_runs)
                     ORDER BY tenant_id, source_name"""))
    return 0


def cmd_secret_key(_args) -> int:
    from cdp.secrets import generate_key
    print(generate_key())
    print("Set this as CDP_SECRET_KEY on the server and keep a safe copy: stored credentials need it.",
          file=sys.stderr)
    return 0


def cmd_sample(_args) -> int:
    from cdp.generate_sources import main
    main()
    return 0


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    parser = argparse.ArgumentParser(prog="cdp", description=f"{brand()['product_name']} customer data platform")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate", help="validate configuration").set_defaults(func=cmd_validate)
    run_p = sub.add_parser("run", help="run the pipeline")
    run_p.add_argument("--tenant", action="append", help="only ingest this tenant (repeatable)")
    run_p.add_argument("--full-refresh", action="store_true", help="ignore watermarks and reload everything")
    run_p.set_defaults(func=cmd_run)
    sched = sub.add_parser("schedule", help="run the pipeline every N minutes (for a scheduler container)")
    sched.add_argument("--every-minutes", type=int, default=360)
    sched.set_defaults(func=cmd_schedule)
    sub.add_parser("status", help="show recent runs").set_defaults(func=cmd_status)
    sub.add_parser("sample-data", help="generate demo source files").set_defaults(func=cmd_sample)
    sub.add_parser("secret-key", help="generate a CDP_SECRET_KEY for the credential store").set_defaults(
        func=cmd_secret_key)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
