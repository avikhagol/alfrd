"""Release-only OAuth checks. Never print credential values or raw parse errors."""
import json
import os
from pathlib import Path
import re
import sys
import zipfile


NAMES = {
    "client_id": "GSHEET_OAUTH_CLIENT_ID",
    "client_secret": "GSHEET_OAUTH_CLIENT_SECRET",
    "picker_api_key": "GSHEET_PICKER_API_KEY",
    "project_number": "GSHEET_GCP_PROJECT_NUMBER",
}
PATTERNS = {
    "client_id": r"[A-Za-z0-9_-]+\.apps\.googleusercontent\.com",
    "client_secret": r"[^\s]{1,256}",
    "picker_api_key": r"[A-Za-z0-9_-]{30,64}",
    "project_number": r"[0-9]{6,20}",
}
MEMBER = "alfrd_gsheet/oauth_client.json"


def validate(values):
    if not isinstance(values, dict):
        raise ValueError("OAuth defaults must be an object")
    for key, pattern in PATTERNS.items():
        value = values.get(key, "")
        if not isinstance(value, str) or (not value and key in ("client_id", "client_secret")) or (value and not re.fullmatch(pattern, value)):
            raise ValueError(f"Missing or invalid {NAMES[key]} ({key})")


def fill(path, environ):
    values = {key: environ.get(name, "").strip() for key, name in NAMES.items()}
    validate(values)
    Path(path).write_text(json.dumps(values), encoding="utf-8")


def check(directory):
    wheels = list(Path(directory).glob("alfrd_gsheet-*.whl"))
    if not wheels:
        raise ValueError("No alfrd-gsheet wheel found")
    for wheel in wheels:
        try:
            with zipfile.ZipFile(wheel) as archive:
                values = json.loads(archive.read(MEMBER))
        except (OSError, KeyError, ValueError, UnicodeError, zipfile.BadZipFile):
            raise ValueError("Cannot read OAuth defaults from alfrd-gsheet wheel") from None
        validate(values)


def main():
    try:
        if sys.argv[1:] == ["fill"]:
            fill(Path(__file__).resolve().parents[1] / "examples/plugins/alfrd-gsheet" / MEMBER, os.environ)
        elif len(sys.argv) == 3 and sys.argv[1] == "check":
            check(sys.argv[2])
        else:
            raise ValueError("Usage: gsheet_release.py fill | check DIST_DIRECTORY")
    except (ValueError, OSError):
        # Only validate() constructs printable errors; all other failures are generic.
        print("Google Sheet OAuth release validation failed. Check required client ID/secret and optional Picker configuration.", file=sys.stderr)
        return 1
    print("Google Sheet OAuth release validation passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
