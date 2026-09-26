"""Which AI crawlers may fetch a site, according to its robots.txt.

The crawler list is data, not law: names and behaviour change, and vendors document them in their own
places. ``AS_OF`` says when this list was last checked. Extend or correct it with a bots file (see ``load_bots_file``).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from . import Robots, check, product_token, rules_for

AS_OF = "2026-09-26"
CATEGORIES = ("training", "search", "user", "other")
CATEGORY_TITLES = {
    "training": "TRAINING (collects content to train models)",
    "search": "SEARCH (builds an index that AI search products cite)",
    "user": "USER-TRIGGERED (fetches a page because a person asked; may ignore robots.txt)",
    "other": "OTHER",
}


@dataclass(frozen=True)
class Bot:
    token: str
    vendor: str
    category: str
    note: str = ""


BOTS: Tuple[Bot, ...] = (
    Bot("GPTBot", "OpenAI", "training"),
    Bot("OAI-SearchBot", "OpenAI", "search"),
    Bot("ChatGPT-User", "OpenAI", "user", "OpenAI's page says robots.txt rules may not apply because a user initiates the fetch"),
    Bot("OAI-AdsBot", "OpenAI", "other", "checks pages submitted as ads on ChatGPT; OpenAI says it only visits submitted pages"),
    Bot("ClaudeBot", "Anthropic", "training"),
    Bot("Claude-SearchBot", "Anthropic", "search"),
    Bot("Claude-User", "Anthropic", "user", "Anthropic states this bot honours robots.txt"),
    Bot("anthropic-ai", "Anthropic", "training", "seen in many robots.txt files but not named in Anthropic's current help-centre article, which lists ClaudeBot, Claude-SearchBot and Claude-User"),
    Bot("PerplexityBot", "Perplexity", "search"),
    Bot("Perplexity-User", "Perplexity", "user", "reported not to follow robots.txt for user-initiated fetches"),
    Bot("Google-Extended", "Google", "training", "a control token read by Google's own crawlers, not a separate crawler; it governs Gemini training and grounding, not Google Search"),
    Bot("Applebot-Extended", "Apple", "training", "a control token read by Applebot; it opts content out of Apple's AI training, not out of Apple search"),
    Bot("meta-externalagent", "Meta", "training"),
    Bot("Bytespider", "ByteDance", "training", "widely reported to ignore robots.txt"),
    Bot("CCBot", "Common Crawl", "training", "builds the open Common Crawl dataset that many models use"),
    Bot("Amazonbot", "Amazon", "other", "Amazon's general-purpose crawler"),
)


def load_bots_file(text: str) -> List[Bot]:
    """Parse lines of ``Token,category[,vendor[,note]]``. Blank lines and ``#`` comments are skipped."""
    bots = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",", 3)]
        if len(parts) < 2 or not parts[0]:
            raise ValueError("bots file line %d: expected 'Token,category[,vendor[,note]]', got %r" % (n, raw.strip()))
        if parts[1] not in CATEGORIES:
            raise ValueError("bots file line %d: category must be one of %s, got %r" % (n, ", ".join(CATEGORIES), parts[1]))
        parts += [""] * (4 - len(parts))
        bots.append(Bot(parts[0], parts[2], parts[1], parts[3]))
    return bots


def merge_bots(base: Sequence[Bot], extra: Sequence[Bot]) -> List[Bot]:
    """``extra`` entries replace built-in ones with the same token (case-insensitive) and are otherwise added."""
    merged: Dict[str, Bot] = {b.token.lower(): b for b in base}
    for b in extra:
        merged[b.token.lower()] = b
    return list(merged.values())


@dataclass
class Row:
    bot: Bot
    group: str
    results: List[Tuple[str, bool, Optional[str]]]        # (path, allowed, deciding rule text)

    @property
    def blocked_everywhere(self) -> bool:
        return all(not ok for _, ok, _ in self.results)

    @property
    def allowed_everywhere(self) -> bool:
        return all(ok for _, ok, _ in self.results)


def analyse(robots: Robots, paths: Sequence[str] = ("/",), bots: Sequence[Bot] = BOTS) -> List[Row]:
    rows = []
    for bot in bots:
        _, group = rules_for(robots, bot.token)
        results = []
        for path in paths:
            ok, rule = check(robots, bot.token, path)
            results.append((path, ok, ("%s (line %d)" % (rule, rule.line)) if rule else None))
        rows.append(Row(bot, group, results))
    return rows


def parse_policy(spec: str) -> Dict[str, str]:
    """``training=block,search=allow`` -> {'training': 'block', 'search': 'allow'}; ``all`` covers every category."""
    policy: Dict[str, str] = {}
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise ValueError("policy item %r: expected category=block or category=allow" % item)
        cat, want = (s.strip().lower() for s in item.split("=", 1))
        if cat != "all" and cat not in CATEGORIES:
            raise ValueError("policy item %r: category must be all or one of %s" % (item, ", ".join(CATEGORIES)))
        if want not in ("block", "allow"):
            raise ValueError("policy item %r: value must be block or allow" % item)
        if cat == "all":
            for c in CATEGORIES:
                policy.setdefault(c, want)
        else:
            policy[cat] = want
    if not policy:
        raise ValueError("empty policy")
    return policy


def violations(rows: Sequence[Row], policy: Dict[str, str]) -> List[str]:
    """Messages for every bot whose treatment differs from the policy for its category."""
    out = []
    for row in rows:
        want = policy.get(row.bot.category)
        if want == "block" and not row.blocked_everywhere:
            paths = ", ".join(p for p, ok, _ in row.results if ok)
            out.append("%s (%s) is not blocked on: %s" % (row.bot.token, row.bot.category, paths))
        elif want == "allow" and not row.allowed_everywhere:
            paths = ", ".join(p for p, ok, _ in row.results if not ok)
            out.append("%s (%s) is blocked on: %s" % (row.bot.token, row.bot.category, paths))
    return out


def summary(rows: Sequence[Row]) -> Dict[str, Tuple[int, int]]:
    """Per category: (bots blocked on every path, bots in the category)."""
    result: Dict[str, Tuple[int, int]] = {}
    for cat in CATEGORIES:
        members = [r for r in rows if r.bot.category == cat]
        if members:
            result[cat] = (sum(1 for r in members if r.blocked_everywhere), len(members))
    return result
