import contextlib
import io
import json
import os
import re
import tempfile
import unittest

import robotscheck as rc
from robotscheck import aibots
from robotscheck.__main__ import main

HERE = os.path.dirname(os.path.abspath(__file__))
POLICY_FILE = os.path.join(HERE, "data", "ai_policy.txt")


def rows(text, paths=("/",), bots=aibots.BOTS):
    return {r.bot.token: r for r in aibots.analyse(rc.parse(text), paths, bots)}


class BotList(unittest.TestCase):
    def test_list_is_well_formed(self):
        tokens = [b.token.lower() for b in aibots.BOTS]
        self.assertEqual(len(tokens), len(set(tokens)))
        for b in aibots.BOTS:
            self.assertIn(b.category, aibots.CATEGORIES, b.token)
            self.assertTrue(b.vendor, b.token)
            self.assertRegex(b.token, r"^[A-Za-z][A-Za-z0-9_-]*$")
            self.assertEqual(rc.product_token(b.token), b.token.lower(), "robots.txt matching must see the whole token: " + b.token)
        self.assertTrue(re.fullmatch(r"\d{4}-\d{2}-\d{2}", aibots.AS_OF))

    def test_every_category_has_members_and_titles(self):
        for c in aibots.CATEGORIES:
            self.assertTrue(any(b.category == c for b in aibots.BOTS), c)
            self.assertIn(c, aibots.CATEGORY_TITLES)

    def test_user_bots_carry_a_note_about_robots(self):
        for b in aibots.BOTS:
            if b.category == "user":
                self.assertTrue(b.note, b.token)


class Analyse(unittest.TestCase):
    def test_empty_robots_allows_everyone(self):
        r = rows("")
        self.assertTrue(all(x.allowed_everywhere for x in r.values()))
        self.assertTrue(all(x.group == "*" for x in r.values()))

    def test_star_block_with_a_specific_allow(self):
        r = rows("User-agent: *\nDisallow: /\n\nUser-agent: OAI-SearchBot\nAllow: /\n")
        self.assertTrue(r["OAI-SearchBot"].allowed_everywhere)
        self.assertEqual(r["OAI-SearchBot"].group, "oai-searchbot")
        self.assertTrue(r["GPTBot"].blocked_everywhere)
        self.assertEqual(r["GPTBot"].group, "*")

    def test_a_named_group_replaces_the_star_group_entirely(self):
        # the classic surprise: naming a bot means the '*' rules no longer apply to it
        r = rows("User-agent: GPTBot\nDisallow: /private/\n\nUser-agent: *\nDisallow: /\n", paths=("/", "/private/x"))
        self.assertEqual([ok for _, ok, _ in r["GPTBot"].results], [True, False])
        self.assertTrue(r["ClaudeBot"].blocked_everywhere)

    def test_case_insensitive_tokens_and_shared_groups(self):
        r = rows("user-agent: gptbot\nuser-agent: CLAUDEBOT\nDisallow: /\n")
        self.assertTrue(r["GPTBot"].blocked_everywhere and r["ClaudeBot"].blocked_everywhere)
        self.assertTrue(r["CCBot"].allowed_everywhere)

    def test_multiple_paths_partial_and_rule_text(self):
        r = rows("User-agent: *\nDisallow: /members/\nAllow: /members/join\n", paths=("/", "/members/x", "/members/join"))
        res = r["GPTBot"].results
        self.assertEqual([ok for _, ok, _ in res], [True, False, True])
        self.assertEqual(res[1][2], "Disallow: /members/ (line 2)")
        self.assertFalse(r["GPTBot"].allowed_everywhere or r["GPTBot"].blocked_everywhere)

    def test_custom_bot_list(self):
        r = rows("User-agent: MyBot\nDisallow: /\n", bots=[aibots.Bot("MyBot", "Me", "other")])
        self.assertEqual(list(r), ["MyBot"])
        self.assertTrue(r["MyBot"].blocked_everywhere)


