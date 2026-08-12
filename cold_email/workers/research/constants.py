from cold_email.config import settings

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
    "wellfound.com",
    "pitchbook.com",
    "producthunt.com",
    "venturebeat.com",
    # Accelerators / directories / social / reference — never a company homepage,
    # and observed being wrongly resolved as one (techstars.com hit 9x in prod).
    "techstars.com",
    "500.co",
    "tracxn.com",
    "f6s.com",
    "builtin.com",
    "glassdoor.com",
    "wikipedia.org",
    "youtube.com",
    "medium.com",
    "github.com",
    "facebook.com",
    "instagram.com",
    "reddit.com",
}

# URL-discovery search settings. Fetch several candidates because the top hit
# is often an aggregator (Wikipedia, LinkedIn) rather than the official site;
# select_best_url filters and scores them.
SEARCH_RESULT_COUNT = 5

# Scraper settings
MIN_SCRAPED_TEXT_LEN = 300
MAX_SCRAPED_TEXT_LEN = 8000
SCRAPE_TIMEOUT = 10.0  # seconds
SCRAPE_EXCLUDE_TAGS = ["script", "style", "footer", "nav", "iframe", "aside", "form"]

# HTTP settings
HTTP_STATUS_OK = 200

# Model settings — defaults to the shared config value; override with a literal
# here to let research diverge from drafting on model choice.
GEMINI_MODEL_NAME = settings.model_name

# JSON formatting extraction markers
JSON_BLOCK_START_MARKER = "```json"
JSON_BLOCK_END_MARKER = "```"

# Slug used to match a company name against a candidate domain.
SLUG_CLEANUP_REGEX = r"[^a-z0-9]"

HUNTER_EMAIL_FINDER_URL = "https://api.hunter.io/v2/email-finder"
HUNTER_TIMEOUT_SECONDS = 15
# Minimum Hunter confidence (0-100) to accept an email; below this we treat the
# lead as having no reliable address and fail it fast into the DLQ.
MIN_EMAIL_SCORE = 25

# Terminal failure reason when research can't resolve a usable founder email.
ERR_NO_EMAIL_FOUND = "No founder email found (Hunter)"
