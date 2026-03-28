#!/usr/bin/env python3
"""
Snapshot Cargo schema and selected category membership from the live wiki.

This script is meant to support archetype discovery without scraping page HTML.
It uses MediaWiki's API plus Cargo's API endpoints to gather:
- the Cargo table list
- fields for each Cargo table
- a small sample of rows per table
- members of selected categories

The defaults are intentionally conservative:
- requests are serialized
- a delay is added between requests
- retries back off on transient failures
- output can be resumed to avoid re-fetching unchanged files
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
DEFAULT_OUT_DIR = ROOT / "generated" / "wiki_schema"
DEFAULT_API_URL = "https://icarus.wiki.gg/api.php"
DEFAULT_USER_AGENT = "IcarusWikiWorkshop/1.0 (schema discovery; local tooling)"
DEFAULT_SLEEP_SECONDS = 1.0
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_RETRIES = 4
DEFAULT_SAMPLE_LIMIT = 10
DEFAULT_MAX_EMBEDDEDIN_RESULTS = 500
DEFAULT_CATEGORIES = [
    "Category:Pages declaring Cargo tables",
    "Category:Templates using Cargo",
]
DEFAULT_DECLARING_CARGO_CATEGORY = "Category:Pages declaring Cargo tables"
DEFAULT_TEMPLATES_USING_CARGO_CATEGORY = "Category:Templates using Cargo"
TRANSIENT_HTTP_STATUS = {429, 500, 502, 503, 504}
CARGO_TEMPLATE_SUFFIXES = ("/CargoDeclare", "/CargoStore")
KNOWN_HELPER_TEMPLATES = {
    "Template:Cargo",
    "Template:ChangelogInfoHeader",
}
CARGO_PARSER_FUNCTION_RE = re.compile(
    r"#(cargo_declare|cargo_store|cargo_attach|cargo_query|cargo_compound_query)\s*:",
    re.IGNORECASE,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Discover Cargo tables, fields, sample rows, and selected category members from a MediaWiki site."
    )
    parser.add_argument(
        "--api-url",
        default=DEFAULT_API_URL,
        help=f"MediaWiki API endpoint. Default: {DEFAULT_API_URL}",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=f"Output directory for schema snapshots. Default: {DEFAULT_OUT_DIR}",
    )
    parser.add_argument(
        "--user-agent",
        default=DEFAULT_USER_AGENT,
        help=f"User-Agent header sent with requests. Default: {DEFAULT_USER_AGENT}",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=DEFAULT_SLEEP_SECONDS,
        help=f"Delay between API requests in seconds. Default: {DEFAULT_SLEEP_SECONDS}",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Per-request timeout in seconds. Default: {DEFAULT_TIMEOUT_SECONDS}",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=DEFAULT_MAX_RETRIES,
        help=f"Maximum request attempts for transient failures. Default: {DEFAULT_MAX_RETRIES}",
    )
    parser.add_argument(
        "--sample-limit",
        type=int,
        default=DEFAULT_SAMPLE_LIMIT,
        help=f"Number of sample rows to fetch per Cargo table. Default: {DEFAULT_SAMPLE_LIMIT}",
    )
    parser.add_argument(
        "--sample-fields",
        nargs="+",
        default=["_pageName"],
        help="Cargo fields to request for table samples. Default: _pageName",
    )
    parser.add_argument(
        "--table",
        action="append",
        dest="tables",
        help="Optional Cargo table name to limit discovery. Repeatable.",
    )
    parser.add_argument(
        "--category",
        action="append",
        dest="categories",
        help="Additional category title to fetch members for. Repeatable.",
    )
    parser.add_argument(
        "--no-default-categories",
        action="store_true",
        help="Do not fetch the built-in Cargo-related categories.",
    )
    parser.add_argument(
        "--category-namespace",
        type=int,
        action="append",
        dest="category_namespaces",
        help="Optional namespace filter for categorymembers. Repeatable.",
    )
    parser.add_argument(
        "--skip-samples",
        action="store_true",
        help="Fetch Cargo tables and fields but skip cargoquery sample rows.",
    )
    parser.add_argument(
        "--skip-categories",
        action="store_true",
        help="Skip categorymembers discovery.",
    )
    parser.add_argument(
        "--inspect-templates",
        action="store_true",
        help="Fetch source and transclusion metadata for top-level Cargo-related templates.",
    )
    parser.add_argument(
        "--template",
        action="append",
        dest="template_titles",
        help="Template title to inspect explicitly. Repeatable.",
    )
    parser.add_argument(
        "--embeddedin-namespace",
        type=int,
        action="append",
        dest="embeddedin_namespaces",
        help="Optional namespace filter for embeddedin results when inspecting templates. Repeatable.",
    )
    parser.add_argument(
        "--max-embeddedin-results",
        type=int,
        default=DEFAULT_MAX_EMBEDDEDIN_RESULTS,
        help=f"Maximum embeddedin rows to fetch per template inspection. Default: {DEFAULT_MAX_EMBEDDEDIN_RESULTS}",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse existing per-table and per-category files when present.",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print a summary without writing JSON files.",
    )
    return parser.parse_args()


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def safe_filename(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in "._-" else "_" for char in str(value or ""))
    cleaned = cleaned.strip("._")
    return cleaned or "snapshot"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sorted_unique(values: list[str]) -> list[str]:
    return sorted({value for value in values if value})


def payload_items(payload: dict[str, Any], key: str) -> list[Any]:
    raw = payload.get(key)
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        if "results" in raw and isinstance(raw["results"], list):
            return raw["results"]
        return list(raw.values())
    return []


def table_name_from_entry(entry: Any) -> str:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        for key in ["name", "table", "_table", "tableName"]:
            value = entry.get(key)
            if isinstance(value, str) and value:
                return value
        if len(entry) == 1:
            only_value = next(iter(entry.values()))
            if isinstance(only_value, str):
                return only_value
    return ""


def field_name_from_entry(entry: Any) -> str:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        for key in ["name", "field", "_fieldName"]:
            value = entry.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


def extract_sample_page_names(rows: list[Any]) -> list[str]:
    names: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if isinstance(row.get("title"), dict):
            title_block = row["title"]
            page_name = title_block.get("_pageName")
            if isinstance(page_name, str) and page_name:
                names.append(page_name)
                continue
        for key in ["_pageName", "title", "page", "page_name"]:
            value = row.get(key)
            if isinstance(value, str) and value:
                names.append(value)
                break
    return sorted_unique(names)


def strip_cargo_template_suffix(title: str) -> tuple[str, str]:
    for suffix in CARGO_TEMPLATE_SUFFIXES:
        if title.endswith(suffix):
            return title[: -len(suffix)], suffix.lstrip("/")
    return title, ""


def build_cargo_template_family_summary(category_snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    snapshot_by_title = {
        snapshot.get("category_title", ""): snapshot
        for snapshot in category_snapshots
        if snapshot.get("category_title")
    }
    declaring_titles = sorted_unique(
        snapshot_by_title.get(DEFAULT_DECLARING_CARGO_CATEGORY, {}).get("member_titles", [])
    )
    using_titles = sorted_unique(
        snapshot_by_title.get(DEFAULT_TEMPLATES_USING_CARGO_CATEGORY, {}).get("member_titles", [])
    )

    family_map: dict[str, dict[str, Any]] = {}
    for title in sorted_unique(declaring_titles + using_titles):
        family_name, cargo_role = strip_cargo_template_suffix(title)
        family = family_map.setdefault(
            family_name,
            {
                "family": family_name,
                "declare_pages": [],
                "store_pages": [],
                "other_pages": [],
                "category_memberships": [],
            },
        )
        if title in declaring_titles and DEFAULT_DECLARING_CARGO_CATEGORY not in family["category_memberships"]:
            family["category_memberships"].append(DEFAULT_DECLARING_CARGO_CATEGORY)
        if title in using_titles and DEFAULT_TEMPLATES_USING_CARGO_CATEGORY not in family["category_memberships"]:
            family["category_memberships"].append(DEFAULT_TEMPLATES_USING_CARGO_CATEGORY)

        if cargo_role == "CargoDeclare":
            family["declare_pages"].append(title)
        elif cargo_role == "CargoStore":
            family["store_pages"].append(title)
        else:
            family["other_pages"].append(title)

    families = []
    candidate_page_templates = []
    component_families = []

    for family_name in sorted(family_map, key=str.casefold):
        family = family_map[family_name]
        family["declare_pages"] = sorted_unique(family["declare_pages"])
        family["store_pages"] = sorted_unique(family["store_pages"])
        family["other_pages"] = sorted_unique(family["other_pages"])
        family["category_memberships"] = sorted_unique(family["category_memberships"])
        family["has_component_templates"] = bool(family["declare_pages"] or family["store_pages"])
        family["has_top_level_template"] = bool(family["other_pages"])
        families.append(family)

        if family["has_component_templates"]:
            component_families.append(family_name)

        if (
            family["has_top_level_template"]
            and family_name not in KNOWN_HELPER_TEMPLATES
            and not family["declare_pages"]
            and not family["store_pages"]
        ):
            candidate_page_templates.extend(family["other_pages"])

    return {
        "fetched_at": iso_now(),
        "declaring_category": DEFAULT_DECLARING_CARGO_CATEGORY,
        "using_category": DEFAULT_TEMPLATES_USING_CARGO_CATEGORY,
        "declaring_templates": declaring_titles,
        "templates_using_cargo": using_titles,
        "component_family_count": len(component_families),
        "component_families": component_families,
        "candidate_page_templates": sorted_unique(candidate_page_templates),
        "families": families,
        "notes": [
            "CargoDeclare and CargoStore pages are usually data-component templates, not page archetypes by themselves.",
            "Top-level templates using Cargo are stronger archetype candidates than raw CargoDeclare/CargoStore subtemplates.",
            "Component families can still be used as composable traits when defining archetypes.",
        ],
    }


def load_existing_cargo_family_summary(out_dir: Path) -> dict[str, Any]:
    path = out_dir / "cargo_template_families.json"
    if path.exists():
        payload = read_json(path)
        if isinstance(payload, dict):
            return payload
    return {}


def cargo_calls_from_wikitext(wikitext: str) -> list[str]:
    return sorted_unique([match.group(1).lower() for match in CARGO_PARSER_FUNCTION_RE.finditer(wikitext or "")])


def first_n_lines(text: str, line_count: int = 20) -> str:
    if not text:
        return ""
    return "\n".join(text.splitlines()[:line_count]).strip()


class WikiApiClient:
    def __init__(
        self,
        api_url: str,
        user_agent: str,
        sleep_seconds: float,
        timeout_seconds: float,
        max_retries: int,
    ) -> None:
        self.api_url = api_url
        self.user_agent = user_agent
        self.sleep_seconds = max(sleep_seconds, 0.0)
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(max_retries, 1)
        self._last_request_monotonic = 0.0

    def _respect_rate_limit(self) -> None:
        if self.sleep_seconds <= 0:
            return
        elapsed = time.monotonic() - self._last_request_monotonic
        remaining = self.sleep_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)

    def get(self, params: dict[str, Any]) -> dict[str, Any]:
        encoded = urllib.parse.urlencode(
            {key: value for key, value in params.items() if value is not None and value != ""},
            doseq=True,
        )
        url = f"{self.api_url}?{encoded}"
        attempt = 0

        while True:
            attempt += 1
            self._respect_rate_limit()
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": self.user_agent,
                    "Accept": "application/json",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self._last_request_monotonic = time.monotonic()
            except urllib.error.HTTPError as exc:
                self._last_request_monotonic = time.monotonic()
                if exc.code in TRANSIENT_HTTP_STATUS and attempt < self.max_retries:
                    retry_after = exc.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after and retry_after.isdigit() else self.sleep_seconds * (2 ** attempt)
                    time.sleep(max(delay, self.sleep_seconds))
                    continue
                raise
            except urllib.error.URLError:
                self._last_request_monotonic = time.monotonic()
                if attempt < self.max_retries:
                    time.sleep(max(self.sleep_seconds * (2 ** attempt), self.sleep_seconds))
                    continue
                raise

            if "error" in payload:
                raise RuntimeError(f"API error for {url}: {payload['error']}")
            return payload

    def get_category_members(self, category_title: str, namespaces: list[int] | None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": category_title,
            "cmlimit": "500",
            "format": "json",
            "formatversion": "2",
        }
        if namespaces:
            params["cmnamespace"] = "|".join(str(namespace) for namespace in namespaces)

        members: list[dict[str, Any]] = []
        while True:
            payload = self.get(params)
            members.extend(payload.get("query", {}).get("categorymembers", []))
            continuation = payload.get("continue", {})
            cmcontinue = continuation.get("cmcontinue")
            if not cmcontinue:
                break
            params["cmcontinue"] = cmcontinue
        return members

    def get_embeddedin(self, title: str, namespaces: list[int] | None, max_results: int) -> dict[str, Any]:
        params: dict[str, Any] = {
            "action": "query",
            "list": "embeddedin",
            "eititle": title,
            "eilimit": "500",
            "format": "json",
            "formatversion": "2",
        }
        if namespaces:
            params["einamespace"] = "|".join(str(namespace) for namespace in namespaces)

        members: list[dict[str, Any]] = []
        truncated = False

        while True:
            payload = self.get(params)
            batch = payload.get("query", {}).get("embeddedin", [])
            remaining = max_results - len(members)
            if remaining <= 0:
                truncated = True
                break
            members.extend(batch[:remaining])
            if len(batch) > remaining:
                truncated = True
                break

            continuation = payload.get("continue", {})
            eicontinue = continuation.get("eicontinue")
            if not eicontinue:
                break
            params["eicontinue"] = eicontinue

        namespace_counts: dict[str, int] = {}
        for member in members:
            if not isinstance(member, dict):
                continue
            namespace_key = str(member.get("ns", ""))
            namespace_counts[namespace_key] = namespace_counts.get(namespace_key, 0) + 1

        return {
            "count_fetched": len(members),
            "truncated": truncated,
            "namespaces": namespaces or [],
            "namespace_counts": namespace_counts,
            "members": members,
            "member_titles": sorted_unique(
                [member.get("title", "") for member in members if isinstance(member, dict)]
            ),
        }


def fetch_cargo_tables(client: WikiApiClient) -> list[dict[str, Any]]:
    payload = client.get(
        {
            "action": "cargotables",
            "format": "json",
        }
    )
    raw_tables = payload.get("cargotables")
    normalized: list[dict[str, Any]] = []
    if isinstance(raw_tables, dict):
        for table_name, entry in raw_tables.items():
            if isinstance(entry, dict):
                normalized.append({"name": table_name, "raw": entry})
            elif isinstance(entry, str):
                normalized.append({"name": entry, "raw": {"name": entry}})
            else:
                normalized.append({"name": table_name, "raw": {"value": entry}})
    else:
        tables = payload_items(payload, "cargotables")
        for entry in tables:
            if isinstance(entry, str):
                normalized.append({"name": entry, "raw": {"name": entry}})
                continue
            if isinstance(entry, dict):
                name = table_name_from_entry(entry)
                normalized.append({"name": name, "raw": entry})
    normalized = [entry for entry in normalized if entry.get("name")]
    return sorted(normalized, key=lambda entry: entry["name"].casefold())


def fetch_cargo_fields(client: WikiApiClient, table: str) -> tuple[list[dict[str, Any]], list[str]]:
    payload = client.get(
        {
            "action": "cargofields",
            "table": table,
            "format": "json",
        }
    )
    normalized_fields: list[dict[str, Any]] = []
    field_names: list[str] = []
    raw_fields = payload.get("cargofields")
    if isinstance(raw_fields, dict):
        for field_name, entry in raw_fields.items():
            row = {
                "name": field_name,
                "raw": entry if isinstance(entry, dict) else {"value": entry},
            }
            normalized_fields.append(row)
            field_names.append(field_name)
    else:
        fields = payload_items(payload, "cargofields")
        for entry in fields:
            if isinstance(entry, str):
                normalized_fields.append({"name": entry, "raw": {"name": entry}})
                field_names.append(entry)
                continue
            if isinstance(entry, dict):
                name = field_name_from_entry(entry)
                row = {"name": name, "raw": entry}
                normalized_fields.append(row)
                if name:
                    field_names.append(name)
    return normalized_fields, sorted_unique(field_names)


def fetch_cargo_sample_rows(
    client: WikiApiClient,
    table: str,
    sample_fields: list[str],
    sample_limit: int,
) -> list[Any]:
    payload = client.get(
        {
            "action": "cargoquery",
            "tables": table,
            "fields": ",".join(sample_fields),
            "limit": str(sample_limit),
            "format": "json",
        }
    )
    return payload_items(payload, "cargoquery")


def fetch_page_content(client: WikiApiClient, title: str) -> dict[str, Any]:
    payload = client.get(
        {
            "action": "query",
            "titles": title,
            "prop": "info|revisions",
            "rvprop": "ids|timestamp|content",
            "rvslots": "main",
            "format": "json",
            "formatversion": "2",
        }
    )
    pages = payload.get("query", {}).get("pages", [])
    page = pages[0] if pages else {}
    revision = (page.get("revisions") or [{}])[0]
    slot = revision.get("slots", {}).get("main", {})
    content = slot.get("content")
    if content is None:
        content = revision.get("content", "")
    return {
        "title": page.get("title", title),
        "pageid": page.get("pageid"),
        "ns": page.get("ns"),
        "lastrevid": page.get("lastrevid"),
        "touched": page.get("touched"),
        "missing": bool(page.get("missing")),
        "revision": {
            "revid": revision.get("revid"),
            "parentid": revision.get("parentid"),
            "timestamp": revision.get("timestamp"),
        },
        "wikitext": content or "",
    }


def fetch_page_relations(client: WikiApiClient, title: str) -> dict[str, Any]:
    params: dict[str, Any] = {
        "action": "query",
        "titles": title,
        "prop": "templates|categories",
        "tllimit": "500",
        "cllimit": "500",
        "format": "json",
        "formatversion": "2",
    }
    templates: list[dict[str, Any]] = []
    categories: list[dict[str, Any]] = []

    while True:
        payload = client.get(params)
        pages = payload.get("query", {}).get("pages", [])
        page = pages[0] if pages else {}
        templates.extend(page.get("templates", []))
        categories.extend(page.get("categories", []))

        continuation = payload.get("continue", {})
        next_values = {key: continuation[key] for key in ["continue", "tlcontinue", "clcontinue"] if key in continuation}
        if not next_values:
            break
        params.update(next_values)

    return {
        "templates": templates,
        "template_titles": sorted_unique(
            [entry.get("title", "") for entry in templates if isinstance(entry, dict)]
        ),
        "categories": categories,
        "category_titles": sorted_unique(
            [entry.get("title", "") for entry in categories if isinstance(entry, dict)]
        ),
    }


def select_template_titles_for_inspection(
    cargo_family_summary: dict[str, Any],
    explicit_titles: list[str] | None,
) -> list[str]:
    if explicit_titles:
        return sorted_unique(explicit_titles)

    titles: list[str] = []
    for family in cargo_family_summary.get("families", []):
        if not isinstance(family, dict):
            continue
        if not family.get("has_top_level_template"):
            continue
        for title in family.get("other_pages", []):
            if title and title not in KNOWN_HELPER_TEMPLATES:
                titles.append(title)
    return sorted_unique(titles)


def template_snapshot(
    client: WikiApiClient,
    title: str,
    cargo_family_summary: dict[str, Any],
    embeddedin_namespaces: list[int] | None,
    max_embeddedin_results: int,
) -> dict[str, Any]:
    family_map = {
        family.get("family", ""): family
        for family in cargo_family_summary.get("families", [])
        if isinstance(family, dict) and family.get("family")
    }
    page_content = fetch_page_content(client, title)
    page_relations = fetch_page_relations(client, title)
    embeddedin = client.get_embeddedin(title, embeddedin_namespaces, max_embeddedin_results)

    wikitext = page_content.get("wikitext", "")
    cargo_calls = cargo_calls_from_wikitext(wikitext)
    direct_cargo_component_templates = []
    referenced_family_names = []
    for template_title in page_relations["template_titles"]:
        family_name, cargo_role = strip_cargo_template_suffix(template_title)
        if cargo_role:
            direct_cargo_component_templates.append(template_title)
        if family_name in family_map:
            referenced_family_names.append(family_name)

    namespace_counts = embeddedin.get("namespace_counts", {})
    top_level_family = family_map.get(title, {})
    family_name, _ = strip_cargo_template_suffix(title)
    family_entry = family_map.get(family_name, top_level_family)
    mainspace_transclusions = namespace_counts.get("0", 0)
    template_transclusions = namespace_counts.get("10", 0)
    likely_page_archetype_anchor = bool(
        mainspace_transclusions
        and (
            cargo_calls
            or direct_cargo_component_templates
            or family_entry.get("has_component_templates")
        )
    )

    return {
        "title": title,
        "family": family_entry.get("family", title),
        "fetched_at": iso_now(),
        "page": {
            "title": page_content.get("title", title),
            "pageid": page_content.get("pageid"),
            "ns": page_content.get("ns"),
            "lastrevid": page_content.get("lastrevid"),
            "touched": page_content.get("touched"),
            "missing": page_content.get("missing", False),
            "revision": page_content.get("revision", {}),
        },
        "analysis": {
            "cargo_calls": cargo_calls,
            "uses_cargo_declare": "cargo_declare" in cargo_calls,
            "uses_cargo_store": "cargo_store" in cargo_calls,
            "uses_cargo_attach": "cargo_attach" in cargo_calls,
            "uses_cargo_query": any(
                call in {"cargo_query", "cargo_compound_query"}
                for call in cargo_calls
            ),
            "direct_cargo_component_templates": sorted_unique(direct_cargo_component_templates),
            "referenced_cargo_families": sorted_unique(referenced_family_names),
            "has_component_family": bool(family_entry.get("has_component_templates")),
            "has_top_level_family_template": bool(family_entry.get("has_top_level_template")),
            "mainspace_transclusions": mainspace_transclusions,
            "template_transclusions": template_transclusions,
            "likely_page_archetype_anchor": likely_page_archetype_anchor,
            "likely_helper_template": bool(not mainspace_transclusions and template_transclusions),
        },
        "relations": page_relations,
        "embeddedin": embeddedin,
        "wikitext_preview": first_n_lines(wikitext, line_count=24),
        "wikitext": wikitext,
    }


def build_template_inspection_summary(
    template_snapshots: list[dict[str, Any]],
) -> dict[str, Any]:
    anchors = []
    helpers = []
    summaries = []

    for snapshot in sorted(template_snapshots, key=lambda entry: entry.get("title", "").casefold()):
        analysis = snapshot.get("analysis", {})
        summary_entry = {
            "title": snapshot.get("title", ""),
            "family": snapshot.get("family", ""),
            "cargo_calls": analysis.get("cargo_calls", []),
            "direct_cargo_component_templates": analysis.get("direct_cargo_component_templates", []),
            "referenced_cargo_families": analysis.get("referenced_cargo_families", []),
            "mainspace_transclusions": analysis.get("mainspace_transclusions", 0),
            "template_transclusions": analysis.get("template_transclusions", 0),
            "likely_page_archetype_anchor": analysis.get("likely_page_archetype_anchor", False),
            "likely_helper_template": analysis.get("likely_helper_template", False),
        }
        summaries.append(summary_entry)
        if summary_entry["likely_page_archetype_anchor"]:
            anchors.append(summary_entry["title"])
        if summary_entry["likely_helper_template"]:
            helpers.append(summary_entry["title"])

    return {
        "fetched_at": iso_now(),
        "template_count": len(template_snapshots),
        "likely_page_archetype_anchors": anchors,
        "likely_helper_templates": helpers,
        "templates": summaries,
        "notes": [
            "A likely page archetype anchor is a top-level template with main-namespace transclusions and Cargo behavior or Cargo component dependencies.",
            "A likely helper template currently means it has template-namespace transclusions but no observed main-namespace transclusions in the fetched embeddedin sample.",
        ],
    }


def category_snapshot(
    client: WikiApiClient,
    category_title: str,
    namespaces: list[int] | None,
) -> dict[str, Any]:
    members = client.get_category_members(category_title, namespaces)
    member_titles = sorted_unique(
        [member.get("title", "") for member in members if isinstance(member, dict)]
    )
    return {
        "category_title": category_title,
        "member_count": len(members),
        "members": members,
        "member_titles": member_titles,
        "namespaces": namespaces or [],
        "fetched_at": iso_now(),
    }


def table_snapshot(
    client: WikiApiClient,
    table_entry: dict[str, Any],
    sample_fields: list[str],
    sample_limit: int,
    skip_samples: bool,
) -> dict[str, Any]:
    table_name = table_entry["name"]
    fields, field_names = fetch_cargo_fields(client, table_name)
    sample_rows: list[Any] = []
    sample_page_names: list[str] = []
    if not skip_samples and sample_limit > 0:
        sample_rows = fetch_cargo_sample_rows(client, table_name, sample_fields, sample_limit)
        sample_page_names = extract_sample_page_names(sample_rows)

    return {
        "table": table_name,
        "table_entry": table_entry.get("raw", {"name": table_name}),
        "fields": fields,
        "field_names": field_names,
        "sample_query": {
            "fields": sample_fields,
            "limit": sample_limit,
        },
        "sample_rows": sample_rows,
        "sample_page_names": sample_page_names,
        "fetched_at": iso_now(),
    }


def print_summary(
    table_snapshots: list[dict[str, Any]],
    category_snapshots: list[dict[str, Any]],
    template_snapshots: list[dict[str, Any]],
    skip_samples: bool,
) -> None:
    total_fields = sum(len(snapshot.get("field_names", [])) for snapshot in table_snapshots)
    print(
        f"Cargo tables: {len(table_snapshots)} fetched, {total_fields} fields across all tables"
    )
    if not skip_samples:
        total_samples = sum(len(snapshot.get("sample_rows", [])) for snapshot in table_snapshots)
        print(f"Cargo samples: {total_samples} rows fetched")
    if category_snapshots:
        print(f"Categories: {len(category_snapshots)} fetched")
        for snapshot in category_snapshots:
            print(f"  {snapshot['category_title']}: {snapshot['member_count']} members")
        family_summary = build_cargo_template_family_summary(category_snapshots)
        if family_summary["component_families"] or family_summary["candidate_page_templates"]:
            print(
                "Cargo template families: "
                f"{family_summary['component_family_count']} component families, "
                f"{len(family_summary['candidate_page_templates'])} top-level candidate templates"
            )
    if template_snapshots:
        template_summary = build_template_inspection_summary(template_snapshots)
        print(
            "Template inspection: "
            f"{len(template_snapshots)} templates, "
            f"{len(template_summary['likely_page_archetype_anchors'])} likely archetype anchors, "
            f"{len(template_summary['likely_helper_templates'])} likely helpers"
        )


def main() -> None:
    args = parse_args()

    categories = []
    if not args.no_default_categories:
        categories.extend(DEFAULT_CATEGORIES)
    if args.categories:
        categories.extend(args.categories)
    categories = sorted_unique(categories)

    client = WikiApiClient(
        api_url=args.api_url,
        user_agent=args.user_agent,
        sleep_seconds=args.sleep_seconds,
        timeout_seconds=args.timeout_seconds,
        max_retries=args.max_retries,
    )

    cargo_tables_path = args.out_dir / "cargo_tables.json"
    cargo_tables: list[dict[str, Any]]
    if args.resume and args.tables:
        cargo_tables = [{"name": table} for table in sorted_unique(args.tables)]
    elif args.resume and cargo_tables_path.exists():
        cached_tables = read_json(cargo_tables_path).get("tables", [])
        cargo_tables = [
            entry
            for entry in cached_tables
            if isinstance(entry, dict) and entry.get("name")
        ]
    else:
        cargo_tables = fetch_cargo_tables(client)

    if args.tables:
        requested = {table.casefold(): table for table in args.tables}
        cargo_tables = [
            entry for entry in cargo_tables if entry["name"].casefold() in requested
        ]

    table_dir = args.out_dir / "tables"
    category_dir = args.out_dir / "categories"
    table_snapshots: list[dict[str, Any]] = []

    for table_entry in cargo_tables:
        table_name = table_entry["name"]
        table_path = table_dir / f"{safe_filename(table_name)}.json"
        if args.resume and table_path.exists():
            snapshot = read_json(table_path)
        else:
            print(f"Fetching Cargo table schema: {table_name}")
            snapshot = table_snapshot(
                client,
                table_entry,
                sample_fields=args.sample_fields,
                sample_limit=args.sample_limit,
                skip_samples=args.skip_samples,
            )
            if not args.summary_only:
                write_json(table_path, snapshot)
        table_snapshots.append(snapshot)

    category_snapshots: list[dict[str, Any]] = []
    if not args.skip_categories:
        for category_title in categories:
            category_path = category_dir / f"{safe_filename(category_title)}.json"
            if args.resume and category_path.exists():
                snapshot = read_json(category_path)
            else:
                print(f"Fetching category members: {category_title}")
                snapshot = category_snapshot(
                    client,
                    category_title=category_title,
                    namespaces=args.category_namespaces,
                )
                if not args.summary_only:
                    write_json(category_path, snapshot)
            category_snapshots.append(snapshot)

    summary = {
        "fetched_at": iso_now(),
        "api_url": args.api_url,
        "user_agent": args.user_agent,
        "request_policy": {
            "sleep_seconds": args.sleep_seconds,
            "timeout_seconds": args.timeout_seconds,
            "max_retries": args.max_retries,
        },
        "sample_query": {
            "fields": args.sample_fields,
            "limit": args.sample_limit,
            "enabled": not args.skip_samples,
        },
        "cargo_tables": {
            "count": len(table_snapshots),
            "names": [snapshot["table"] for snapshot in table_snapshots],
        },
        "categories": {
            "count": len(category_snapshots),
            "titles": [snapshot["category_title"] for snapshot in category_snapshots],
        },
    }
    cargo_family_summary = build_cargo_template_family_summary(category_snapshots) if category_snapshots else {}
    if not cargo_family_summary and args.resume:
        cargo_family_summary = load_existing_cargo_family_summary(args.out_dir)

    template_dir = args.out_dir / "templates"
    template_snapshots: list[dict[str, Any]] = []
    if args.inspect_templates or args.template_titles:
        template_titles = select_template_titles_for_inspection(cargo_family_summary, args.template_titles)
        for title in template_titles:
            template_path = template_dir / f"{safe_filename(title)}.json"
            if args.resume and template_path.exists():
                snapshot = read_json(template_path)
            else:
                print(f"Inspecting template: {title}")
                snapshot = template_snapshot(
                    client,
                    title=title,
                    cargo_family_summary=cargo_family_summary,
                    embeddedin_namespaces=args.embeddedin_namespaces,
                    max_embeddedin_results=args.max_embeddedin_results,
                )
                if not args.summary_only:
                    write_json(template_path, snapshot)
            template_snapshots.append(snapshot)

    if template_snapshots:
        summary["templates"] = {
            "count": len(template_snapshots),
            "titles": [snapshot["title"] for snapshot in template_snapshots],
        }
    template_inspection_summary = build_template_inspection_summary(template_snapshots) if template_snapshots else {}

    print_summary(
        table_snapshots,
        category_snapshots,
        template_snapshots,
        skip_samples=args.skip_samples,
    )

    if args.summary_only:
        return

    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_json(
        args.out_dir / "cargo_tables.json",
        {
            "fetched_at": summary["fetched_at"],
            "tables": cargo_tables,
        },
    )
    write_json(args.out_dir / "summary.json", summary)
    if cargo_family_summary:
        write_json(args.out_dir / "cargo_template_families.json", cargo_family_summary)
    if template_inspection_summary:
        write_json(args.out_dir / "template_inspection_summary.json", template_inspection_summary)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        raise SystemExit(130)
