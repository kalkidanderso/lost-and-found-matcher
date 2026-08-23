"""Command line entry points.

    python -m lostfound serve            # web UI + JSON API on :8000
    python -m lostfound demo             # seed an in-memory DB, print the board
    python -m lostfound match L-1A2B3C    # explain one report's matches

The CLI exists because a terminal walkthrough is the fastest way for a reviewer
to see the decisions, and because it proves the domain logic does not depend on
the web layer.
"""

from __future__ import annotations

import argparse
import sys

from . import seed
from .server import build_server
from .service import LostFoundService, NotFound
from .store import Store

BANDS = {"strong": "STRONG  ", "possible": "POSSIBLE", "weak": "WEAK    "}


def _service(db):
    return LostFoundService(Store(db))


def _print_match(match, indent=""):
    lost, found = match["lost"], match["found"]
    print(f"{indent}{BANDS[match['band']]} {match['score']:>3}/100  "
          f"{match['lost_id']} <-> {match['found_id']}")
    print(f"{indent}   lost : {lost['description'][:88]}")
    print(f"{indent}   found: {found['description'][:88]}")
    for signal in match["signals"]:
        mark = "-" if not signal["available"] else f"{signal['score']:.2f}"
        print(f"{indent}     {signal['label']:<20} {mark:>5}  {signal['reason']}")
    for note in match["notes"]:
        print(f"{indent}     ! {note}")
    print()


def cmd_serve(args):
    service = _service(args.db)
    if args.demo and not service.store.open_reports():
        seed.load(service)
        print(f"Seeded {len(seed.SEED)} demo reports.")
    httpd = build_server(service, args.host, args.port)
    host, port = httpd.server_address[0], httpd.server_address[1]
    print(f"\n  Lost & Found Matcher\n  http://{host}:{port}\n  database: {args.db}\n"
          f"  press ctrl-c to stop\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopping")
    finally:
        httpd.server_close()
        service.store.close()
    return 0


def cmd_demo(args):
    service = _service(args.db)
    if not service.store.open_reports():
        seed.load(service)
    stats = service.stats()
    print(f"\n{stats['reports']['lost_open']} open lost reports, "
          f"{stats['reports']['found_open']} open found reports")
    board = service.board()
    print(f"{board['total']} pairs worth showing a human: "
          + ", ".join(f"{n} {band}" for band, n in board["by_band"].items() if n)
          + "\n" + "-" * 78 + "\n")
    for match in board["matches"]:
        _print_match(match)
    return 0


def cmd_match(args):
    service = _service(args.db)
    try:
        result = service.matches_for(args.report_id, include_rejections=args.debug)
    except NotFound as exc:
        print(exc)
        return 1
    report = result["report"]
    print(f"\n{report['kind'].upper()} {report['id']}: {report['description']}\n")
    if not result["matches"]:
        print("  no matches above the display threshold\n")
    for match in result["matches"]:
        _print_match(match, indent="  ")
    for ruled_out in result.get("ruled_out", []):
        print(f"  ruled out {ruled_out['found_id'] if report['kind'] == 'lost' else ruled_out['lost_id']}"
              f": {ruled_out['reason']}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="lostfound", description="University lost & found matcher")
    parser.add_argument("--db", default="lostfound.db", help="SQLite path, or :memory: (default: lostfound.db)")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="run the web UI and JSON API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--demo", action="store_true", help="seed demo reports if the database is empty")
    serve.set_defaults(func=cmd_serve)

    demo = sub.add_parser("demo", help="seed data and print the match board")
    demo.set_defaults(func=cmd_demo, db=":memory:")

    match = sub.add_parser("match", help="explain the matches for one report")
    match.add_argument("report_id")
    match.add_argument("--debug", action="store_true", help="also list pairs that were ruled out")
    match.set_defaults(func=cmd_match)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
