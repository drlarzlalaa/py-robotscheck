"""robotscheck - parse robots.txt and test URLs against it, following RFC 9309.

Implements what Python's ``urllib.robotparser`` leaves out: ``*`` wildcards and
the ``$`` end anchor in paths, longest-match-wins with ``Allow`` beating
``Disallow`` on a tie, percent-encoding normalisation, and a linter for common
mistakes. Standard library only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote, unquote, urlsplit

__version__ = "0.3.0"

MAX_BYTES = 500 * 1024
KNOWN = {"user-agent", "allow", "disallow", "sitemap", "content-signal", "license"}
# The three signals defined by the IETF draft draft-romm-aipref-contentsignals (which does not define the robots.txt syntax;
# the syntax used here is the one Cloudflare deploys). "search", "ai-input" and "ai-train" take yes or no.
SIGNALS = ("search", "ai-input", "ai-train")
ABSOLUTE_URI = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://\S+$")
NON_STANDARD = {"crawl-delay": "Google ignores Crawl-delay (Bing and some others honour it)",
                "host": "Host is a Yandex-only directive", "clean-param": "Clean-param is a Yandex-only directive",
                "noindex": "Google stopped supporting Noindex in robots.txt in 2019; use a meta robots tag or X-Robots-Tag header",
                "request-rate": "Request-rate is not part of the standard and is widely ignored"}


@dataclass
class Rule:
    allow: bool
    pattern: str
    line: int

    def __str__(self) -> str:
        return "%s: %s" % ("Allow" if self.allow else "Disallow", self.pattern)


@dataclass
class Signal:
    name: str
    value: str             # "yes" or "no" (lower-cased); anything else is kept as written and reported by lint
    line: int


@dataclass
class Group:
    agents: List[str] = field(default_factory=list)
    rules: List[Rule] = field(default_factory=list)
    line: int = 0
    signals: List[Signal] = field(default_factory=list)
    licenses: List[Tuple[str, int]] = field(default_factory=list)


@dataclass
class Issue:
    severity: str          # "error", "warning" or "info"
    code: str
    message: str
    line: int = 0


@dataclass
class Robots:
    groups: List[Group] = field(default_factory=list)
    sitemaps: List[Tuple[str, int]] = field(default_factory=list)
    issues: List[Issue] = field(default_factory=list)
    size: int = 0
    licenses: List[Tuple[str, int]] = field(default_factory=list)      # License lines before any User-agent line (global)


def parse(text: str) -> Robots:
    """Parse robots.txt text. Never raises: problems become ``Robots.issues``."""
    robots = Robots(size=len(text.encode("utf-8", "replace")))
    if text.startswith("﻿"):
        text = text[1:]
    if robots.size > MAX_BYTES:
        robots.issues.append(Issue("warning", "too-large", "over 500 KiB: crawlers may ignore everything past that point"))
    current: Optional[Group] = None
    collecting_agents = False
    if not text.strip():
        robots.issues.append(Issue("warning", "empty", "the file is empty, so everything is allowed"))
    for n, raw in enumerate(re.split(r"\r\n|\r|\n", text), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if ":" not in line:
            robots.issues.append(Issue("warning", "no-colon", "not a 'field: value' line, ignored: %r" % line, n))
            continue
        field_name, value = (p.strip() for p in line.split(":", 1))
        key = field_name.lower()
        if key == "user-agent":
            if not collecting_agents or current is None:
                current = Group(line=n)
                robots.groups.append(current)
            current.agents.append(value.lower())
            collecting_agents = True
            if not value:
                robots.issues.append(Issue("warning", "empty-user-agent", "User-agent has no value", n))
            continue
        collecting_agents = False
        if key in ("allow", "disallow"):
            if current is None:
                robots.issues.append(Issue("warning", "rule-outside-group", "%s before any User-agent line is ignored" % field_name, n))
                continue
            if value and not value.startswith(("/", "*")):
                robots.issues.append(Issue("warning", "path-not-slash", "%s path %r should start with / or *" % (field_name, value), n))
            current.rules.append(Rule(key == "allow", value, n))
        elif key == "sitemap":
            robots.sitemaps.append((value, n))
            if not re.match(r"^https?://", value, re.I):
                robots.issues.append(Issue("warning", "sitemap-not-absolute", "Sitemap must be a full http(s) URL", n))
        elif key == "content-signal":
            if current is None:
                robots.issues.append(Issue("warning", "content-signal-outside-group", "Content-Signal before any User-agent line applies to no crawler group", n))
                continue
            _parse_signals(current, value, n, robots)
        elif key == "license":
            scope = current.licenses if current is not None else robots.licenses
            if not ABSOLUTE_URI.match(value):
                robots.issues.append(Issue("warning", "license-not-absolute", "License must be an absolute URI (RSL): %r" % value[:60], n))
            elif value.lower().startswith("http://"):
                robots.issues.append(Issue("info", "license-insecure", "the licence document is fetched over http, not https", n))
            if value and any(v == value for v, _ in scope):
                robots.issues.append(Issue("info", "license-duplicate", "the same licence is listed twice in this scope", n))
            scope.append((value, n))
        elif key in NON_STANDARD:
            robots.issues.append(Issue("info", key, NON_STANDARD[key], n))
        else:
            robots.issues.append(Issue("warning", "unknown-directive", "unknown directive %r is ignored" % field_name, n))
    if not robots.groups and text.strip():
        robots.issues.append(Issue("warning", "no-user-agent", "no User-agent line, so no rules apply to anyone"))
    _lint_groups(robots)
    return robots


def _parse_signals(group: Group, value: str, n: int, robots: Robots) -> None:
    """``Content-Signal: ai-train=no, search=yes`` -> Signal entries on the group; problems become issues."""
    if not value.strip():
        robots.issues.append(Issue("warning", "content-signal-syntax", "Content-Signal has no signals; expected name=yes or name=no separated by commas", n))
        return
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            robots.issues.append(Issue("warning", "content-signal-syntax", "%r is not name=value" % part[:30], n))
            continue
        name, val = (x.strip() for x in part.split("=", 1))
        name, val = name.lower(), val.lower()
        if name not in SIGNALS:
            robots.issues.append(Issue("info", "content-signal-unknown-signal", "%r is not one of the defined signals (%s); it is kept but not interpreted" % (name, ", ".join(SIGNALS)), n))
        if val not in ("yes", "no"):
            robots.issues.append(Issue("warning", "content-signal-value", "signal %s has value %r; expected yes or no" % (name, val or "(empty)"), n))
        for earlier in group.signals:
            if earlier.name == name:
                if earlier.value == val:
                    robots.issues.append(Issue("info", "content-signal-duplicate", "%s=%s is stated twice in this group (first on line %d)" % (name, val, earlier.line), n))
                else:
                    robots.issues.append(Issue("warning", "content-signal-conflict", "%s=%s here but %s=%s on line %d, in the same group" % (name, val, name, earlier.value, earlier.line), n))
                break
        group.signals.append(Signal(name, val, n))


def _lint_groups(robots: Robots) -> None:
    star_has_signals = any("*" in g.agents and g.signals for g in robots.groups)
    star_exists = any("*" in g.agents for g in robots.groups)
    for g in robots.groups:
        if g.signals and "*" not in g.agents and star_exists and not star_has_signals:
            robots.issues.append(Issue("info", "content-signal-not-for-all", "the Content-Signal on line %d belongs to the group for %s; crawlers that use 'User-agent: *' see no signal. "
                                                                              "A line placed after another group's rules joins that group, so move it under 'User-agent: *' if you meant it for everyone"
                                       % (g.signals[0].line, ", ".join(g.agents)), g.signals[0].line))
        if g.licenses and "*" not in g.agents and robots.groups:
            robots.issues.append(Issue("info", "license-in-group", "the License on line %d belongs to the group for %s only; a License before the first User-agent line applies to every crawler" % (g.licenses[0][1], ", ".join(g.agents)), g.licenses[0][1]))
    seen = {}
    for g in robots.groups:
        for a in g.agents:
            if a in seen:
                robots.issues.append(Issue("warning", "duplicate-group", "user-agent %r has more than one group (rules are merged)" % a, g.line))
            seen[a] = g.line
        if "*" in g.agents:
            for r in g.rules:
                if not r.allow and r.pattern in ("/", "/*"):
                    robots.issues.append(Issue("warning", "blocks-everything", "'User-agent: *' with '%s' blocks the whole site for every crawler" % r, r.line))


def _normalise(path: str) -> str:
    """Percent-encode to a canonical form so 'café', '%c3%a9' and '%C3%A9' compare equal."""
    decoded = unquote(path)
    return quote(decoded, safe="/*$?&=:@!,;+~()'-._")


def pattern_regex(pattern: str) -> "re.Pattern":
    """A compiled regex for a robots.txt path pattern: ``*`` is any run, a final ``$`` anchors the end."""
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    regex = "".join(".*" if ch == "*" else re.escape(ch) for ch in _normalise(body))
    return re.compile("^" + regex + ("$" if anchored else ""), re.S)


def product_token(agent: str) -> str:
    """The crawler's product token from a bare token or a full User-agent string, lower-cased."""
    m = re.search(r"([A-Za-z][A-Za-z0-9_-]*)(?:/[\d.]+)?", agent)
    return (m.group(1) if m else agent).lower()


