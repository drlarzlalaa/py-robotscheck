"""Command line: python -m robotscheck test|lint|sitemaps FILE."""
from __future__ import annotations

import argparse
import sys

from . import __version__, check, parse, rules_for, product_token


def _read(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="robotscheck", description="Parse robots.txt and test URLs against it (RFC 9309).")
    parser.add_argument("--version", action="version", version="robotscheck " + __version__)
    sub = parser.add_subparsers(dest="command", required=True)
    t = sub.add_parser("test", help="is each URL allowed for a crawler?")
    t.add_argument("file", help="robots.txt file, or - for stdin")
    t.add_argument("urls", nargs="+", help="URLs or paths to test")
    t.add_argument("--agent", default="*", help="crawler name or User-agent string (default: any crawler)")
    l = sub.add_parser("lint", help="report likely mistakes")
    l.add_argument("file")
    l.add_argument("--strict", action="store_true", help="exit 1 for warnings as well as errors")
    s = sub.add_parser("sitemaps", help="list the Sitemap: lines")
    s.add_argument("file")
    args = parser.parse_args(argv)
    try:
        robots = parse(_read(args.file))
    except OSError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    if args.command == "sitemaps":
        for url, _ in robots.sitemaps:
            print(url)
        return 0 if robots.sitemaps else 1

    if args.command == "lint":
        for i in robots.issues:
            where = "line %d: " % i.line if i.line else ""
            print("%-8s %-20s %s%s" % (i.severity, i.code, where, i.message))
        n_w = sum(1 for i in robots.issues if i.severity in ("warning", "error"))
        print("%d group(s), %d sitemap(s), %d issue(s) to look at" % (len(robots.groups), len(robots.sitemaps), n_w))
        return 1 if any(i.severity == "error" for i in robots.issues) or (args.strict and n_w) else 0

    _, group = rules_for(robots, args.agent)
    print("agent %s uses the %s group" % (product_token(args.agent) if args.agent != "*" else "*", "'%s'" % group))
    blocked = 0
    for url in args.urls:
        ok, rule = check(robots, args.agent, url)
        blocked += not ok
        why = "%s (line %d)" % (rule, rule.line) if rule else "no rule matches"
        print("%-8s %-40s %s" % ("ALLOWED" if ok else "BLOCKED", url, why))
    return 1 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
