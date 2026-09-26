import contextlib
import io
import json
import os
import random
import unittest

import robotscheck as rc
from robotscheck.__main__ import main

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "data", "signals.txt")


def codes(text):
    return sorted((i.code, i.line) for i in rc.parse(text).issues)


class SignalLines(unittest.TestCase):
    def test_valid_signals_are_parsed_and_add_no_issues(self):
        r = rc.parse("User-agent: *\nContent-Signal: search=yes, ai-input=no, ai-train=no\nAllow: /\n")
        self.assertEqual(r.issues, [])
        self.assertEqual([(s.name, s.value, s.line) for s in r.groups[0].signals], [("search", "yes", 2), ("ai-input", "no", 2), ("ai-train", "no", 2)])

    def test_case_spacing_and_trailing_comma(self):
        r = rc.parse("User-agent: *\ncontent-signal:  AI-Train = NO ,Search=Yes,\n")
        self.assertEqual([(s.name, s.value) for s in r.groups[0].signals], [("ai-train", "no"), ("search", "yes")])
        self.assertEqual(r.issues, [])

    def test_several_lines_in_one_group_merge(self):
        r = rc.parse("User-agent: *\nContent-Signal: search=yes\nContent-Signal: ai-train=no\n")
        self.assertEqual(rc.signals_for(r, "anybot"), ({"search": "yes", "ai-train": "no"}, "*"))

    def test_a_signal_never_changes_what_is_crawlable(self):
        with_signal = rc.parse("User-agent: *\nContent-Signal: ai-train=no\nDisallow: /a/\nAllow: /a/b\n")
        without = rc.parse("User-agent: *\nDisallow: /a/\nAllow: /a/b\n")
        for url in ("/", "/a/", "/a/x", "/a/b", "/z"):
            self.assertEqual(rc.check(with_signal, "Bot", url)[0], rc.check(without, "Bot", url)[0], url)
        self.assertEqual(len(with_signal.groups[0].rules), 2)

    def test_problems(self):
        cases = [("Content-Signal: \n", "content-signal-syntax"), ("Content-Signal: ai-train\n", "content-signal-syntax"), ("Content-Signal: ai-train=maybe\n", "content-signal-value"),
                 ("Content-Signal: ai-train=\n", "content-signal-value"), ("Content-Signal: ai-train=yes, ai-train=no\n", "content-signal-conflict"),
                 ("Content-Signal: search=yes, search=yes\n", "content-signal-duplicate"), ("Content-Signal: train-ai=no\n", "content-signal-unknown-signal")]
        for line, code in cases:
            got = [i.code for i in rc.parse("User-agent: *\n" + line).issues]
            self.assertEqual(got, [code], line)

    def test_severity_levels(self):
        sev = {i.code: i.severity for i in rc.parse("User-agent: *\nContent-Signal: ai-train=maybe, x=yes, search=yes, search=yes\n").issues}
        self.assertEqual(sev, {"content-signal-value": "warning", "content-signal-unknown-signal": "info", "content-signal-duplicate": "info"})

    def test_outside_any_group_is_reported_and_ignored(self):
        r = rc.parse("Content-Signal: ai-train=no\nUser-agent: *\nAllow: /\n")
        self.assertEqual([(i.code, i.line) for i in r.issues], [("content-signal-outside-group", 1)])
        self.assertEqual(rc.signals_for(r, "bot")[0], {})

    def test_the_directives_are_no_longer_unknown_but_typos_still_are(self):
        self.assertEqual(codes("User-agent: *\nContent-Signal: search=yes\nLicense: https://a.example/l.xml\n"), [])
        self.assertEqual([c for c, _ in codes("User-agent: *\nContent-Signals: search=yes\nLicence: https://a.example/l.xml\n")], ["unknown-directive", "unknown-directive"])