def request_path(url: str) -> str:
    """The path and query of a URL (or of a bare '/path?x=1'), which is what rules are matched against."""
    parts = urlsplit(url)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return path


def rules_for(robots: Robots, agent: str) -> Tuple[List[Rule], str]:
    """The rules that apply to ``agent`` and which group name supplied them.

    A group naming the crawler's product token wins; otherwise the ``*`` group is used;
    several groups for one agent are merged.
    """
    token = product_token(agent)
    specific = [g for g in robots.groups if token in g.agents]
    if specific:
        return [r for g in specific for r in g.rules], token
    star = [g for g in robots.groups if "*" in g.agents]
    return [r for g in star for r in g.rules], "*"


def check(robots: Robots, agent: str, url: str) -> Tuple[bool, Optional[Rule]]:
    """Whether ``agent`` may fetch ``url``, and the rule that decided it (None if no rule matched)."""
    path = request_path(url)
    if re.fullmatch(r"/robots\.txt", path.split("?")[0]):
        return True, None
    rules, _ = rules_for(robots, agent)
    target = _normalise(path)
    best: Optional[Rule] = None
    best_len = -1
    for rule in rules:
        if not rule.pattern:                       # 'Disallow:' with nothing after it allows everything
            continue
        if pattern_regex(rule.pattern).match(target):
            length = len(_normalise(rule.pattern))
            if length > best_len or (length == best_len and rule.allow and best is not None and not best.allow):
                best, best_len = rule, length
    if best is None:
        return True, None
    return best.allow, best


