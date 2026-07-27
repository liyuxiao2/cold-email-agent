# Research Worker — Notes

## `find_company_url`

Builds a search query from all non-null fields on the lead (company name, funding stage, etc.) and hits the Brave Web Search API with `count=5`. Fetches 5 candidates instead of 1 because the top result isn't always the official homepage — aggregators like Crunchbase or LinkedIn frequently rank above it.

Brave response shape:
```json
{
  "web": {
    "results": [
      { "url": "...", "title": "...", "description": "..." }
    ]
  }
}
```

## `select_best_url`

Scores each result and returns the best URL.

**Blocklist filter** — domains in `AGGREGATOR_BLOCKLIST` (constants.py) are skipped. Uses `in` rather than `==` so subdomains like `news.ycombinator.com` are caught by `"ycombinator.com"`.

**Slug match** — both the company name and the domain are reduced to bare alphanumeric chars before comparing (`re.sub(r"[^a-z0-9]", "", ...)`), so punctuation, TLDs, and casing don't interfere. Match → score 1, no match → score 0.

**Stable sort** — `list.sort` is stable in Python, so equal-scored results stay in their original Brave order. Ties defer to Brave's own relevance ranking rather than arbitrary reordering.

**Fallback** — if every result was on the blocklist, returns `results[0]["url"]` anyway. Some URL is better than `None` for the scraping step downstream.
