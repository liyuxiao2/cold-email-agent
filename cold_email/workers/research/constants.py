BRAVE_SEARCH_API_URL = "https://api.search.brave.com/res/v1/web/search"

BRAVE_SEARCH_HEADERS = {
    "Accept": "application/json",
    "Accept-Encoding": "gzip",
    "X-Subscription-Token": "<YOUR_API_KEY>",
}

# Domains that aggregate startup info but are never the official homepage.
AGGREGATOR_BLOCKLIST = {
    "linkedin.com",
    "crunchbase.com",
    "techcrunch.com",
    "twitter.com",
    "x.com",
    "ycombinator.com",
    "bloomberg.com",
    "forbes.com",
    "angel.co",
    "pitchbook.com",
    "producthunt.com",
    "venturebeat.com",
}