def signals_for(robots: Robots, agent: str) -> Tuple[Dict[str, str], str]:
    """The Content-Signal values stated for ``agent`` and the group they came from, using the same group choice as rules_for.

    A named group replaces the ``*`` group entirely, so a crawler with its own group that states no signal has none. Signals
    that are not stated are absent (no preference), not "no". The IETF draft does not specify group selection for signals;
    this is the usual robots.txt behaviour.
    """
    token = product_token(agent)
    chosen = [g for g in robots.groups if token in g.agents] or [g for g in robots.groups if "*" in g.agents]
    name = token if any(token in g.agents for g in robots.groups) else "*"
    out: Dict[str, str] = {}
    for g in chosen:
        for sig in g.signals:
            out[sig.name] = sig.value
    return out, name


def licenses_for(robots: Robots, agent: str) -> Tuple[List[str], str]:
    """RSL licence URIs for ``agent``: those in its group, else the global ones. Returns (uris, 'group' or 'global' or 'none')."""
    token = product_token(agent)
    chosen = [g for g in robots.groups if token in g.agents] or [g for g in robots.groups if "*" in g.agents]
    grouped = [u for g in chosen for u, _ in g.licenses]
    if grouped:
        return grouped, "group"
    if robots.licenses:
        return [u for u, _ in robots.licenses], "global"
    return [], "none"