class NotForAll(unittest.TestCase):
    """A widely deployed real file puts its Content-Signal line after the last named group, so under normal grouping it joins that group only."""

    def test_signal_after_a_named_group_only_covers_that_group(self):
        t = "User-agent: *\nAllow: /\n\nUser-agent: Cohere-ai\nAllow: /\n\nContent-Signal: ai-train=yes, search=yes, ai-input=yes\n"
        r = rc.parse(t)
        self.assertEqual([(i.code, i.line, i.severity) for i in r.issues], [("content-signal-not-for-all", 7, "info")])
        self.assertIn("cohere-ai", r.issues[0].message)
        self.assertEqual(rc.signals_for(r, "*")[0], {})
        self.assertEqual(rc.signals_for(r, "Cohere-ai")[0], {"ai-train": "yes", "search": "yes", "ai-input": "yes"})

    def test_only_raised_when_it_applies(self):
        star_has = "User-agent: *\nContent-Signal: search=yes\nUser-agent: GPTBot\nContent-Signal: ai-train=no\n"
        self.assertEqual(codes(star_has), [])
        no_star = "User-agent: GPTBot\nContent-Signal: ai-train=no\n"
        self.assertEqual(codes(no_star), [])
        both_named = "User-agent: *\nAllow: /\nUser-agent: GPTBot\nContent-Signal: ai-train=no\n"
        self.assertEqual([c for c, _ in codes(both_named)], ["content-signal-not-for-all"])


class Licenses(unittest.TestCase):
    def test_global_and_group_scope_and_precedence(self):
        t = "License: https://a.example/global.xml\n\nUser-agent: *\nAllow: /\n\nUser-agent: GPTBot\nLicense: https://a.example/gpt.xml\nAllow: /\n"
        r = rc.parse(t)
        self.assertEqual([i.code for i in r.issues], ["license-in-group"])          # GPTBot's own group holds a License, so it is scoped to that crawler
        self.assertEqual(rc.licenses_for(r, "somebot"), (["https://a.example/global.xml"], "global"))
        self.assertEqual(rc.licenses_for(r, "GPTBot"), (["https://a.example/gpt.xml"], "group"))
        self.assertEqual(rc.licenses_for(rc.parse("User-agent: *\nAllow: /\n"), "bot"), ([], "none"))

    def test_problems(self):
        cases = [("License: license.xml\n", "license-not-absolute"), ("License: /license.xml\n", "license-not-absolute"), ("License:\n", "license-not-absolute"),
                 ("License: http://a.example/l.xml\n", "license-insecure"), ("License: https://a.example/l.xml\nLicense: https://a.example/l.xml\n", "license-duplicate")]
        for line, code in cases:
            self.assertEqual([i.code for i in rc.parse(line + "User-agent: *\nAllow: /\n").issues], [code], line)

    def test_license_line_after_groups_joins_the_last_group(self):
        r = rc.parse("User-agent: *\nAllow: /\nUser-agent: ClaudeBot\nUser-agent: GPTBot\nDisallow: /a/\nSitemap: https://a.example/s.xml\nLicense: https://a.example/l.xml\n")
        self.assertEqual([(i.code, i.line) for i in r.issues], [("license-in-group", 7)])
        self.assertIn("claudebot, gptbot", r.issues[0].message)
        self.assertEqual(r.licenses, [])
        self.assertEqual(rc.licenses_for(r, "GPTBot")[1], "group")
        self.assertEqual(rc.licenses_for(r, "other")[1], "none")


class Effective(unittest.TestCase):
    def test_a_named_group_replaces_the_star_group(self):
        r = rc.parse(open(FIXTURE).read())
        self.assertEqual(rc.signals_for(r, "SomeBot"), ({"search": "yes", "ai-input": "no", "ai-train": "no"}, "*"))
        self.assertEqual(rc.signals_for(r, "GPTBot"), ({}, "gptbot"))
        self.assertEqual(rc.signals_for(r, "ClaudeBot/1.0"), ({"ai-train": "no"}, "claudebot"))
        self.assertEqual(rc.licenses_for(r, "GPTBot"), (["https://example.com/license.xml"], "global"))

    def test_groups_for_the_same_agent_merge(self):
        r = rc.parse("User-agent: GPTBot\nContent-Signal: ai-train=no\n\nUser-agent: GPTBot\nContent-Signal: search=yes\n")
        self.assertEqual(rc.signals_for(r, "gptbot")[0], {"ai-train": "no", "search": "yes"})


