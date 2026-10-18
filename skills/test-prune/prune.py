#!/usr/bin/env python3
"""test-prune: drive the fixer/reviewer prune loop and the strengthen loop over a target repo.

Stub: defines the CLI surface only. Every command exits 3, "not implemented".
"""
import argparse
import sys


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--state", help="run state directory (default: <git dir>/test-prune)")
    common.add_argument("--date", help="branch date YYYY-MM-DD (default: today, UTC)")

    parser = argparse.ArgumentParser(prog="prune.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    collect = sub.add_parser("collect", parents=[common])
    collect.add_argument("--unattended", action="store_true")
    collect.add_argument("--protected-only", action="store_true")

    sub.add_parser("review-input", parents=[common]).add_argument("--proposals", required=True)
    sub.add_parser("batch", parents=[common]).add_argument("--reviews", required=True)
    sub.add_parser("apply", parents=[common])
    sub.add_parser("prune", parents=[common])
    sub.add_parser("strengthen-start", parents=[common])
    sub.add_parser("strengthen-verify", parents=[common])
    sub.add_parser("strengthen-finish", parents=[common]).add_argument("--reviews", required=True)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    print(f"{args.command}: not implemented", file=sys.stderr)
    return 3


if __name__ == "__main__":
    sys.exit(main())
