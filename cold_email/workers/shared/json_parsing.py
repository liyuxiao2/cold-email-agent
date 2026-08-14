"""Parse a model's JSON response, tolerating a ```json code fence.

Both research extraction and drafting ask the LLM for a single JSON object and
occasionally get it wrapped in a markdown fence. This is the one place that
strips the fence and decodes, so the two workers can't drift on parse behavior.
"""

import json
import logging

logger = logging.getLogger(__name__)

JSON_BLOCK_START_MARKER = "```json"
JSON_BLOCK_END_MARKER = "```"


def parse_fenced_json(raw: str) -> dict:
    """Decode a JSON object from `raw`, stripping an optional ```json ... ``` fence.

    Fail-soft: returns {} when `raw` is missing or not valid JSON, because every
    caller treats an empty dict as "unusable" and fails the lead — a malformed
    model response must never crash the worker.
    """
    if not raw:
        return {}
    raw_json = raw.strip()
    if raw_json.startswith(JSON_BLOCK_START_MARKER) and raw_json.endswith(JSON_BLOCK_END_MARKER):
        raw_json = raw_json[len(JSON_BLOCK_START_MARKER) : -len(JSON_BLOCK_END_MARKER)].strip()
    try:
        return json.loads(raw_json)
    except json.JSONDecodeError:
        logger.error(f"Failed to parse JSON response: {raw_json}")
        return {}