def build(rnd):
    """A random robots.txt and the structure it was written from."""
    lines, groups, global_licenses = [], [], []
    if rnd.random() < 0.4:
        global_licenses = ["https://lic.example/%d.xml" % rnd.randint(1, 99)]
        lines += ["License: " + global_licenses[0], ""]
    agents_pool = ["*", "GPTBot", "ClaudeBot", "CCBot", "Googlebot", "Bytespider", "PerplexityBot"]
    for agents in rnd.sample(agents_pool, rnd.randint(1, 4)):
        agents = [agents] if rnd.random() < 0.8 else [agents, "extra%d" % rnd.randint(1, 9)]
        sigs = {}
        for name in rnd.sample(rc.SIGNALS, rnd.randint(0, 3)):
            sigs[name] = rnd.choice(["yes", "no"])
        lics = ["https://g.example/%d.xml" % rnd.randint(1, 99)] if rnd.random() < 0.2 else []
        lines += ["User-agent: " + a for a in agents]
        body = []
        if sigs:
            items = ["%s=%s" % (k, v) for k, v in sigs.items()]
            if rnd.random() < 0.3 and len(items) > 1:
                body += ["Content-Signal: " + items[0], "Content-Signal: " + ", ".join(items[1:])]
            else:
                body.append("Content-Signal: " + ", ".join(items))
        body += ["License: " + u for u in lics]
        body += [rnd.choice(["Allow: /", "Disallow: /private/", "Disallow:"]) for _ in range(rnd.randint(1, 2))]      # a group with no line of its own would merge with the next one (RFC 9309)
        rnd.shuffle(body)
        lines += body + ["", "# comment"]
        groups.append((agents, sigs, lics))
    return "\n".join(lines) + "\n", groups, global_licenses


class Generated(unittest.TestCase):
    def test_parsed_structure_equals_the_structure_that_was_written(self):
        rnd = random.Random(20260926)
        for i in range(400):
            text, groups, global_lics = build(rnd)
            r = rc.parse(text)
            self.assertEqual(len(r.groups), len(groups), "run %d\n%s" % (i, text))
            for g, (agents, sigs, lics) in zip(r.groups, groups):
                self.assertEqual(g.agents, [a.lower() for a in agents])
                self.assertEqual({s.name: s.value for s in g.signals}, sigs, "run %d\n%s" % (i, text))
                self.assertEqual([u for u, _ in g.licenses], lics)
            self.assertEqual([u for u, _ in r.licenses], global_lics)
            self.assertEqual([x.code for x in r.issues if x.code.startswith(("content-signal", "license")) and x.code not in ("content-signal-not-for-all", "license-in-group")], [], "run %d\n%s" % (i, text))

    def test_effective_values_follow_group_selection(self):
        rnd = random.Random(11)
        for i in range(300):
            text, groups, global_lics = build(rnd)
            r = rc.parse(text)
            named = {a.lower(): sigs for agents, sigs, _ in groups for a in agents}
            star = next((sigs for agents, sigs, _ in groups if "*" in agents), {})
            for token in ("gptbot", "claudebot", "ccbot", "somethingelse"):
                want = {}
                if token in named:
                    for agents, sigs, _ in groups:
                        if token in [a.lower() for a in agents]:
                            want.update(sigs)
                else:
                    want = dict(star)
                self.assertEqual(rc.signals_for(r, token)[0], want, "run %d %s\n%s" % (i, token, text))


