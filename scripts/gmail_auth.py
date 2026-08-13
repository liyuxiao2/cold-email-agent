"""One-time Gmail OAuth — mint a refresh token for the sender mailbox.

The pipeline sends mail headless from a Celery worker, so it needs a long-lived
*refresh token* (offline access) it can replay with no browser present. This
script runs the interactive consent flow once and prints the four values to set
on Cloud Run.

Prerequisite — create an OAuth client in the GCP console (one time):
  1. https://console.cloud.google.com/apis/credentials  (project cold-email-490016)
  2. Enable the Gmail API if prompted:
     https://console.cloud.google.com/apis/library/gmail.googleapis.com
  3. Configure the OAuth consent screen (External; add your sender email as a
     Test user so consent works without app verification).
  4. Create Credentials -> OAuth client ID -> Application type: "Desktop app".
  5. Download the client JSON.

Then run locally (a browser will open for consent):
  uv run python scripts/gmail_auth.py --client-secret ~/Downloads/client_secret_XXX.json

Paste the printed GMAIL_* values into the Cloud Run env (see CLAUDE.md deploy).
"""

import argparse
import json
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

# gmail.compose covers exactly what the worker does: create drafts
# (users.drafts.create) and send them (users.drafts.send). Nothing broader.
SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--client-secret",
        required=True,
        help="Path to the OAuth client JSON downloaded from the GCP console.",
    )
    args = parser.parse_args()

    flow = InstalledAppFlow.from_client_secrets_file(args.client_secret, scopes=SCOPES)
    # access_type=offline + prompt=consent are what force Google to return a
    # refresh_token (not just a short-lived access token).
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

    if not creds.refresh_token:
        raise SystemExit(
            "No refresh token returned. Re-run and ensure you consent freshly "
            "(revoke prior access at https://myaccount.google.com/permissions)."
        )

    with Path(args.client_secret).open() as fh:
        conf = json.load(fh)
    conf = conf.get("installed") or conf.get("web") or {}

    print("\n" + "=" * 60)
    print("Set these on Cloud Run (gcloud run services update ... \\")
    print("  --update-env-vars KEY=VALUE,...):")
    print("=" * 60)
    print(f"GMAIL_CLIENT_ID={conf.get('client_id', '<from client JSON>')}")
    print(f"GMAIL_CLIENT_SECRET={conf.get('client_secret', '<from client JSON>')}")
    print(f"GMAIL_REFRESH_TOKEN={creds.refresh_token}")
    print("GMAIL_SENDER_EMAIL=<the mailbox you just authorized>")
    print("=" * 60)


if __name__ == "__main__":
    main()
