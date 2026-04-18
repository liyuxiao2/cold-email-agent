EMAIL_DRAFT_SYSTEM = (
    "You write cold emails for software engineers reaching out to potential employers. "
    "The emails are peer-to-peer, specific, and short. You never use filler openers. "
    "You always reference something specific from research. "
    "You always end with one clear ask.\n\n"
    "Rules:\n"
    "- No 'I hope this email finds you well' or similar openers\n"
    "- First sentence must reference a specific detail from the research\n"
    "- Body ≤ 150 words total\n"
    "- One ask in the final sentence only\n"
    "- Tone: confident peer, not job applicant\n"
    "- Do not mention 'internship' or 'opportunity' — just propose a conversation"
)

EMAIL_DRAFT_TOOL = {
    "name": "write_email",
    "description": "Write a cold email subject line and body",
    "input_schema": {
        "type": "object",
        "properties": {
            "subject": {
                "type": "string",
                "description": "Email subject line",
            },
            "body": {
                "type": "string",
                "description": "Email body, ≤ 150 words",
            },
        },
        "required": ["subject", "body"],
    },
}


def build_email_draft_messages(
    sender_name: str,
    sender_role: str,
    sender_company: str,
    founder_name: str,
    founder_title: str,
    company_name: str,
    tech_stack: list[str],
    recent_news: str,
    hook: str,
) -> list[dict]:
    return [
        {
            "role": "user",
            "content": (
                f"Sender: {sender_name}, {sender_role} at {sender_company}\n"
                f"Recipient: {founder_name}, {founder_title} at {company_name}\n"
                f"Tech stack: {', '.join(tech_stack)}\n"
                f"Recent news: {recent_news}\n"
                f"Hook: {hook}\n\n"
                "Write the subject line and email body."
            ),
        }
    ]
