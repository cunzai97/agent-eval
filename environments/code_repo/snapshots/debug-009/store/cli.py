"""Command line front end. Keeps its own copy of the supported kinds."""
import argparse
import json
import sys

from .db import JsonStore
from .portable import import_bundle, read_bundle, write_bundle
from .registry import LABELS
from .render import render_card
from .state import board_state, daemon_state

USAGE = """boardstore - JSON file backed board storage

kinds: issue, agent, squad

commands:
  init    PATH                                create an empty store
  create  PATH KIND JSON                      insert one record
  list    PATH KIND                           list one collection as cards
  state   PATH                                print the daemon heartbeat
  board   PATH                                print the board payload
  export  PATH OUT                            write a portable bundle
  import  PATH IN                             merge a portable bundle
"""

KIND_CHOICES = ["issue", "agent", "squad"]


def usage_text():
    return USAGE


def build_parser():
    parser = argparse.ArgumentParser(prog="boardstore", add_help=False)
    sub = parser.add_subparsers(dest="command")

    init = sub.add_parser("init")
    init.add_argument("path")

    create = sub.add_parser("create")
    create.add_argument("path")
    create.add_argument("kind", choices=KIND_CHOICES)
    create.add_argument("fields")

    listing = sub.add_parser("list")
    listing.add_argument("path")
    listing.add_argument("kind", choices=KIND_CHOICES)

    for name in ("state", "board"):
        command = sub.add_parser(name)
        command.add_argument("path")

    export = sub.add_parser("export")
    export.add_argument("path")
    export.add_argument("out")

    merge = sub.add_parser("import")
    merge.add_argument("path")
    merge.add_argument("src")
    return parser


def main(argv):
    parser = build_parser()
    args = parser.parse_args(argv)
    store = JsonStore(args.path)

    if args.command == "create":
        item = store.create(args.kind, json.loads(args.fields))
        return render_card(item)
    if args.command == "list":
        lines = [render_card(item) for item in store.list(args.kind)]
        header = LABELS.get(args.kind, args.kind)
        return "\n".join([header] + lines)
    if args.command == "state":
        return json.dumps(daemon_state(store), sort_keys=True)
    if args.command == "board":
        return json.dumps(board_state(store), sort_keys=True)
    if args.command == "export":
        bundle = write_bundle(store, args.out)
        return f"exported {sum(len(v) for v in bundle['sections'].values())} records"
    if args.command == "import":
        imported = import_bundle(store, read_bundle(args.src))
        return json.dumps(imported, sort_keys=True)
    return usage_text()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
