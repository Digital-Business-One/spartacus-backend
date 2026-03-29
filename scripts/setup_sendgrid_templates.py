#!/usr/bin/env python3
"""Create or update SendGrid Dynamic Templates from local HTML files.

Reads templates from docs/templates/email/ and creates them in SendGrid.
Idempotent: updates existing templates if they already exist.

Usage:
    SENDGRID_TEMPLATES_API_KEY=SG.xxx uv run python scripts/setup_sendgrid_templates.py

The script outputs template IDs to be used in rules.py.
"""

import json
import os
import sys
from pathlib import Path
from urllib.request import Request, urlopen

API_KEY = os.environ.get("SENDGRID_TEMPLATES_API_KEY", "")
BASE_URL = "https://api.sendgrid.com/v3"
TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "docs" / "templates" / "email"

TEMPLATES = [
    {
        "name": "Spartacus — Cadastro e Boas-vindas",
        "file": "signup_welcome.html",
        "subject": "{{subject}}",
    },
    {
        "name": "Spartacus — Notificação de Conta",
        "file": "account_notification.html",
        "subject": "{{subject}}",
    },
]


def _request(method: str, path: str, body: dict | None = None) -> dict:
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode() if body else None
    req = Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {API_KEY}")
    req.add_header("Content-Type", "application/json")
    with urlopen(req) as resp:
        return json.loads(resp.read()) if resp.status != 204 else {}


def _find_existing_templates() -> dict[str, str]:
    """Return {name: template_id} for existing templates."""
    result = _request("GET", "/templates?generations=dynamic&page_size=100")
    return {
        t["name"]: t["id"]
        for t in result.get("result", result.get("templates", []))
    }


def run() -> None:
    if not API_KEY:
        print("ERROR: Set SENDGRID_TEMPLATES_API_KEY env var")
        sys.exit(1)

    existing = _find_existing_templates()
    print(f"Found {len(existing)} existing template(s)\n")

    for tmpl in TEMPLATES:
        html_path = TEMPLATES_DIR / tmpl["file"]
        if not html_path.exists():
            print(f"SKIP: {tmpl['file']} not found at {html_path}")
            continue

        html = html_path.read_text(encoding="utf-8")
        name = tmpl["name"]

        # Create or find template
        if name in existing:
            template_id = existing[name]
            print(f"UPDATE: {name} → {template_id}")
        else:
            resp = _request("POST", "/templates", {
                "name": name,
                "generation": "dynamic",
            })
            template_id = resp["id"]
            print(f"CREATE: {name} → {template_id}")

        # Create new version with the HTML content
        _request("POST", f"/templates/{template_id}/versions", {
            "name": f"v{len(TEMPLATES)}",
            "subject": tmpl["subject"],
            "html_content": html,
            "active": 1,
            "editor": "code",
        })
        print(f"  Version created (active)")

    print("\nDone. Update rules.py with the template IDs above.")


if __name__ == "__main__":
    run()
