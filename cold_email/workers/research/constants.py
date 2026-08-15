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

# Slug used to match a company name against a candidate domain.
SLUG_CLEANUP_REGEX = r"[^a-z0-9]"

HUNTER_DOMAIN_SEARCH_URL = "https://api.hunter.io/v2/domain-search"
HUNTER_TIMEOUT_SECONDS = 15
# Max contacts to request per domain. Hunter pages results; a startup rarely has
# more than a handful of decision-makers, so a small page keeps credits down.
HUNTER_DOMAIN_SEARCH_LIMIT = 25

# Minimum Hunter confidence (0-100) for a contact to be usable. Unchanged
# threshold, but it is now a PER-CONTACT filter rather than a lead-level gate.
MIN_EMAIL_SCORE = 25

# Positions worth cold-emailing as a candidate. The email template is
# founder-flavored ("I admire what you're building"), so restricting recipients
# to decision-makers and hiring roles keeps it honest with no prompt changes.
# Matched case-insensitively as substrings against Hunter's `position`.
DECISION_MAKER_PATTERNS = (
    "founder",
    "co-founder",
    "cofounder",
    "ceo",
    "cto",
    "coo",
    "chief technology",
    "chief executive",
    "vp engineering",
    "vp of engineering",
    "head of engineering",
    "director of engineering",
    "engineering manager",
    "eng lead",
    "technical lead",
    "recruit",
    "talent",
    "people ops",
    "head of people",
    "hiring",
)

# Terminal failure reason when research finds nobody worth emailing.
ERR_NO_ELIGIBLE_CONTACTS = "No eligible contacts found (Hunter)"

RESEARCH = "research"
