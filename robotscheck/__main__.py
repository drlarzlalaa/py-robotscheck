"""Command line: python -m robotscheck test|lint|sitemaps FILE."""
from __future__ import annotations

import argparse
import sys

import json

from . import __version__, check, parse, rules_for, product_token
from . import aibots
from . import SIGNALS, licenses_for, signals_for


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
    g = sub.add_parser("signals", help="Content-Signal and License (RSL) lines, and what each crawler ends up with")
    g.add_argument("file", help="robots.txt file, or - for stdin")
    g.add_argument("--agent", action="append", help="crawler to show (repeatable; default: * and every known AI crawler)")
    g.add_argument("--expect", help="signals every shown crawler must state, e.g. ai-train=no,search=yes; exit 1 if any does not (a signal that is not stated does not match)")
    g.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        robots = parse(_read(args.file))
    except OSError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    if args.command == "bots":
        return _bots(args, robots)
    if args.command == "signals":
        return _signals(args, robots)

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


def _parse_expect(spec):
    out = {}
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        name, _, val = item.partition("=")
        name, val = name.strip().lower(), val.strip().lower()
        if name not in SIGNALS or val not in ("yes", "no"):
            raise ValueError("expected signals like ai-train=no (names: %s; values: yes or no), got %r" % (", ".join(SIGNALS), item))
        out[name] = val
    if not out:
        raise ValueError("empty --expect")
    return out


def _signals(args, robots) -> int:
    try:
        expect = _parse_expect(args.expect) if args.expect else None
    except ValueError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    agents = args.agent or (["*"] + [b.token for b in aibots.BOTS])
    rows = []
    for a in agents:
        sig, group = signals_for(robots, a)
        lic, scope = licenses_for(robots, a)
        rows.append({"agent": a, "group": group, "signals": sig, "licenses": lic, "license_scope": scope})
    problems = []
    if expect:
        for r in rows:
            for name, want in expect.items():
                got = r["signals"].get(name)
                if got != want:
                    why = ("not stated%s" % (" (its group '%s' has no Content-Signal for it)" % r["group"])) if got is None else "is %s" % got
                    problems.append("%s: %s=%s expected but %s" % (r["agent"], name, want, why))
    if args.json:
        print(json.dumps({"groups": [{"agents": g.agents, "line": g.line, "signals": {s.name: s.value for s in g.signals}, "licenses": [u for u, _ in g.licenses]} for g in robots.groups],
                          "global_licenses": [u for u, _ in robots.licenses], "effective": rows, "expect": expect, "mismatches": problems}, indent=2))
        return 1 if problems else 0
    print("Content-Signal and License lines (signal names from the IETF draft; the line syntax is the one Cloudflare deploys)")
    for u, n in robots.licenses:
        print("  License (global, line %d): %s" % (n, u))
    for g in robots.groups:
        sigs = ", ".join("%s=%s" % (s.name, s.value) for s in g.signals) or "no signals"
        lic = "; License: " + ", ".join(u for u, _ in g.licenses) if g.licenses else ""
        print("  group %s (line %d): %s%s" % (", ".join("'%s'" % a for a in g.agents), g.line, sigs, lic))
    print("\nEffective for each crawler (a named group replaces the '*' group; a signal not stated means no preference, shown as -):")
    for r in rows:
        vals = "  ".join("%s=%s" % (name, r["signals"].get(name, "-")) for name in SIGNALS)
        lic = "License %s" % r["license_scope"] if r["licenses"] else "no License"
        print("  %-20s group %-18s %s   %s" % (r["agent"], "'%s'" % r["group"], vals, lic))
    if expect:
        print("\nExpected: " + ", ".join("%s=%s" % kv for kv in expect.items()))
        for m in problems:
            print("  MISMATCH  " + m)
        print("signals %s" % ("do NOT match" if problems else "match"))
    return 1 if problems else 0


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
