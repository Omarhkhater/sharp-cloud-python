"""Standalone Sharp Cloud login test."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from sharp_cloud import SharpCloud


def extract_code(value: str) -> str:
    value = value.strip()

    if value.startswith("http://") or value.startswith("https://"):
        parsed = urlparse(value)
        query = parse_qs(parsed.query)
        code = query.get("code", [None])[0]
        if not code:
            raise ValueError("No code parameter found in the URL.")
        return code

    return value


def main() -> None:
    client = SharpCloud()

    print("=" * 60)
    print("Sharp COCORO AIR Login Test")
    print("=" * 60)

    url, _state = client.auth.build_authorization_url()

    print("\nOpen the following URL:\n")
    print(url)

    print("\nAfter login, paste the full redirect URL or just the code.\n")

    raw_value = input("Authorization response: ").strip()
    code = extract_code(raw_value)

    token = client.auth.exchange_code(code)

    print("\nAuthentication successful\n")
    print(f"Access Token : {token.access_token[:40]}...")
    print(f"Refresh Token: {token.refresh_token[:40]}...")
    print(f"ID Token     : {token.id_token[:40]}...")
    print(f"Expires      : {token.expires_at}")


if __name__ == "__main__":
    main()