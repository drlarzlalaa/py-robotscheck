"""Command line: python -m robotscheck test|lint|sitemaps FILE."""
from __future__ import annotations

import argparse
import sys

import json

from . import __version__, check, parse, rules_for, product_token
from . import aibots


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
    b = sub.add_parser("bots", help="how does this robots.txt treat known AI crawlers?")
    b.add_argument("file", help="robots.txt file, or - for stdin")
    b.add_argument("--path", action="append", help="path to test (repeatable; default /)")
    b.add_argument("--policy", help="desired treatment, e.g. training=block,search=allow,user=allow (categories: training, search, user, other, all); exit 1 if the file does not match")
    b.add_argument("--bots-file", help="extra or corrected crawlers: lines of Token,category[,vendor[,note]]")
    b.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        robots = parse(_read(args.file))
    except OSError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    if args.command == "bots":
        return _bots(args, robots)

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


def _bots(args, robots) -> int:
    try:
        extra = aibots.load_bots_file(_read(args.bots_file)) if args.bots_file else []
        policy = aibots.parse_policy(args.policy) if args.policy else None
    except (OSError, ValueError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    paths = args.path or ["/"]
    rows = aibots.analyse(robots, paths, aibots.merge_bots(aibots.BOTS, extra))
    problems = aibots.violations(rows, policy) if policy else []
    if args.json:
        print(json.dumps({"as_of": aibots.AS_OF, "paths": paths, "bots": [
            {"token": r.bot.token, "vendor": r.bot.vendor, "category": r.bot.category, "group": r.group, "note": r.bot.note,
             "results": [{"path": p, "allowed": ok, "rule": why} for p, ok, why in r.results]} for r in rows],
            "policy": policy, "violations": problems}, indent=2))
        return 1 if problems else 0
    print("AI crawlers against %s (crawler list as of %s; it changes, see the README)" % (", ".join(paths), aibots.AS_OF))
    for cat in aibots.CATEGORIES:
        members = [r for r in rows if r.bot.category == cat]
        if not members:
            continue
        print("\n" + aibots.CATEGORY_TITLES[cat])
        for r in members:
            if len(r.results) == 1:
                path, ok, why = r.results[0]
                verdict = "ALLOWED" if ok else "BLOCKED"
                detail = why or "no rule matches"
            else:
                verdict = "BLOCKED" if r.blocked_everywhere else ("ALLOWED" if r.allowed_everywhere else "PARTIAL")
                detail = "; ".join("%s %s" % (p, "ok" if ok else "blocked") for p, ok, _ in r.results)
            print("  %-8s %-19s %-13s group %-20s %s" % (verdict, r.bot.token, r.bot.vendor, "'%s'" % r.group, detail))
    print("\nBlocked on every path: " + ", ".join("%s %d of %d" % (c, b, n) for c, (b, n) in aibots.summary(rows).items()))
    if policy:
        print("Policy: " + ", ".join("%s=%s" % kv for kv in policy.items()))
        for msg in problems:
            print("  MISMATCH  " + msg)
        print("policy %s" % ("NOT met" if problems else "met"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