class Policy(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(aibots.parse_policy("training=block, search=allow"), {"training": "block", "search": "allow"})
        self.assertEqual(aibots.parse_policy("all=block"), {c: "block" for c in aibots.CATEGORIES})
        for spec in ("all=block,search=allow", "search=allow,all=block"):
            self.assertEqual(aibots.parse_policy(spec)["search"], "allow", spec)
            self.assertEqual(aibots.parse_policy(spec)["training"], "block", spec)
        self.assertEqual(aibots.parse_policy("TRAINING=BLOCK"), {"training": "block"})

    def test_parse_errors(self):
        for bad in ("", "training", "training=maybe", "spam=block", "=block", "training=", ","):
            with self.assertRaises(ValueError, msg=bad):
                aibots.parse_policy(bad)

    def test_violations(self):
        r = aibots.analyse(rc.parse("User-agent: GPTBot\nDisallow: /\n"), ["/"])
        v = aibots.violations(r, {"training": "block"})
        self.assertNotIn("GPTBot", " ".join(v))
        self.assertIn("ClaudeBot (training) is not blocked on: /", v)
        self.assertEqual(aibots.violations(r, {"search": "allow"}), [])
        v = aibots.violations(r, {"training": "allow"})
        self.assertEqual(v, ["GPTBot (training) is blocked on: /"])

    def test_summary(self):
        r = aibots.analyse(rc.parse("User-agent: GPTBot\nUser-agent: CCBot\nDisallow: /\n"))
        s = aibots.summary(r)
        self.assertEqual(s["training"][0], 2)
        self.assertEqual(s["training"][1], sum(1 for b in aibots.BOTS if b.category == "training"))
        self.assertEqual(s["search"][0], 0)


class BotsFile(unittest.TestCase):
    def test_load_and_merge(self):
        bots = aibots.load_bots_file("# my list\nNewBot,training,Acme,collects data\n\ngptbot,search,OpenAI  # corrected\n")
        self.assertEqual([(b.token, b.category, b.vendor, b.note) for b in bots], [("NewBot", "training", "Acme", "collects data"), ("gptbot", "search", "OpenAI", "")])
        merged = aibots.merge_bots(aibots.BOTS, bots)
        by = {b.token.lower(): b for b in merged}
        self.assertEqual(by["gptbot"].category, "search")
        self.assertIn("newbot", by)
        self.assertEqual(len(merged), len(aibots.BOTS) + 1)

    def test_errors(self):
        for bad in ("justtoken", "Bot,nonsense", ",training"):
            with self.assertRaises(ValueError, msg=bad):
                aibots.load_bots_file(bad)


class Cli(unittest.TestCase):
    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_report_without_policy_exits_zero(self):
        code, out, _ = self.run_cli("bots", POLICY_FILE)
        self.assertEqual(code, 0)
        self.assertIn("BLOCKED  GPTBot", out)
        self.assertIn("ALLOWED  Claude-SearchBot", out)
        self.assertIn("group '*'", out)
        self.assertIn("Blocked on every path: training 4 of 8", out)
        self.assertIn("as of " + aibots.AS_OF, out)

    def test_policy_met_and_not_met(self):
        code, out, _ = self.run_cli("bots", POLICY_FILE, "--policy", "search=allow,user=allow")
        self.assertEqual((code, "policy met" in out), (0, True))
        code, out, _ = self.run_cli("bots", POLICY_FILE, "--policy", "training=block")
        self.assertEqual(code, 1)
        self.assertIn("MISMATCH  Bytespider (training) is not blocked on: /", out)
        self.assertIn("policy NOT met", out)

    def test_multiple_paths_show_partial(self):
        code, out, _ = self.run_cli("bots", POLICY_FILE, "--path", "/", "--path", "/cart")
        self.assertEqual(code, 0)
        self.assertIn("PARTIAL  Amazonbot", out)

    def test_bad_arguments_exit_two(self):
        self.assertEqual(self.run_cli("bots", POLICY_FILE, "--policy", "nonsense")[0], 2)
        self.assertEqual(self.run_cli("bots", POLICY_FILE, "--bots-file", "/nonexistent")[0], 2)
        self.assertEqual(self.run_cli("bots", "/nonexistent/robots.txt")[0], 2)

    def test_bots_file_and_json(self):
        with tempfile.TemporaryDirectory() as d:
            bf = os.path.join(d, "bots.txt")
            with open(bf, "w") as fh:
                fh.write("ai_policy_probe,other,Test\n")
            code, out, _ = self.run_cli("bots", POLICY_FILE, "--bots-file", bf, "--json", "--policy", "other=allow")
            data = json.loads(out)
            self.assertEqual(code, 0)
            self.assertEqual(data["as_of"], aibots.AS_OF)
            self.assertIn("ai_policy_probe", [b["token"] for b in data["bots"]])
            gpt = [b for b in data["bots"] if b["token"] == "GPTBot"][0]
            self.assertEqual(gpt["results"], [{"path": "/", "allowed": False, "rule": "Disallow: / (line 5)"}])
            self.assertEqual(data["violations"], [])


if __name__ == "__main__":
    unittest.main()
