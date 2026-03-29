#!/usr/bin/env python3
"""
Publish generated Lua modules to wiki module pages through the MediaWiki API.

Usage:
    python scripts/publish_wiki_modules.py \
        --summary "Automated update from https://github.com/IcarusWiki/TreeUI <sha>" \
        --map "generated/TalentData.lua=Module:TalentTree/TalentData"

Environment:
    WIKIGG_USERNAME
    WIKIGG_APP_PASSWORD
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import socket
import sys
import urllib.parse
import urllib.error
import urllib.request
from pathlib import Path
from typing import NoReturn

DEFAULT_API_URL = "https://icarus.wiki.gg/api.php"
USERNAME_ENV = "WIKIGG_USERNAME"
PASSWORD_ENV = "WIKIGG_APP_PASSWORD"
USER_AGENT = "TreeUIWikiPublisher/1.0 (GitHub Actions; github.com/IcarusWiki/TreeUI)"
REQUEST_TIMEOUT_SECONDS = 30


def fail(message: str) -> NoReturn:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Publish generated Lua modules to configured MediaWiki pages."
    )
    parser.add_argument(
        "--api-url",
        default=DEFAULT_API_URL,
        help=f"MediaWiki API URL (default: {DEFAULT_API_URL})",
    )
    parser.add_argument(
        "--summary",
        required=True,
        help="Edit summary to use for all published pages.",
    )
    parser.add_argument(
        "--map",
        dest="mappings",
        action="append",
        required=True,
        metavar="LOCAL_PATH=PAGE_TITLE",
        help="Map a local Lua file to its target wiki page title.",
    )
    return parser.parse_args()


def parse_mapping(text: str) -> tuple[Path, str]:
    local_path_text, separator, page_title = text.partition("=")
    if not separator or not local_path_text.strip() or not page_title.strip():
        fail(f"Invalid --map value {text!r}; expected LOCAL_PATH=PAGE_TITLE")

    local_path = Path(local_path_text).expanduser()
    if not local_path.is_file():
        fail(f"Mapped file does not exist: {local_path}")

    return local_path, page_title.strip()


def normalize_text(text: str) -> str:
    return text.replace("\r\n", "\n")


class MediaWikiClient:
    def __init__(self, api_url: str, username: str, password: str) -> None:
        self.api_url = api_url
        self.username = username
        self.password = password
        cookie_jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(cookie_jar)
        )
        self.csrf_token = ""

    def request(self, params: dict[str, str], *, post: bool) -> dict:
        request_headers = {
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        payload = dict(params)
        payload["format"] = "json"

        if post:
            request = urllib.request.Request(
                self.api_url,
                data=urllib.parse.urlencode(payload).encode("utf-8"),
                headers=request_headers,
                method="POST",
            )
        else:
            query = urllib.parse.urlencode(payload)
            request = urllib.request.Request(
                f"{self.api_url}?{query}",
                headers=request_headers,
                method="GET",
            )

        try:
            with self.opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                body = response.read().decode("utf-8")
        except urllib.error.URLError as exc:
            fail(f"Failed to reach MediaWiki API: {exc}")
        except socket.timeout:
            fail(
                "Timed out while waiting for the MediaWiki API "
                f"after {REQUEST_TIMEOUT_SECONDS} seconds."
            )

        data = json.loads(body)
        if "error" in data:
            error = data["error"]
            code = error.get("code", "unknown")
            info = error.get("info", "Unknown API error")
            fail(f"MediaWiki API error ({code}): {info}")
        return data

    def login(self) -> None:
        token_data = self.request(
            {
                "action": "query",
                "meta": "tokens",
                "type": "login",
            },
            post=False,
        )
        login_token = token_data["query"]["tokens"]["logintoken"]

        login_data = self.request(
            {
                "action": "login",
                "lgname": self.username,
                "lgpassword": self.password,
                "lgtoken": login_token,
            },
            post=True,
        )
        login_result = login_data.get("login", {})
        if login_result.get("result") != "Success":
            fail(f"Wiki login failed: {login_result}")

        csrf_data = self.request(
            {
                "action": "query",
                "meta": "tokens",
                "type": "csrf",
            },
            post=False,
        )
        self.csrf_token = csrf_data["query"]["tokens"]["csrftoken"]
        if not self.csrf_token:
            fail("Failed to obtain CSRF token after login.")

    def fetch_page_text(self, page_title: str) -> str:
        page_data = self.request(
            {
                "action": "query",
                "prop": "revisions",
                "titles": page_title,
                "rvprop": "content",
                "rvslots": "main",
                "formatversion": "2",
            },
            post=False,
        )
        pages = page_data["query"]["pages"]
        if not pages:
            fail(f"Wiki returned no page results for {page_title!r}")

        page = pages[0]
        if page.get("missing"):
            fail(f"Configured wiki page does not exist: {page_title}")

        revisions = page.get("revisions", [])
        if not revisions:
            return ""

        slot = revisions[0].get("slots", {}).get("main", {})
        return normalize_text(slot.get("content", ""))

    def edit_page(self, page_title: str, text: str, summary: str) -> None:
        edit_data = self.request(
            {
                "action": "edit",
                "title": page_title,
                "text": text,
                "summary": summary,
                "nocreate": "1",
                "token": self.csrf_token,
            },
            post=True,
        )
        edit_result = edit_data.get("edit", {})
        if edit_result.get("result") != "Success":
            fail(f"Edit failed for {page_title}: {edit_result}")


def main() -> None:
    args = parse_args()

    username = os.environ.get(USERNAME_ENV, "").strip()
    password = os.environ.get(PASSWORD_ENV, "").strip()
    if not username:
        fail(f"Missing required environment variable: {USERNAME_ENV}")
    if not password:
        fail(f"Missing required environment variable: {PASSWORD_ENV}")

    mappings = [parse_mapping(item) for item in args.mappings]

    client = MediaWikiClient(args.api_url, username, password)
    print(f"Logging into wiki API at {args.api_url} as {username}")
    client.login()

    updated_count = 0
    skipped_count = 0

    for local_path, page_title in mappings:
        print(f"Checking {local_path} -> {page_title}")
        local_text = normalize_text(local_path.read_text(encoding="utf-8"))
        remote_text = client.fetch_page_text(page_title)

        if local_text == remote_text:
            skipped_count += 1
            print("  Unchanged on wiki; skipping edit")
            continue

        client.edit_page(page_title, local_text, args.summary)
        updated_count += 1
        print("  Updated wiki page")

    print(
        f"Finished publishing wiki modules: {updated_count} updated, "
        f"{skipped_count} unchanged"
    )


if __name__ == "__main__":
    main()
