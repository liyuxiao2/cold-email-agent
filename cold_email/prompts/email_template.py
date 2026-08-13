"""The fixed candidate-outreach email template + placeholder fill.

The email body is NOT freeform-written by the LLM. This template is the single
source of structure/tone; the LLM only produces the values for the contextual
tokens ({{company_interest}}, {{admiration_detail}}, {{experience_bullets}}),
while the sender/link tokens are filled deterministically.

Tokens use {{double-brace}} syntax specifically so they never collide with the
markdown link syntax [label](url) that the rendered links use. fill_template
raises on any token with no value so a half-built email can never be sent.
"""

import re

# {{token}} markers; double-brace avoids clashing with markdown [links](url).
TOKEN_RE = re.compile(r"\{\{(\w+)\}\}")

TEMPLATE = """\
Hi {{first_name}},

I know your time is valuable so I'll keep it short. {{intro}}

I'm particularly interested in {{company_interest}}, and am impressed by the \
technical depth of your engineering team. I'm drawn to {{admiration_detail}} \
and wanted to see if there might be a fit for me to contribute in any capacity.

**Recent Experience:**

{{experience_bullets}}

Let me know if there is anyone specific I can contact to learn more about \
potential opportunities to support the team!

Thank you in advance for your time and consideration.

{{sender_first_name}}

{{github_link}} | {{linkedin_link}}
"""


def fill_template(template: str, values: dict[str, str]) -> str:
    """Replace every {{token}} in `template` with values[token].

    Fails loudly (ValueError) on a token with no matching value — the safest
    choice for outreach, so a literal "{{company_interest}}" never ships.
    """

    def repl(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in values:
            raise ValueError(f"No value provided for template token: {{{{{key}}}}}")
        return values[key]

    return TOKEN_RE.sub(repl, template)
