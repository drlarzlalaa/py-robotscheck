# py-robotscheck

Parse `robots.txt` and test URLs against it, following [RFC 9309](https://www.rfc-editor.org/rfc/rfc9309). A zero-dependency Python package and command-line tool. Python's built-in `urllib.robotparser` predates the standard and does not understand the `*` wildcard or the `$` end anchor in paths, or longest-match-wins; this does, and it tells you **which rule on which line** decided each URL. A linter catches the mistakes that quietly break crawling, such as a stray `Disallow: /` under `User-agent: *`.

```
$ python -m robotscheck test tests/data/example.txt /cart /blog/post /admin/public/a "/page?sessionid=1" --agent SomeBot
agent somebot uses the '*' group
BLOCKED  /cart                                    Disallow: /cart (line 4)
ALLOWED  /blog/post                               no rule matches
ALLOWED  /admin/public/a                          Allow: /admin/public/ (line 6)
BLOCKED  /page?sessionid=1                        Disallow: /*?sessionid= (line 5)

$ python -m robotscheck test tests/data/example.txt /cart /private/x /private/press-kit.pdf --agent "Googlebot/2.1"
agent googlebot uses the 'googlebot' group
ALLOWED  /cart                                    no rule matches
BLOCKED  /private/x                               Disallow: /private/ (line 10)
ALLOWED  /private/press-kit.pdf                   Allow: /private/press-kit.pdf (line 11)

$ python -m robotscheck lint tests/data/problems.txt
warning  rule-outside-group   line 1: Disallow before any User-agent line is ignored
warning  path-not-slash       line 3: Disallow path 'private' should start with / or *
warning  unknown-directive    line 4: unknown directive 'Disalow' is ignored
info     crawl-delay          line 5: Google ignores Crawl-delay (Bing and some others honour it)
info     noindex              line 6: Google stopped supporting Noindex in robots.txt in 2019; use a meta robots tag or X-Robots-Tag header
warning  no-colon             line 7: not a 'field: value' line, ignored: 'this line has no colon'
warning  sitemap-not-absolute line 8: Sitemap must be a full http(s) URL
warning  duplicate-group      line 9: user-agent '*' has more than one group (rules are merged)
warning  blocks-everything    line 10: 'User-agent: *' with 'Disallow: /' blocks the whole site for every crawler
3 group(s), 1 sitemap(s), 7 issue(s) to look at
```

Notice the second example: Googlebot has its own group, so the `User-agent: *` rules (including `Disallow: /cart`) **do not apply to it at all**. That surprises many people, and it is exactly what `test` shows you. The sample files describe a synthetic site (`example.com`).

`test` exits **1** if any URL is blocked, so it can guard a deploy ("the pages that must be crawlable still are"); `lint` exits 0 unless there are errors (`--strict` also fails on warnings); 2 means an unreadable file.

## Install and use

No dependencies; Python 3.9 or newer.

```
pip install git+https://github.com/drlarzlalaa/py-robotscheck
robotscheck lint robots.txt
robotscheck test robots.txt /pricing /blog/post --agent Googlebot
curl -s https://www.example.com/robots.txt | robotscheck test - /admin/
```

or run it from a checkout with `python -m robotscheck`.

| Command | What it does |
|---|---|
| `test FILE URL... [--agent NAME]` | For each URL or path: ALLOWED or BLOCKED, and the deciding rule with its line number. `--agent` takes a name (`Googlebot`) or a full User-agent string; the default is a crawler with no group of its own. |
| `lint FILE [--strict]` | Report likely mistakes with line numbers. |
| `sitemaps FILE` | List the `Sitemap:` lines (exit 1 if there are none). |
| `bots FILE [--policy SPEC]` | How the file treats known AI crawlers, optionally checked against a policy. See below. |
| `signals FILE [--expect SPEC]` | The `Content-Signal` and `License` lines, and what each crawler ends up with. See below. |

As a library:

```python
import robotscheck as rc

robots = rc.parse(open("robots.txt").read())
allowed, rule = rc.check(robots, "Googlebot", "https://example.com/private/x")
```

## AI crawlers: `bots`

Most sites now want a policy for AI crawlers (block training, allow the ones that send readers, decide about the rest), and `robots.txt` is where it is written. `bots` reads a `robots.txt` and reports, for each well-known AI crawler, which group applies to it and whether it may fetch your paths. With `--policy` it becomes a check that exits 1 when the file does not implement what you meant.

```
$ python -m robotscheck bots tests/data/ai_policy.txt --policy training=block,search=allow,user=allow
AI crawlers against / (crawler list as of 2026-09-26; it changes, see the README)

TRAINING (collects content to train models)
  BLOCKED  GPTBot              OpenAI        group 'gptbot'             Disallow: / (line 5)
  BLOCKED  ClaudeBot           Anthropic     group 'claudebot'          Disallow: / (line 5)
  ALLOWED  anthropic-ai        Anthropic     group '*'                  no rule matches
  BLOCKED  Google-Extended     Google        group 'google-extended'    Disallow: / (line 8)
  ALLOWED  Applebot-Extended   Apple         group '*'                  no rule matches
  ALLOWED  meta-externalagent  Meta          group '*'                  no rule matches
  ALLOWED  Bytespider          ByteDance     group '*'                  no rule matches
  BLOCKED  CCBot               Common Crawl  group 'ccbot'              Disallow: / (line 5)

SEARCH (builds an index that AI search products cite)
  ALLOWED  OAI-SearchBot       OpenAI        group 'oai-searchbot'      Allow: / (line 12)
  ALLOWED  Claude-SearchBot    Anthropic     group '*'                  no rule matches
  ALLOWED  PerplexityBot       Perplexity    group 'perplexitybot'      Allow: / (line 12)

USER-TRIGGERED (fetches a page because a person asked; may ignore robots.txt)
  ALLOWED  ChatGPT-User        OpenAI        group '*'                  no rule matches
  ALLOWED  Claude-User         Anthropic     group '*'                  no rule matches
  ALLOWED  Perplexity-User     Perplexity    group '*'                  no rule matches

OTHER
  ALLOWED  OAI-AdsBot          OpenAI        group '*'                  no rule matches
  ALLOWED  Amazonbot           Amazon        group '*'                  no rule matches

Blocked on every path: training 4 of 8, search 0 of 3, user 0 of 3, other 0 of 2
Policy: training=block, search=allow, user=allow
  MISMATCH  anthropic-ai (training) is not blocked on: /
  MISMATCH  Applebot-Extended (training) is not blocked on: /
  MISMATCH  meta-externalagent (training) is not blocked on: /
  MISMATCH  Bytespider (training) is not blocked on: /
policy NOT met
```

Look at the last block: `anthropic-ai`, `Applebot-Extended`, `meta-externalagent` and `Bytespider` are **not blocked**, because the file names some training crawlers but not these, so they fall through to the `User-agent: *` group, which only blocks `/admin/` and `/cart`. That gap is the usual reason a "we block AI training" robots.txt does not.

| Option | What it does |
|---|---|
| `--path P` | A path to test (repeatable, default `/`). With several, a crawler that is blocked on some and not others shows as `PARTIAL`, and a `block` policy needs it blocked on all of them. |
| `--policy SPEC` | Desired treatment, `category=block` or `category=allow`, comma-separated: `training=block,search=allow,user=allow`. Categories are `training`, `search`, `user`, `other` and `all`. Explicit items win over `all`, in either order. |
| `--bots-file F` | Add or correct crawlers: lines of `Token,category[,vendor[,note]]`, `#` comments allowed. An entry replaces the built-in one with the same token. |
| `--json` | Machine-readable output, including the list date, each crawler's group and deciding rule, and the policy violations. |

Exit status: `0` report printed (or policy met), `1` policy not met, `2` bad option, policy or file.

### About the crawler list

The list (16 tokens in `robotscheck/aibots.py`, dated `AS_OF`) is **data that goes out of date**. Categories follow how vendors and reference sources describe them: *training* crawlers collect content for models, *search* crawlers build an index that AI search products cite, and *user-triggered* fetchers retrieve a page because a person asked. On 2026-09-26 the OpenAI and Anthropic entries were checked against those vendors' own pages ([OpenAI's bots page](https://developers.openai.com/api/docs/bots) and [Anthropic's help-centre article](https://support.claude.com/en/articles/8896518-does-anthropic-crawl-data-from-the-web-and-how-can-site-owners-block-the-crawler)), which name `GPTBot`, `OAI-SearchBot`, `ChatGPT-User`, `OAI-AdsBot`, `ClaudeBot`, `Claude-SearchBot` and `Claude-User`. The other vendors' entries (Perplexity, Google, Apple, Meta, ByteDance, Common Crawl, Amazon) come from independent 2026 crawler references, not from those vendors' pages, and `anthropic-ai` is kept only because it is widespread in existing files. Correct the list with `--bots-file` if a vendor changes a name.

Things this can and cannot tell you:

- **User-triggered fetchers may ignore robots.txt.** OpenAI's page says robots.txt rules "may not apply" to `ChatGPT-User`, `Perplexity-User` is reported not to follow it (a secondary report), while Anthropic states `Claude-User` does. A `BLOCKED` verdict for those means "the file asks", not "they will comply". The output says so under that heading.
- **`Google-Extended` and `Applebot-Extended` are control tokens**, not separate crawlers: they are read by Google's and Apple's own crawlers, and govern AI use, not search indexing.
- **A verdict is about the file, not behaviour.** Some crawlers, `Bytespider` in particular, are widely reported to ignore `robots.txt`. Blocking there is a request. To enforce, use server or CDN rules (verify the crawler's published IP ranges, do not trust the User-agent string alone).
- **Crawlers you did not list are governed by `*`.** That is the RFC 9309 rule, and it is the point of the report.

## AI preferences in robots.txt: `Content-Signal` and `License`

Two newer lines now appear in real `robots.txt` files, and older versions of this tool (and most linters) wrongly reported both as unknown directives:

- **`Content-Signal: ai-train=no, search=yes, ai-input=no`**, deployed by Cloudflare across millions of domains. The three signals are defined by the IETF Internet-Draft `draft-romm-aipref-contentsignals` (now expired): `search` (building a search index and showing results), `ai-input` (feeding content to an AI model for retrieval-augmented generation) and `ai-train` (training or fine-tuning models). That draft does not define the line's syntax; the syntax used here is the one seen in real files and described by Cloudflare: comma-separated `name=yes` or `name=no` pairs, placed inside a `User-agent` group. A signal that is not stated means no preference.
- **`License: https://example.com/license.xml`**, from the Really Simple Licensing (RSL 1.0) standard. The value must be an absolute URI. It may appear before the first `User-agent` line (global) or inside a group, and a group's `License` takes precedence over a global one.

`lint` now understands both. `signals` shows what each crawler actually ends up with:

```
$ python -m robotscheck signals tests/data/signals.txt --agent '*' --agent GPTBot --agent ClaudeBot --agent Bytespider --expect ai-train=no
Content-Signal and License lines (signal names from the IETF draft; the line syntax is the one Cloudflare deploys)
  License (global, line 2): https://example.com/license.xml
  group '*' (line 4): search=yes, ai-input=no, ai-train=no
  group 'gptbot' (line 9): no signals
  group 'claudebot' (line 12): ai-train=no

Effective for each crawler (a named group replaces the '*' group; a signal not stated means no preference, shown as -):
  *                    group '*'                search=yes  ai-input=no  ai-train=no   License global
  GPTBot               group 'gptbot'           search=-  ai-input=-  ai-train=-   License global
  ClaudeBot            group 'claudebot'        search=-  ai-input=-  ai-train=no   License global
  Bytespider           group '*'                search=yes  ai-input=no  ai-train=no   License global

Expected: ai-train=no
  MISMATCH  GPTBot: ai-train=no expected but not stated (its group 'gptbot' has no Content-Signal for it)
signals do NOT match
```

Look at `GPTBot`. The file says `ai-train=no` for `*`, but GPTBot has its own group (for a `Disallow`), and a named group **replaces** the `*` group entirely, so under normal robots.txt grouping GPTBot sees no signal at all. `--expect ai-train=no` catches exactly that, and exits 1 when any shown crawler does not state what you expect. By default `signals` shows `*` and every crawler in the AI-crawler list; `--agent` (repeatable) narrows it. A signal that is not stated is shown as `-`, and never counts as a match.

A second mistake, seen in a real, widely deployed file, is placing a `Content-Signal` line **after another group's rules**: a line joins the group above it, so the signal then belongs to that one crawler and everything else that uses `*` sees none. `lint` reports that as `content-signal-not-for-all`. The same applies to `License`.

```
$ python -m robotscheck lint problems.txt
warning  content-signal-value line 7: signal search has value 'maybe'; expected yes or no
warning  content-signal-syntax line 7: 'ai-input' is not name=value
warning  license-not-absolute line 8: License must be an absolute URI (RSL): 'license.xml'
info     content-signal-not-for-all line 7: the Content-Signal on line 7 belongs to the group for ccbot; crawlers that use 'User-agent: *' see no signal. A line placed after another group's rules joins that group, so move it under 'User-agent: *' if you meant it for everyone
info     license-in-group     line 8: the License on line 8 belongs to the group for ccbot only; a License before the first User-agent line applies to every crawler
2 group(s), 0 sitemap(s), 3 issue(s) to look at
```

| Code | Level | Meaning |
| --- | --- | --- |
| `content-signal-syntax` | warning | Not `name=value` pairs, or empty. |
| `content-signal-value` | warning | A value that is not `yes` or `no`. |
| `content-signal-conflict` | warning | The same signal stated as both `yes` and `no` in one group. |
| `content-signal-outside-group` | warning | A `Content-Signal` before any `User-agent` line, which applies to no group. |
| `content-signal-unknown-signal`, `content-signal-duplicate` | info | A name other than `search`, `ai-input`, `ai-train` (kept, not interpreted); the same signal stated twice with the same value. |
| `content-signal-not-for-all` | info | Signals sit only in named groups while `User-agent: *` has none. |
| `license-not-absolute` | warning | The value is not an absolute URI. |
| `license-insecure`, `license-duplicate`, `license-in-group` | info | The licence is fetched over `http`; the same licence twice in one scope; a `License` inside a group, so it applies to that group's crawlers only. |

**Limits.** These lines are *preferences*: they do not stop a crawler, and this tool cannot say who honours them. Group selection for `Content-Signal` is not specified by the draft, so this tool assumes the usual robots.txt behaviour. The `no` value and the "not stated means no preference" rule come from Cloudflare's own documentation as reported by secondary sources; the real files sampled for this project all used `yes`. `License` follows the RSL 1.0 specification (read in full), but the tool never fetches or validates the licence document.

## How matching works (RFC 9309)

- **Which group.** A group that names the crawler's product token (case-insensitive) is used; otherwise the `User-agent: *` group. Several `User-agent` lines in a row share one group, and multiple groups for the same agent are merged. Rules from a different group never leak in.
- **Which rule.** Every `Allow` and `Disallow` pattern that matches the URL's path and query is a candidate; **the longest pattern wins**, and on a tie **`Allow` wins**. An empty `Disallow:` allows everything; no matching rule means allowed. `/robots.txt` itself is always fetchable.
- **Patterns.** Matching is case-sensitive prefix matching; `*` matches any run of characters and a final `$` anchors the end. `Disallow: /*.php$` blocks `/a.php` but not `/a.php?x=1`.
- **Encoding.** Percent-encoding is normalised, so `/caf%C3%A9`, `/caf%c3%a9` and `/café` match each other.

## What `lint` reports

`rule-outside-group`, `path-not-slash`, `unknown-directive` (including typos like `Disalow`), `no-colon`, `sitemap-not-absolute`, `duplicate-group`, `no-user-agent`, `empty`, `too-large` (over 500 KiB), `blocks-everything` (`User-agent: *` with `Disallow: /`), and informational notes on `Crawl-delay`, `Noindex`, `Host`, `Clean-param` and `Request-rate`, which are non-standard or ignored by Google.

## Limits

- **It implements the standard, not every crawler.** Google, Bing, Yandex and others each add quirks. In particular, matching here uses the crawler's own product token; some crawlers (Google's image and news bots, for example) also fall back to a parent group such as `googlebot`, so pass `--agent googlebot` to see that group's verdict.
- **Robots rules are not access control.** They ask well-behaved crawlers to stay away; anything listed in `robots.txt` is publicly readable.
- **Blocking is not de-indexing.** A URL blocked here can still appear in search results if other pages link to it; use a `noindex` meta tag or header (on a page that is *not* blocked) to remove it.
- **Offline.** It never fetches `robots.txt` itself; give it a file or standard input.
- **Only `User-agent`, `Allow`, `Disallow` and `Sitemap`** affect the result; other directives are reported by `lint` but not interpreted.

## How it was checked

```
python -m unittest discover -s tests -v
```

81 tests cover the documented matching examples (prefixes, wildcards, end anchors, longest match, ties), group selection and merging, percent-encoding, BOM and CRLF files, every lint code with line numbers, and the commands with their exit codes. The `signals` tests cover each rule with matching and non-matching lines; 400 generated `robots.txt` files written from a known structure (random groups, signals and licences, in single and split `Content-Signal` lines) that must parse back to exactly that structure; and, for group selection, 300 more where the effective signals are computed independently and compared. The first generator was wrong, not the parser: a group with no line of its own merges into the next group under RFC 9309, and it now always gives each group a line. Four deliberate breakages of the new logic (group choice, licence precedence, conflict versus duplicate, the not-for-all condition) each made tests fail. Against real files, an opt-in test (`ROBOTS_REAL_DIR`) checks public `robots.txt` files that use these lines, fetched on 2026-09-26 and not stored in the repository: none produces a false `unknown-directive` any more, the observed signals and licences read back as expected, and the misplaced-line case is reported. The `bots` tests check the crawler list is well formed (unique tokens, valid categories, each token survives `product_token` unchanged), the group rule that surprises people (a named group replaces `*` entirely), case-insensitive names, several paths, policy parsing and its error cases, both orders of `all=` against explicit items, the bots-file format, JSON output and exit codes; two deliberate breakages of the code (a wrong "blocked everywhere" test and a wrong policy precedence) were confirmed to make tests fail. CI runs them on Python 3.9 to 3.13.

## Licence

MIT (`LICENSE`).
