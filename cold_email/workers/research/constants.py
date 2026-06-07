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

# Brave search settings
BRAVE_SEARCH_RESULT_COUNT = 5
BRAVE_SEARCH_TIMEOUT = 10.0  # seconds

# Scraper settings
MIN_SCRAPED_TEXT_LEN = 300
MAX_SCRAPED_TEXT_LEN = 8000
SCRAPE_TIMEOUT = 10.0  # seconds
SCRAPE_EXCLUDE_TAGS = ["script", "style", "footer", "nav", "iframe", "aside", "form"]

# HTTP settings
HTTP_STATUS_OK = 200

# Model settings
GEMINI_MODEL_NAME = "gemini-2.5-flash"

# JSON formatting extraction markers
JSON_BLOCK_START_MARKER = "```json"
JSON_BLOCK_END_MARKER = "```"

# Regex and Scoring constants
SLUG_CLEANUP_REGEX = r"[^a-z0-9]"
DOMAIN_MATCH_SCORE = 1
DOMAIN_MISMATCH_SCORE = 0