class Cli(unittest.TestCase):
    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_report(self):
        code, out, _ = self.run_cli("signals", FIXTURE, "--agent", "*", "--agent", "GPTBot", "--agent", "ClaudeBot")
        self.assertEqual(code, 0)
        self.assertIn("License (global, line 2): https://example.com/license.xml", out)
        self.assertIn("group '*' (line 4): search=yes, ai-input=no, ai-train=no", out)
        self.assertIn("group 'gptbot' (line 9): no signals", out)
        self.assertRegex(out, r"GPTBot\s+group 'gptbot'\s+search=-\s+ai-input=-\s+ai-train=-\s+License global")
        self.assertRegex(out, r"ClaudeBot\s+group 'claudebot'\s+search=-\s+ai-input=-\s+ai-train=no")

    def test_default_agents_include_the_known_ai_crawlers(self):
        code, out, _ = self.run_cli("signals", FIXTURE)
        self.assertEqual(code, 0)
        for token in ("Bytespider", "CCBot", "Claude-SearchBot", "OAI-SearchBot"):
            self.assertIn(token, out)
        self.assertRegex(out, r"Bytespider\s+group '\*'\s+search=yes\s+ai-input=no\s+ai-train=no")

    def test_expect(self):
        code, out, _ = self.run_cli("signals", FIXTURE, "--expect", "ai-train=no")
        self.assertEqual(code, 1)
        self.assertIn("MISMATCH  GPTBot: ai-train=no expected but not stated (its group 'gptbot' has no Content-Signal for it)", out)
        self.assertNotIn("MISMATCH  Bytespider", out)
        self.assertEqual(self.run_cli("signals", FIXTURE, "--agent", "*", "--agent", "ClaudeBot", "--expect", "ai-train=no")[0], 0)
        code, out, _ = self.run_cli("signals", FIXTURE, "--agent", "*", "--expect", "search=no")
        self.assertEqual(code, 1)
        self.assertIn("search=no expected but is yes", out)
        self.assertEqual(self.run_cli("signals", FIXTURE, "--agent", "*", "--expect", "search=yes,ai-input=no,ai-train=no")[0], 0)

    def test_bad_arguments_and_files(self):
        for bad in ("nonsense", "ai-train=maybe", "spam=no", ","):
            self.assertEqual(self.run_cli("signals", FIXTURE, "--expect", bad)[0], 2, bad)
        self.assertEqual(self.run_cli("signals", "/nonexistent/robots.txt")[0], 2)

    def test_json(self):
        code, out, _ = self.run_cli("signals", FIXTURE, "--agent", "GPTBot", "--expect", "ai-train=no", "--json")
        data = json.loads(out)
        self.assertEqual(code, 1)
        self.assertEqual(data["global_licenses"], ["https://example.com/license.xml"])
        self.assertEqual(data["effective"], [{"agent": "GPTBot", "group": "gptbot", "signals": {}, "licenses": ["https://example.com/license.xml"], "license_scope": "global"}])
        self.assertEqual(data["groups"][0]["signals"], {"search": "yes", "ai-input": "no", "ai-train": "no"})
        self.assertEqual(len(data["mismatches"]), 1)

    def test_lint_reports_the_new_codes_with_lines(self):
        path = os.path.join(HERE, "data", "signals.txt")
        code, out, _ = self.run_cli("lint", path)
        self.assertEqual(code, 0)
        self.assertNotIn("unknown-directive", out)


REAL = os.environ.get("ROBOTS_REAL_DIR")


@unittest.skipUnless(REAL, "set ROBOTS_REAL_DIR to a folder of real robots.txt files (named like cloudflare_com.txt) to run these")
class RealFiles(unittest.TestCase):
    """Public robots.txt files that use these directives, fetched on 2026-09-26 and not stored in the repository."""

    def load(self, name):
        with open(os.path.join(REAL, name + ".txt"), encoding="utf-8") as fh:
            return rc.parse(fh.read())

    def test_no_false_unknown_directive_warnings(self):
        for name in ("cloudflare_com", "developers_cloudflare_com", "contentsignals_org", "rslstandard_org", "medium_com", "blog_cloudflare_com"):
            if os.path.exists(os.path.join(REAL, name + ".txt")):
                self.assertFalse([i for i in self.load(name).issues if i.code == "unknown-directive"], name)

    def test_observed_signals(self):
        want = {"ai-train": "yes", "search": "yes", "ai-input": "yes"}
        for name in ("contentsignals_org", "developers_cloudflare_com"):
            if os.path.exists(os.path.join(REAL, name + ".txt")):
                self.assertEqual(rc.signals_for(self.load(name), "*"), (want, "*"), name)

    def test_cloudflare_dot_com_puts_the_line_in_the_last_named_group(self):
        path = os.path.join(REAL, "cloudflare_com.txt")
        if not os.path.exists(path):
            self.skipTest("cloudflare_com.txt not present")
        r = self.load("cloudflare_com")
        self.assertEqual(rc.signals_for(r, "*")[0], {})
        self.assertEqual(rc.signals_for(r, "Cohere-ai")[0], {"ai-train": "yes", "search": "yes", "ai-input": "yes"})
        self.assertIn("content-signal-not-for-all", [i.code for i in r.issues])

    def test_rsl_licences(self):
        if os.path.exists(os.path.join(REAL, "rslstandard_org.txt")):
            r = self.load("rslstandard_org")
            self.assertEqual(rc.licenses_for(r, "GPTBot"), (["https://rslcollective.org/royalty.xml"], "global"))
        if os.path.exists(os.path.join(REAL, "medium_com.txt")):
            r = self.load("medium_com")
            self.assertEqual(rc.licenses_for(r, "ClaudeBot")[1], "group")
            self.assertEqual(rc.licenses_for(r, "SomeOtherBot")[1], "none")


if __name__ == "__main__":
    unittest.main()
