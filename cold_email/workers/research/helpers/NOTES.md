# Research Worker — URL Resolution Notes

How the research stage decides which URL is a company's real homepage before it
scrapes, extracts, and hands the domain to Hunter. Lives in `extraction.py` and
is orchestrated by `preflight.resolve_lead_url`.

## `find_company_url(lead)`

Keyless web search via DuckDuckGo (`ddgs`). Builds a query from the company name
plus funding stage and asks for `SEARCH_RESULT_COUNT` (5) results — the top hit
is often an aggregator (LinkedIn, Crunchbase) rather than the official site, so
we fetch several and filter. `ddgs` returns each URL under `href`; we remap to
`url` and pass the list to `select_best_url`.

`ddgs` **raises** on transient failures (rate limiting, network) rather than
returning a status code, so those propagate and the task's autoretry recovers
the lead. An empty result list is a genuine "not found".

## `is_probable_homepage(url, company_name)`

The single homepage test, used for both the DDG results **and** the
discovery-scraped `company_url` (so a wrong domain never cascades into scraping,
extraction, or the Hunter lookup). A domain qualifies only if:

- **Not blocklisted** — domains in `AGGREGATOR_BLOCKLIST` (constants.py) are
  rejected. Uses `in`, not `==`, so `news.ycombinator.com` is caught by
  `ycombinator.com`.
- **Slug match** — both the company name and the domain are reduced to bare
  alphanumerics (`SLUG_CLEANUP_REGEX`) so punctuation, TLDs, and casing don't
  interfere; the company slug must appear in the domain slug.

## `select_best_url(results, lead)`

Returns the first result that passes `is_probable_homepage`, else **`None`** —
results are already in search-relevance order. There is deliberately **no
fallback to the first result**: when nothing slug-matches, the lead fails
honestly as "no company URL" rather than proceeding on a guess (a wrong domain
poisons the Hunter email lookup downstream).
