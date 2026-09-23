import contextlib
import io
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import robotscheck as rc
from robotscheck.__main__ import main

DATA = os.path.join(HERE, "data")


def read(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as handle:
        return handle.read()


def allowed(text, path, agent="*"):
    return rc.check(rc.parse(text), agent, path)[0]


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue(), err.getvalue()


class Matching(unittest.TestCase):
    """The path-matching examples from Google's and RFC 9309's documentation."""

    def test_prefix_is_case_sensitive(self):
        r = "User-agent: *\nDisallow: /fish\n"
        for path in ["/fish", "/fish.html", "/fish/salmon.html", "/fishheads", "/fishheads/yummy.html", "/fish.php?id=anything"]:
            self.assertFalse(allowed(r, path), path)
        for path in ["/Fish.asp", "/catfish", "/?id=fish", "/desert/fish"]:
            self.assertTrue(allowed(r, path), path)

    def test_trailing_slash_means_a_folder(self):
        r = "User-agent: *\nDisallow: /fish/\n"
        self.assertFalse(allowed(r, "/fish/"))
        self.assertFalse(allowed(r, "/fish/salmon.htm"))
        self.assertTrue(allowed(r, "/fish"))
        self.assertTrue(allowed(r, "/fish.html"))

    def test_wildcard(self):
        r = "User-agent: *\nDisallow: /*.php\n"
        for path in ["/index.php", "/filename.php", "/folder/filename.php", "/folder/filename.php?parameters", "/folder/any.php.file.html"]:
            self.assertFalse(allowed(r, path), path)
        for path in ["/", "/windows.PHP"]:
            self.assertTrue(allowed(r, path), path)

    def test_end_anchor(self):
        r = "User-agent: *\nDisallow: /*.php$\n"
        self.assertFalse(allowed(r, "/filename.php"))
        self.assertFalse(allowed(r, "/folder/filename.php"))
        self.assertTrue(allowed(r, "/filename.php?parameters"))
        self.assertTrue(allowed(r, "/filename.php/"))
        self.assertTrue(allowed(r, "/filename.php5"))

    def test_query_string_is_part_of_the_path(self):
        r = "User-agent: *\nDisallow: /*?sessionid=\n"
        self.assertFalse(allowed(r, "/page?sessionid=abc"))
        self.assertTrue(allowed(r, "https://example.com/a/b?x=1&sessionid=9"))     # "?sessionid=" is not in there; "&sessionid=" needs its own rule
        self.assertTrue(allowed(r, "/page?other=1"))

    def test_longest_match_wins(self):
        self.assertTrue(allowed("User-agent: *\nAllow: /p\nDisallow: /\n", "/page"))
        self.assertFalse(allowed("User-agent: *\nAllow: /p\nDisallow: /\n", "/other"))
        self.assertFalse(allowed("User-agent: *\nAllow: /\nDisallow: /p\n", "/page"))

    def test_tie_goes_to_allow_whichever_is_first(self):
        self.assertTrue(allowed("User-agent: *\nAllow: /folder\nDisallow: /folder\n", "/folder/page"))
        self.assertTrue(allowed("User-agent: *\nDisallow: /folder\nAllow: /folder\n", "/folder/page"))

    def test_wildcard_length_counts_the_pattern(self):
        r = "User-agent: *\nAllow: /page\nDisallow: /*.htm\n"
        self.assertFalse(allowed(r, "/page.htm"))       # '/*.htm' (6) is longer than '/page' (5)

    def test_empty_disallow_allows_everything(self):
        self.assertTrue(allowed("User-agent: *\nDisallow:\n", "/anything"))

    def test_no_rules_no_restriction(self):
        self.assertTrue(allowed("User-agent: *\n", "/x"))
        self.assertTrue(allowed("", "/x"))

    def test_robots_txt_itself_is_always_allowed(self):
        self.assertTrue(allowed("User-agent: *\nDisallow: /\n", "/robots.txt"))

    def test_percent_encoding_is_normalised(self):
        r = "User-agent: *\nDisallow: /café\n"
        self.assertFalse(allowed(r, "/caf%C3%A9"))
        self.assertFalse(allowed(r, "/caf%c3%a9/menu"))
        r = "User-agent: *\nDisallow: /caf%C3%A9\n"
        self.assertFalse(allowed(r, "/café"))

    def test_full_urls_are_reduced_to_path_and_query(self):
        self.assertFalse(allowed("User-agent: *\nDisallow: /a\n", "https://example.com/a/b"))
        self.assertTrue(allowed("User-agent: *\nDisallow: /a\n", "https://example.com"))


class Groups(unittest.TestCase):
    def setUp(self):
        self.robots = rc.parse(read("example.txt"))

    def test_star_group_for_unlisted_crawlers(self):
        self.assertFalse(rc.check(self.robots, "SomeOtherBot", "/cart")[0])
        self.assertTrue(rc.check(self.robots, "SomeOtherBot", "/private/x")[0])
        self.assertTrue(rc.check(self.robots, "SomeOtherBot", "/admin/public/page")[0])
        self.assertFalse(rc.check(self.robots, "SomeOtherBot", "/admin/settings")[0])

    def test_named_group_replaces_star(self):
        self.assertTrue(rc.check(self.robots, "Googlebot", "/cart")[0])            # * rules do not apply to Googlebot
        self.assertFalse(rc.check(self.robots, "Googlebot", "/private/x")[0])
        self.assertTrue(rc.check(self.robots, "Googlebot", "/private/press-kit.pdf")[0])

    def test_two_agents_share_a_group(self):
        self.assertFalse(rc.check(self.robots, "bingbot", "/private/x")[0])
        self.assertEqual(len(self.robots.groups), 3)

    def test_full_user_agent_string(self):
        ua = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
        self.assertEqual(rc.product_token("Googlebot/2.1"), "googlebot")
        self.assertEqual(rc.product_token("GoogleBot"), "googlebot")
        self.assertTrue(rc.check(self.robots, "Googlebot/2.1", "/cart")[0])

    def test_blocked_bot(self):
        self.assertFalse(rc.check(self.robots, "BadBot", "/")[0])

    def test_rule_reported(self):
        ok, rule = rc.check(self.robots, "*", "/admin/public/x")
        self.assertTrue(ok)
        self.assertEqual((str(rule), rule.line), ("Allow: /admin/public/", 6))

    def test_same_agent_in_two_groups_is_merged(self):
        r = rc.parse("User-agent: a\nDisallow: /one\n\nUser-agent: a\nDisallow: /two\n")
        self.assertFalse(rc.check(r, "a", "/one")[0])
        self.assertFalse(rc.check(r, "a", "/two")[0])
        self.assertIn("duplicate-group", [i.code for i in r.issues])

    def test_sitemaps(self):
        self.assertEqual([u for u, _ in self.robots.sitemaps], ["https://www.example.com/sitemap.xml", "https://www.example.com/sitemap-blog.xml"])


class Parsing(unittest.TestCase):
    def test_bom_and_crlf(self):
        r = rc.parse(read("bom_crlf.txt"))
        self.assertEqual(len(r.groups), 1)
        self.assertFalse(rc.check(r, "*", "/crlf")[0])

    def test_comments_and_blank_lines(self):
        r = rc.parse("# hello\n\nUser-agent: * # everyone\nDisallow: /x # private\n")
        self.assertFalse(rc.check(r, "*", "/x")[0])

    def test_field_names_are_case_insensitive_values_are_not(self):
        r = rc.parse("USER-AGENT: *\nDISALLOW: /Private\n")
        self.assertFalse(rc.check(r, "*", "/Private")[0])
        self.assertTrue(rc.check(r, "*", "/private")[0])


class Lint(unittest.TestCase):
    def codes(self, text):
        return sorted(i.code for i in rc.parse(text).issues)

    def test_problem_file(self):
        r = rc.parse(read("problems.txt"))
        got = sorted(set(i.code for i in r.issues))
        self.assertEqual(got, ["blocks-everything", "crawl-delay", "duplicate-group", "no-colon", "noindex", "path-not-slash",
                               "rule-outside-group", "sitemap-not-absolute", "unknown-directive"])

    def test_clean_file(self):
        self.assertEqual(self.codes(read("example.txt")), [])

    def test_empty_and_no_group(self):
        self.assertEqual(self.codes(""), ["empty"])
        self.assertEqual(self.codes("Sitemap: https://example.com/s.xml\n"), ["no-user-agent"])

    def test_too_large(self):
        big = "User-agent: *\n" + "Disallow: /x\n" * 50000
        self.assertIn("too-large", self.codes(big))

    def test_line_numbers(self):
        r = rc.parse(read("problems.txt"))
        by = {i.code: i.line for i in r.issues}
        self.assertEqual((by["rule-outside-group"], by["path-not-slash"], by["no-colon"]), (1, 3, 7))


class Cli(unittest.TestCase):
    def path(self, name):
        return os.path.join(DATA, name)

    def test_test_command(self):
        code, out, _ = run_cli("test", self.path("example.txt"), "/cart", "/blog/post", "/admin/public/a", "--agent", "SomeBot")
        self.assertEqual(code, 1)
        self.assertIn("agent somebot uses the '*' group", out)
        self.assertIn("BLOCKED  /cart", out)
        self.assertIn("Disallow: /cart (line 4)", out)
        self.assertIn("ALLOWED  /blog/post", out)
        self.assertIn("no rule matches", out)
        self.assertIn("Allow: /admin/public/ (line 6)", out)

    def test_all_allowed_exits_zero(self):
        code, out, _ = run_cli("test", self.path("example.txt"), "/blog", "--agent", "Googlebot")
        self.assertEqual(code, 0)
        self.assertIn("uses the 'googlebot' group", out)

    def test_lint_command(self):
        code, out, _ = run_cli("lint", self.path("problems.txt"))
        self.assertEqual(code, 0)
        self.assertIn("blocks-everything", out)
        self.assertEqual(run_cli("lint", self.path("problems.txt"), "--strict")[0], 1)
        code, out, _ = run_cli("lint", self.path("example.txt"), "--strict")
        self.assertEqual(code, 0)
        self.assertIn("0 issue(s) to look at", out)

    def test_sitemaps_command(self):
        code, out, _ = run_cli("sitemaps", self.path("example.txt"))
        self.assertEqual(out.split(), ["https://www.example.com/sitemap.xml", "https://www.example.com/sitemap-blog.xml"])
        self.assertEqual(run_cli("sitemaps", self.path("bom_crlf.txt"))[0], 1)

    def test_missing_file(self):
        code, _, err = run_cli("lint", "/no/such/robots.txt")
        self.assertEqual(code, 2)
        self.assertIn("error:", err)


if __name__ == "__main__":
    unittest.main()
