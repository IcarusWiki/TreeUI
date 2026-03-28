#!/usr/bin/env python3
"""
Build deterministic wiki page inventories from Icarus game data.

This is meant to support a "game-data first" wiki workflow:
- derive the canonical page titles that should exist
- compare those titles against a local wiki page export
- emit review queues for ambiguous titles
- optionally write starter item stubs for missing pages

The script intentionally avoids AI-authored prose. Generated item stubs only
contain values pulled directly from game data plus template-ready sections.

Examples:
    python scripts/build_stub_inventory.py --fetch-live-titles
    python scripts/build_stub_inventory.py --kinds items --render-title "CHAC Sentinel Pistol"
    python scripts/build_stub_inventory.py --kinds prospects --render-title Beachhead
    python scripts/build_stub_inventory.py --kinds creatures --render-title Raptor

"""

from __future__ import annotations

import argparse
import csv
import difflib
import json
import re
import sys
import tomllib
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
DEFAULT_OUT_DIR = ROOT / "generated" / "page_inventory"
DEFAULT_OUTPUT_FILE = ROOT / "output" / "output.wiki"
DEFAULT_ARCHETYPE_CONFIG = ROOT / "config" / "page_archetypes.toml"
DEFAULT_WIKI_API_URL = "https://icarus.wiki.gg/api.php"
LIVE_WIKI_USER_AGENT = "IcarusWikiWorkshop/1.0 (inventory compare; local tooling)"

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from generate_tree_data import (  # noqa: E402
    _static_fallback_keys,
    clean_display_label,
    clean_icon_path,
    extract_duration_parts,
    format_enum_label,
    load_json,
    parse_flavor_sections,
    parse_nsloctext,
)


COMPARE_SPACE_RE = re.compile(r"\s+")
INVALID_FILENAME_RE = re.compile(r'[<>:"/\\|?*]+')
SUSPICIOUS_SOURCE_RE = re.compile(
    r"(?:^|_)(?:Debug|Dev|Dummy|FieldGuide|Prop|Mission|Quest|Collectable|AudioLog|Test)(?:_|$)",
    re.IGNORECASE,
)
TITLE_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)?")
STAT_NAME_RE = re.compile(r'Value="([^"]+)"')
KNOWN_TITLE_ACRONYMS = {
    "CHAC",
    "UDA",
    "EDS",
    "OEI",
    "SMG",
    "TPS",
}

SUPPORTED_ARCHETYPE_KINDS = {"item", "prospect", "creature"}
SUPPORTED_RENDERERS = {
    "item_firearm_workshop",
    "item_generic",
    "prospect",
    "creature",
}


def _validated_string_list(value: Any, field_name: str, archetype_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(entry, str) for entry in value):
        raise ValueError(f'Archetype "{archetype_name}" field "{field_name}" must be a list of strings.')
    return [entry for entry in value if entry]


def load_archetype_config(path: Path) -> dict[str, dict[str, Any]]:
    with path.open("rb") as handle:
        payload = tomllib.load(handle)

    if not isinstance(payload, dict) or not payload:
        raise ValueError(f"Archetype config {path} did not contain any archetype tables.")

    archetypes: dict[str, dict[str, Any]] = {}
    defaults_by_kind: dict[str, list[str]] = defaultdict(list)

    for archetype_name, raw_config in payload.items():
        if not isinstance(raw_config, dict):
            raise ValueError(f'Archetype "{archetype_name}" must be a TOML table.')

        missing_fields = {
            field
            for field in ["kind", "renderer", "linked_tables"]
            if field not in raw_config
        }
        if "anchor_template" not in raw_config and "template" not in raw_config:
            missing_fields.add("anchor_template")
        if missing_fields:
            missing = ", ".join(sorted(missing_fields))
            raise ValueError(f'Archetype "{archetype_name}" is missing required fields: {missing}')

        kind = raw_config["kind"]
        renderer = raw_config["renderer"]
        anchor_template = raw_config.get("anchor_template") or raw_config.get("template")
        if kind not in SUPPORTED_ARCHETYPE_KINDS:
            raise ValueError(
                f'Archetype "{archetype_name}" has unsupported kind "{kind}". '
                f"Expected one of: {', '.join(sorted(SUPPORTED_ARCHETYPE_KINDS))}"
            )
        if renderer not in SUPPORTED_RENDERERS:
            raise ValueError(
                f'Archetype "{archetype_name}" has unsupported renderer "{renderer}". '
                f"Expected one of: {', '.join(sorted(SUPPORTED_RENDERERS))}"
            )

        linked_tables = _validated_string_list(raw_config.get("linked_tables"), "linked_tables", archetype_name)
        components = _validated_string_list(raw_config.get("components"), "components", archetype_name)
        cargo_component_families = _validated_string_list(
            raw_config.get("cargo_component_families"),
            "cargo_component_families",
            archetype_name,
        )
        cargo_helper_templates = _validated_string_list(
            raw_config.get("cargo_helper_templates"),
            "cargo_helper_templates",
            archetype_name,
        )
        requires_fields = _validated_string_list(raw_config.get("requires_fields"), "requires_fields", archetype_name)
        gameplay_tag_prefixes = _validated_string_list(
            raw_config.get("gameplay_tag_prefixes"),
            "gameplay_tag_prefixes",
            archetype_name,
        )
        prospect_types = _validated_string_list(raw_config.get("prospect_types"), "prospect_types", archetype_name)
        match_priority = raw_config.get("match_priority", 0)
        if not isinstance(match_priority, int):
            raise ValueError(f'Archetype "{archetype_name}" field "match_priority" must be an integer.')

        archetypes[archetype_name] = {
            "kind": kind,
            "anchor_template": str(anchor_template),
            "template": str(anchor_template),
            "renderer": renderer,
            "linked_tables": linked_tables,
            "components": components,
            "cargo_component_families": cargo_component_families,
            "cargo_helper_templates": cargo_helper_templates,
            "match_priority": match_priority,
            "default": bool(raw_config.get("default", False)),
            "requires_fields": requires_fields,
            "gameplay_tag_prefixes": gameplay_tag_prefixes,
            "prospect_types": prospect_types,
        }

        if archetypes[archetype_name]["default"]:
            defaults_by_kind[kind].append(archetype_name)

    for kind, archetype_names in defaults_by_kind.items():
        if len(archetype_names) > 1:
            raise ValueError(
                f'Multiple default archetypes declared for kind "{kind}": {", ".join(sorted(archetype_names))}'
            )

    return archetypes


def get_archetype_definition(
    archetypes: dict[str, dict[str, Any]],
    archetype_name: str,
    expected_kind: str | None = None,
) -> dict[str, Any]:
    config = archetypes.get(archetype_name)
    if not config:
        raise ValueError(f'Unknown archetype "{archetype_name}" in loaded config.')
    if expected_kind and config["kind"] != expected_kind:
        raise ValueError(
            f'Archetype "{archetype_name}" has kind "{config["kind"]}", expected "{expected_kind}".'
        )
    return config


def archetype_matches_record(record: dict[str, Any], config: dict[str, Any]) -> bool:
    requires_fields = config.get("requires_fields", [])
    if requires_fields and not all(record.get(field) for field in requires_fields):
        return False

    gameplay_tag_prefixes = config.get("gameplay_tag_prefixes", [])
    if gameplay_tag_prefixes:
        tags = record.get("gameplay_tags", [])
        if not any(
            any(tag.startswith(prefix) for prefix in gameplay_tag_prefixes)
            for tag in tags
        ):
            return False

    prospect_types = config.get("prospect_types", [])
    if prospect_types and record.get("prospect_type") not in prospect_types:
        return False

    return True


def classify_record_archetype(
    kind: str,
    record: dict[str, Any],
    archetypes: dict[str, dict[str, Any]],
) -> str:
    matching: list[tuple[int, str]] = []
    default_name = ""

    for archetype_name, config in archetypes.items():
        if config["kind"] != kind:
            continue
        if config.get("default") and not default_name:
            default_name = archetype_name
        if archetype_matches_record(record, config):
            matching.append((config.get("match_priority", 0), archetype_name))

    if matching:
        matching.sort(key=lambda entry: (-entry[0], entry[1]))
        return matching[0][1]
    if default_name:
        return default_name
    raise ValueError(f'No archetype matched record kind "{kind}" and no default was configured.')


def apply_archetype_metadata(
    record: dict[str, Any],
    archetypes: dict[str, dict[str, Any]],
    archetype_name: str,
    expected_kind: str | None = None,
) -> dict[str, Any]:
    config = get_archetype_definition(archetypes, archetype_name, expected_kind=expected_kind)
    if record.get("kind") and record["kind"] != config["kind"]:
        raise ValueError(
            f'Record kind "{record["kind"]}" does not match archetype "{archetype_name}" kind "{config["kind"]}".'
        )

    enriched = dict(record)
    enriched["kind"] = config["kind"]
    enriched["archetype"] = archetype_name
    enriched["anchor_template"] = config["anchor_template"]
    enriched["template"] = config["template"]
    enriched["renderer"] = config["renderer"]
    enriched["linked_tables"] = list(config["linked_tables"])
    enriched["configured_components"] = list(config.get("components", []))
    enriched["components"] = select_available_components(enriched, config.get("components", []))
    enriched["cargo_component_families"] = list(config.get("cargo_component_families", []))
    enriched["configured_cargo_helper_templates"] = list(config.get("cargo_helper_templates", []))
    enriched["cargo_helper_templates"] = select_available_cargo_helpers(
        enriched,
        config.get("cargo_helper_templates", []),
    )
    return enriched


def component_is_available(record: dict[str, Any], component: str) -> bool:
    if component in {"usage_section", "description_section", "lead_section"}:
        return True
    if component == "crafting_section":
        return bool(record.get("crafted_in") or record.get("used_in"))
    if component == "resource_summary_template":
        return bool(
            record.get("icon")
            and (
                record.get("description")
                or record.get("flavor_text")
                or record.get("crafted_in")
            )
        )
    if component == "obtaining_section":
        return bool(record.get("workshop_rows") or record.get("workshop_item"))
    if component == "ammo_section":
        return bool(record.get("ammo_type_name"))
    if component == "repair_section":
        return bool(record.get("durable_row"))
    if component == "mission_briefing":
        return any(record.get(field) for field in ["operator", "biome", "background", "mission", "terms"])
    if component == "lore_section":
        return bool(record.get("lore2") or record.get("lore3"))
    return True


def select_available_components(record: dict[str, Any], configured_components: list[str]) -> list[str]:
    return [component for component in configured_components if component_is_available(record, component)]


def cargo_helper_is_available(record: dict[str, Any], helper_template: str) -> bool:
    if helper_template in {"Template:ProcessorRecipes", "Template:Get Recipes"}:
        return bool(record.get("crafted_in") or record.get("used_in"))
    return True


def select_available_cargo_helpers(record: dict[str, Any], configured_helpers: list[str]) -> list[str]:
    return [helper for helper in configured_helpers if cargo_helper_is_available(record, helper)]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build deterministic wiki page inventories and reviewed single-page outputs from Icarus game data."
    )
    parser.add_argument(
        "--kinds",
        nargs="+",
        choices=["items", "prospects", "creatures"],
        default=["items", "prospects", "creatures"],
        help="Content types to inventory. Default: items prospects creatures",
    )
    parser.add_argument(
        "--existing-titles",
        type=Path,
        help="Optional newline/CSV/JSON file of existing wiki page titles.",
    )
    parser.add_argument(
        "--existing-dir",
        type=Path,
        help="Optional directory of local .wiki pages to use as the existing title set.",
    )
    parser.add_argument(
        "--fetch-live-titles",
        action="store_true",
        help="Fetch current main-namespace page titles directly from the wiki API.",
    )
    parser.add_argument(
        "--wiki-api-url",
        default=DEFAULT_WIKI_API_URL,
        help=f"MediaWiki API endpoint used with --fetch-live-titles. Default: {DEFAULT_WIKI_API_URL}",
    )
    parser.add_argument(
        "--archetype-config",
        type=Path,
        default=DEFAULT_ARCHETYPE_CONFIG,
        help=f"Archetype config used for template and renderer selection. Default: {DEFAULT_ARCHETYPE_CONFIG}",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=f"Output directory for manifests and optional stubs. Default: {DEFAULT_OUT_DIR}",
    )
    parser.add_argument(
        "--all-items",
        action="store_true",
        help="Include every item-like D_ItemsStatic row instead of the conservative recipe/workshop inventory.",
    )
    parser.add_argument(
        "--write-item-stubs",
        action="store_true",
        help="Write starter item stub pages. Only applies to item records.",
    )
    parser.add_argument(
        "--only-missing",
        action="store_true",
        help="When writing stubs, only write pages not found in the existing title set.",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print a summary without writing manifests or stub files.",
    )
    parser.add_argument(
        "--render-title",
        help="Render exactly one page title into output/output.wiki for manual review.",
    )
    parser.add_argument(
        "--render-kind",
        choices=["items", "prospects", "creatures"],
        help="Optional content type for --render-title when a title could match multiple inventories.",
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        default=DEFAULT_OUTPUT_FILE,
        help=f"Output file used by --render-title. Default: {DEFAULT_OUTPUT_FILE}",
    )
    return parser.parse_args()


def comparable_title(title: str) -> str:
    return COMPARE_SPACE_RE.sub(" ", str(title or "").replace("_", " ")).strip().casefold()


def loose_title_key(title: str) -> str:
    key = comparable_title(title)
    key = key.replace('"', "").replace("'", "")
    return re.sub(r"\s+", " ", key).strip()


def safe_filename(title: str) -> str:
    cleaned = INVALID_FILENAME_RE.sub("_", str(title or "").strip())
    cleaned = cleaned.replace(" ", "_")
    cleaned = re.sub(r"_+", "_", cleaned).strip("._")
    return cleaned or "page"


def sorted_unique(values: list[str] | set[str]) -> list[str]:
    return sorted({value for value in values if value})


def load_existing_titles(path: Path | None, directory: Path | None) -> set[str]:
    titles: set[str] = set()

    if path:
        suffix = path.suffix.lower()
        if suffix == ".json":
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                for entry in payload:
                    if isinstance(entry, str):
                        titles.add(entry)
                    elif isinstance(entry, dict):
                        title = entry.get("title") or entry.get("page_title") or entry.get("name")
                        if title:
                            titles.add(str(title))
        elif suffix == ".csv":
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                if reader.fieldnames:
                    title_field = next(
                        (
                            field
                            for field in reader.fieldnames
                            if field and field.strip().lower() in {"title", "page_title", "name"}
                        ),
                        None,
                    )
                    if title_field:
                        for row in reader:
                            title = row.get(title_field, "")
                            if title:
                                titles.add(title)
                    else:
                        handle.seek(0)
                        fallback = csv.reader(handle)
                        for row in fallback:
                            if row:
                                titles.add(row[0])
        else:
            for line in path.read_text(encoding="utf-8").splitlines():
                title = line.strip()
                if title:
                    titles.add(title)

    if directory:
        for file_path in directory.rglob("*.wiki"):
            titles.add(file_path.stem)

    return titles


def normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def choose_preferred_text(values: list[str]) -> str:
    cleaned = [value.strip() for value in values if value and value.strip()]
    if not cleaned:
        return ""
    return max(cleaned, key=lambda value: (len(value), value))


def canonical_page_title(text: str) -> str:
    text = clean_display_label(parse_nsloctext(text))
    if not text:
        return ""

    def repl(match: re.Match[str]) -> str:
        word = match.group(0)
        start = match.start()
        prev_char = text[start - 1] if start > 0 else ""
        if not word.isalpha():
            return word
        if word.isupper():
            if word in KNOWN_TITLE_ACRONYMS:
                return word
            if prev_char.isdigit():
                return word.lower()
            return word[:1] + word[1:].lower()
        if word.islower():
            return word[:1].upper() + word[1:]
        return word

    return TITLE_WORD_RE.sub(repl, text)


def fetch_live_titles(api_url: str) -> set[str]:
    titles: set[str] = set()
    params: dict[str, str] = {
        "action": "query",
        "list": "allpages",
        "apnamespace": "0",
        "aplimit": "500",
        "format": "json",
    }

    while True:
        request_url = f"{api_url}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(
            request_url,
            headers={"User-Agent": LIVE_WIKI_USER_AGENT},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))

        for page in payload.get("query", {}).get("allpages", []):
            title = page.get("title", "")
            if title:
                titles.add(title)

        continuation = payload.get("continue")
        if not continuation:
            break

        for key, value in continuation.items():
            params[key] = value

    return titles


def extract_stat_name(stat_key: str) -> str:
    match = STAT_NAME_RE.search(str(stat_key or ""))
    return match.group(1) if match else str(stat_key or "")


def format_stat_value(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value)


def format_additional_stats(additional_stats: dict[str, Any]) -> list[str]:
    parts = []
    for key, value in (additional_stats or {}).items():
        parts.append(f"{extract_stat_name(key)}:{format_stat_value(value)}")
    return parts


def map_workshop_costs(cost_rows: list[dict[str, Any]]) -> dict[str, Any]:
    key_map = {
        "Credits": "ren",
        "Exotic1": "exotics",
        "Exotic2": "redExotics",
        "Biomass": "biomass",
    }
    mapped = {value: "" for value in key_map.values()}
    for row in cost_rows or []:
        meta_name = row.get("Meta", {}).get("RowName", "")
        param_name = key_map.get(meta_name)
        if not param_name:
            continue
        amount = row.get("Amount", "")
        if isinstance(amount, float) and amount.is_integer():
            amount = int(amount)
        mapped[param_name] = amount
    return mapped


def load_item_lookups() -> dict[str, Any]:
    itemable_rows = load_json("Traits", "D_Itemable.json").get("Rows", [])
    static_rows = load_json("Items", "D_ItemsStatic.json").get("Rows", [])
    recipe_rows = load_json("Crafting", "D_ProcessorRecipes.json").get("Rows", [])
    recipe_sets = load_json("Crafting", "D_RecipeSets.json").get("Rows", [])
    workshop_rows = load_json("MetaWorkshop", "D_WorkshopItems.json").get("Rows", [])
    talents_rows = load_json("Talents", "D_Talents.json").get("Rows", [])
    durable_rows = load_json("Traits", "D_Durable.json").get("Rows", [])
    firearm_rows = load_json("Tools", "D_FirearmData.json").get("Rows", [])
    valid_ammo_rows = load_json("Tools", "D_ValidAmmoTypes.json").get("Rows", [])

    itemable_by_key = {row.get("Name", ""): row for row in itemable_rows if row.get("Name")}
    static_by_key = {row.get("Name", ""): row for row in static_rows if row.get("Name")}
    durable_by_key = {row.get("Name", ""): row for row in durable_rows if row.get("Name")}
    firearm_by_key = {row.get("Name", ""): row for row in firearm_rows if row.get("Name")}
    valid_ammo_by_key = {row.get("Name", ""): row for row in valid_ammo_rows if row.get("Name")}

    normalized_static: dict[str, list[str]] = defaultdict(list)
    normalized_itemable: dict[str, list[str]] = defaultdict(list)

    for key in static_by_key:
        normalized_static[normalize_key(key)].append(key)

    for key in itemable_by_key:
        normalized_itemable[normalize_key(key)].append(key)
        if key.startswith("Item_"):
            normalized_itemable[normalize_key(key[5:])].append(key)

    recipe_set_labels = {}
    for row in recipe_sets:
        name = row.get("Name", "")
        if not name:
            continue
        label = canonical_page_title(parse_nsloctext(row.get("RecipeSetName", "")))
        if not label:
            label = format_enum_label(name)
        recipe_set_labels[name] = label

    workshop_item_keys = set()
    workshop_rows_by_item_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in workshop_rows:
        item_key = row.get("Item", {}).get("RowName", "")
        if item_key and item_key != "None":
            workshop_item_keys.add(item_key)
            workshop_rows_by_item_key[item_key].append(row)

    talents_by_workshop_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in talents_rows:
        extra_data = row.get("ExtraData", {}) or {}
        workshop_key = extra_data.get("RowName", "")
        data_table = extra_data.get("DataTableName", "D_WorkshopItems")
        if workshop_key and workshop_key != "None" and data_table == "D_WorkshopItems":
            talents_by_workshop_key[workshop_key].append(row)

    return {
        "itemable_by_key": itemable_by_key,
        "static_by_key": static_by_key,
        "durable_by_key": durable_by_key,
        "firearm_by_key": firearm_by_key,
        "valid_ammo_by_key": valid_ammo_by_key,
        "normalized_static": normalized_static,
        "normalized_itemable": normalized_itemable,
        "recipe_rows": recipe_rows,
        "recipe_set_labels": recipe_set_labels,
        "workshop_item_keys": workshop_item_keys,
        "workshop_rows_by_item_key": workshop_rows_by_item_key,
        "talents_by_workshop_key": talents_by_workshop_key,
    }


def resolve_item_source(source_key: str, lookups: dict[str, Any]) -> dict[str, Any] | None:
    static_by_key = lookups["static_by_key"]
    itemable_by_key = lookups["itemable_by_key"]
    durable_by_key = lookups["durable_by_key"]
    firearm_by_key = lookups["firearm_by_key"]
    valid_ammo_by_key = lookups["valid_ammo_by_key"]
    normalized_static = lookups["normalized_static"]
    normalized_itemable = lookups["normalized_itemable"]

    resolution = ""
    resolved_static_key = ""
    itemable_key = ""

    if source_key in static_by_key:
        resolved_static_key = source_key
        resolution = "static-exact"
    else:
        for candidate in _static_fallback_keys(source_key, source_key):
            if candidate in static_by_key:
                resolved_static_key = candidate
                resolution = "static-fallback"
                break

    if resolved_static_key:
        itemable_key = static_by_key[resolved_static_key].get("Itemable", {}).get("RowName", "")
        if itemable_key == "None":
            itemable_key = ""

    if not itemable_key:
        direct_candidates = [source_key]
        if not source_key.startswith("Item_"):
            direct_candidates.append(f"Item_{source_key}")
        for candidate in direct_candidates:
            if candidate in itemable_by_key:
                itemable_key = candidate
                resolution = resolution or "itemable-exact"
                break

    if not resolved_static_key:
        static_matches = normalized_static.get(normalize_key(source_key), [])
        if len(static_matches) == 1:
            resolved_static_key = static_matches[0]
            resolution = resolution or "static-normalized"
            if not itemable_key:
                itemable_key = static_by_key[resolved_static_key].get("Itemable", {}).get("RowName", "")
                if itemable_key == "None":
                    itemable_key = ""

    if not itemable_key:
        itemable_matches = normalized_itemable.get(normalize_key(source_key), [])
        unique_matches = sorted(set(itemable_matches))
        if len(unique_matches) == 1:
            itemable_key = unique_matches[0]
            resolution = resolution or "itemable-normalized"

    if not itemable_key:
        normalized = normalize_key(source_key)
        static_match = difflib.get_close_matches(normalized, list(normalized_static.keys()), n=1, cutoff=0.97)
        if static_match:
            matches = sorted(set(normalized_static[static_match[0]]))
            if len(matches) == 1:
                resolved_static_key = matches[0]
                resolution = resolution or "static-fuzzy"
                itemable_key = static_by_key[resolved_static_key].get("Itemable", {}).get("RowName", "")
                if itemable_key == "None":
                    itemable_key = ""

    if not itemable_key:
        normalized = normalize_key(source_key)
        itemable_match = difflib.get_close_matches(normalized, list(normalized_itemable.keys()), n=1, cutoff=0.97)
        if itemable_match:
            matches = sorted(set(normalized_itemable[itemable_match[0]]))
            if len(matches) == 1:
                itemable_key = matches[0]
                resolution = resolution or "itemable-fuzzy"

    if not itemable_key or itemable_key not in itemable_by_key:
        return None

    static_row = static_by_key.get(resolved_static_key, {})
    itemable_row = itemable_by_key[itemable_key]

    manual_tags = [
        tag.get("TagName", "")
        for tag in static_row.get("Manual_Tags", {}).get("GameplayTags", [])
        if isinstance(tag, dict)
    ]

    display_name = clean_display_label(parse_nsloctext(itemable_row.get("DisplayName", "")))
    page_title = canonical_page_title(display_name)
    icon = clean_icon_path(itemable_row.get("Icon", "None"))
    description = clean_display_label(parse_nsloctext(itemable_row.get("Description", "")))
    flavor_text = clean_display_label(parse_nsloctext(itemable_row.get("FlavorText", "")))
    durable_row_name = static_row.get("Durable", {}).get("RowName", "")
    firearm_row_name = static_row.get("FirearmData", {}).get("RowName", "")
    durable_row = durable_by_key.get(durable_row_name, {})
    firearm_row = firearm_by_key.get(firearm_row_name, {})
    ammo_type_name = firearm_row.get("ValidAmmoTypes", {}).get("RowName", "")
    valid_ammo_row = valid_ammo_by_key.get(ammo_type_name, {})
    additional_stats = format_additional_stats(static_row.get("AdditionalStats", {}))

    if not page_title or not icon:
        return None

    return {
        "source_key": source_key,
        "resolved_static_key": resolved_static_key,
        "itemable_key": itemable_key,
        "page_title": page_title,
        "display_name": display_name,
        "icon": icon,
        "description": description,
        "flavor_text": flavor_text,
        "weight": itemable_row.get("Weight", 0),
        "max_stack": itemable_row.get("MaxStack", 0),
        "crafting_experience": static_row.get("CraftingExperience", 0),
        "gameplay_tags": sorted_unique(manual_tags),
        "additional_stats": additional_stats,
        "durable_row_name": durable_row_name,
        "durable_row": durable_row,
        "firearm_row_name": firearm_row_name,
        "firearm_row": firearm_row,
        "ammo_type_name": ammo_type_name,
        "valid_ammo_row": valid_ammo_row,
        "static_row": static_row,
        "static_present": bool(resolved_static_key),
        "resolution": resolution or "itemable-exact",
    }


def collect_recipe_links(lookups: dict[str, Any]) -> dict[str, Any]:
    recipe_rows = lookups["recipe_rows"]
    recipe_set_labels = lookups["recipe_set_labels"]

    crafted_in_by_key: dict[str, set[str]] = defaultdict(set)
    used_in_by_key: dict[str, set[str]] = defaultdict(set)
    input_keys: set[str] = set()
    output_keys: set[str] = set()

    for row in recipe_rows:
        row_output_keys = []
        row_input_keys = []

        for output in row.get("Outputs", []):
            output_key = output.get("Element", {}).get("RowName", "")
            if output_key and output_key != "None":
                row_output_keys.append(output_key)
                output_keys.add(output_key)

        for entry in row.get("Inputs", []):
            input_key = entry.get("Element", {}).get("RowName", "")
            if input_key and input_key != "None":
                row_input_keys.append(input_key)
                input_keys.add(input_key)

        bench_labels = {
            recipe_set_labels.get(ref.get("RowName", ""), "")
            for ref in row.get("RecipeSets", [])
            if isinstance(ref, dict)
        }
        bench_labels = {label for label in bench_labels if label}

        for output_key in row_output_keys:
            crafted_in_by_key[output_key].update(bench_labels)

        output_titles = []
        for output_key in row_output_keys:
            resolved = resolve_item_source(output_key, lookups)
            if resolved:
                output_titles.append(resolved["page_title"])

        for input_key in row_input_keys:
            used_in_by_key[input_key].update(output_titles)

    return {
        "crafted_in_by_key": crafted_in_by_key,
        "used_in_by_key": used_in_by_key,
        "input_keys": input_keys,
        "output_keys": output_keys,
    }

def build_item_inventory(
    include_all_items: bool,
    archetypes: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    lookups = load_item_lookups()
    recipe_links = collect_recipe_links(lookups)

    candidate_keys = set(recipe_links["input_keys"]) | set(recipe_links["output_keys"]) | set(
        lookups["workshop_item_keys"]
    )
    if include_all_items:
        candidate_keys.update(lookups["static_by_key"].keys())

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unresolved_keys = []

    for key in sorted(candidate_keys):
        resolved = resolve_item_source(key, lookups)
        if not resolved:
            unresolved_keys.append(key)
            continue

        resolved["recipe_input"] = key in recipe_links["input_keys"]
        resolved["recipe_output"] = key in recipe_links["output_keys"]
        resolved["workshop_item"] = key in lookups["workshop_item_keys"]

        crafted_in = set(recipe_links["crafted_in_by_key"].get(key, set()))
        if resolved["resolved_static_key"] and resolved["resolved_static_key"] != key:
            crafted_in.update(recipe_links["crafted_in_by_key"].get(resolved["resolved_static_key"], set()))
        resolved["crafted_in"] = sorted_unique(crafted_in)

        used_in = set(recipe_links["used_in_by_key"].get(key, set()))
        if resolved["resolved_static_key"] and resolved["resolved_static_key"] != key:
            used_in.update(recipe_links["used_in_by_key"].get(resolved["resolved_static_key"], set()))
        resolved["used_in"] = sorted_unique(used_in)

        workshop_rows = list(
            lookups["workshop_rows_by_item_key"].get(resolved.get("resolved_static_key", ""), [])
        )
        resolved["workshop_rows"] = workshop_rows

        talent_rows = []
        for workshop_row in workshop_rows:
            talent_rows.extend(
                lookups["talents_by_workshop_key"].get(workshop_row.get("Name", ""), [])
            )
        resolved["talent_rows"] = talent_rows
        resolved = apply_archetype_metadata(
            resolved,
            archetypes,
            classify_record_archetype("item", resolved, archetypes),
            expected_kind="item",
        )

        grouped[resolved["page_title"]].append(resolved)

    records = []
    review_count = 0

    for page_title in sorted(grouped):
        variants = grouped[page_title]

        def variant_score(variant: dict[str, Any]) -> tuple[int, int, int, int, int]:
            filled_fields = sum(
                1
                for value in [
                    variant.get("icon"),
                    variant.get("description"),
                    variant.get("flavor_text"),
                    variant.get("crafting_experience"),
                ]
                if value
            )
            return (
                1 if variant.get("static_present") else 0,
                1 if variant.get("recipe_output") else 0,
                1 if variant.get("workshop_item") else 0,
                0 if SUSPICIOUS_SOURCE_RE.search(variant.get("source_key", "")) else 1,
                filled_fields,
            )

        preferred = max(variants, key=variant_score)

        review_reasons = []
        if len(variants) > 1:
            review_reasons.append("multiple internal rows share this page title")

        if any(variant.get("resolution", "").endswith("fuzzy") for variant in variants):
            review_reasons.append("fuzzy data match used")

        if any(not variant.get("static_present") for variant in variants):
            review_reasons.append("some sources do not have a D_ItemsStatic row")

        if len({variant.get("archetype", "") for variant in variants}) > 1:
            review_reasons.append("multiple archetypes resolved to the same page title")

        suspicious_sources = [
            variant["source_key"]
            for variant in variants
            if SUSPICIOUS_SOURCE_RE.search(variant.get("source_key", ""))
        ]
        if suspicious_sources:
            review_reasons.append("suspicious internal-style source keys present")

        if review_reasons:
            review_count += 1

        all_tags = sorted_unique(
            tag
            for variant in variants
            for tag in variant.get("gameplay_tags", [])
        )

        records.append(
            apply_archetype_metadata(
                {
                    "page_title": page_title,
                    "display_name": preferred["display_name"],
                    "icon": preferred["icon"],
                    "description": choose_preferred_text([variant["description"] for variant in variants]),
                    "flavor_text": choose_preferred_text([variant["flavor_text"] for variant in variants]),
                    "weight": preferred.get("weight", 0),
                    "max_stack": preferred.get("max_stack", 0),
                    "crafting_experience": preferred.get("crafting_experience", 0),
                    "gameplay_tags": all_tags,
                    "additional_stats": preferred.get("additional_stats", []),
                    "crafted_in": sorted_unique(
                        bench for variant in variants for bench in variant.get("crafted_in", [])
                    ),
                    "used_in": sorted_unique(
                        title for variant in variants for title in variant.get("used_in", [])
                    ),
                    "source_keys": sorted_unique([variant["source_key"] for variant in variants]),
                    "resolved_static_keys": sorted_unique(
                        [variant.get("resolved_static_key", "") for variant in variants]
                    ),
                    "itemable_keys": sorted_unique([variant["itemable_key"] for variant in variants]),
                    "recipe_input": any(variant.get("recipe_input") for variant in variants),
                    "recipe_output": any(variant.get("recipe_output") for variant in variants),
                    "workshop_item": any(variant.get("workshop_item") for variant in variants),
                    "durable_row_name": preferred.get("durable_row_name", ""),
                    "durable_row": preferred.get("durable_row", {}),
                    "firearm_row_name": preferred.get("firearm_row_name", ""),
                    "firearm_row": preferred.get("firearm_row", {}),
                    "ammo_type_name": preferred.get("ammo_type_name", ""),
                    "valid_ammo_row": preferred.get("valid_ammo_row", {}),
                    "workshop_rows": [
                        row
                        for variant in variants
                        for row in variant.get("workshop_rows", [])
                    ],
                    "talent_rows": [
                        row
                        for variant in variants
                        for row in variant.get("talent_rows", [])
                    ],
                    "primary_variant": preferred,
                    "review_reasons": review_reasons,
                    "variants": variants,
                },
                archetypes,
                preferred["archetype"],
                expected_kind="item",
            )
        )

    summary = {
        "total_pages": len(records),
        "review_pages": review_count,
        "unresolved_keys": sorted(unresolved_keys),
        "candidate_keys": len(candidate_keys),
        "resolved_keys": sum(len(variants) for variants in grouped.values()),
        "include_all_items": include_all_items,
    }
    return records, summary


def classify_prospect(row: dict[str, Any], page_title: str) -> str:
    if row.get("bIsOpenWorld"):
        return "open_world"
    if row.get("bIsPersistent"):
        return "outpost"
    if "Outpost" in row.get("Name", "") or page_title.endswith(": Outpost"):
        return "outpost"
    return "mission"


def format_duration(duration: dict[str, Any]) -> str:
    days, hours, mins = extract_duration_parts(duration or {})
    parts = []
    if days:
        parts.append(f"{days} Day" if days == 1 else f"{days} Days")
    if hours:
        parts.append(f"{hours} Hour" if hours == 1 else f"{hours} Hours")
    if mins:
        parts.append(f"{mins} Min" if mins == 1 else f"{mins} Mins")
    return ", ".join(parts)


def build_prospect_inventory(
    archetypes: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = load_json("Prospects", "D_ProspectList.json").get("Rows", [])
    terrain_rows = load_json("Prospects", "D_Terrains.json").get("Rows", [])
    terrain_labels = {
        row.get("Name", ""): clean_display_label(parse_nsloctext(row.get("TerrainName", "")))
        for row in terrain_rows
        if row.get("Name")
    }
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in rows:
        drop_name = clean_display_label(parse_nsloctext(row.get("DropName", "")))
        page_title = canonical_page_title(drop_name) or canonical_page_title(row.get("Name", ""))
        if not page_title:
            continue

        flavour_text = clean_display_label(parse_nsloctext(row.get("FlavourText", "")))
        flavour_sections = parse_flavor_sections(flavour_text)
        difficulty = str(row.get("Difficulty", "") or "")
        terrain_name = terrain_labels.get(row.get("Terrain", {}).get("RowName", ""), "")
        prospect_type = classify_prospect(row, page_title)
        record = {
            "page_title": page_title,
            "source_key": row.get("Name", ""),
            "prospect_type": prospect_type,
            "image": clean_icon_path(row.get("ProspectImage", "None")),
            "description": clean_display_label(parse_nsloctext(row.get("Description", ""))),
            "flavor_text": flavour_text,
            "difficulty": format_enum_label(difficulty),
            "required_level": row.get("RequiredLevel", 0),
            "required_tech": format_enum_label(row.get("RequiredTech", "")),
            "availability": format_enum_label(row.get("OnProspectAvailability", "")),
            "duration": format_duration(row.get("TimeDuration", {})),
            "map_name": terrain_name,
            "operator": flavour_sections.get("operator", ""),
            "biome": flavour_sections.get("biome", ""),
            "background": flavour_sections.get("background", ""),
            "mission": flavour_sections.get("mission", ""),
            "terms": flavour_sections.get("terms", ""),
            "disabled": bool(row.get("bDisabled", False)),
        }
        record = apply_archetype_metadata(
            record,
            archetypes,
            classify_record_archetype("prospect", record, archetypes),
            expected_kind="prospect",
        )
        grouped[page_title].append(record)

    records = []
    review_pages = 0

    for page_title in sorted(grouped):
        variants = grouped[page_title]
        preferred = max(
            variants,
            key=lambda variant: (
                0 if variant.get("disabled") else 1,
                len(variant.get("description", "")),
                len(variant.get("flavor_text", "")),
            ),
        )

        review_reasons = []
        if len(variants) > 1:
            review_reasons.append("multiple internal rows share this page title")
        if any(variant.get("disabled") for variant in variants):
            review_reasons.append("disabled prospect row present")
        if preferred.get("prospect_type") != "mission":
            review_reasons.append("non-mission prospect type")

        if review_reasons:
            review_pages += 1

        records.append(
            apply_archetype_metadata(
                {
                    "page_title": page_title,
                    "prospect_type": preferred["prospect_type"],
                    "image": preferred["image"],
                    "description": choose_preferred_text([variant["description"] for variant in variants]),
                    "flavor_text": choose_preferred_text([variant["flavor_text"] for variant in variants]),
                    "difficulty": preferred["difficulty"],
                    "required_level": preferred["required_level"],
                    "required_tech": preferred["required_tech"],
                    "availability": preferred["availability"],
                    "duration": preferred["duration"],
                    "map_name": preferred["map_name"],
                    "operator": preferred["operator"],
                    "biome": preferred["biome"],
                    "background": preferred["background"],
                    "mission": preferred["mission"],
                    "terms": preferred["terms"],
                    "source_keys": sorted_unique([variant["source_key"] for variant in variants]),
                    "primary_variant": preferred,
                    "review_reasons": review_reasons,
                    "variants": variants,
                },
                archetypes,
                preferred["archetype"],
                expected_kind="prospect",
            )
        )

    summary = {
        "total_pages": len(records),
        "review_pages": review_pages,
    }
    return records, summary


def build_creature_inventory(
    archetypes: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    bestiary_rows = load_json("Bestiary", "D_BestiaryData.json").get("Rows", [])
    atmosphere_rows = load_json("Prospects", "D_Atmospheres.json").get("Rows", [])
    terrain_rows = load_json("Prospects", "D_Terrains.json").get("Rows", [])

    atmosphere_labels = {
        row.get("Name", ""): clean_display_label(parse_nsloctext(row.get("AtmosphereName", "")))
        for row in atmosphere_rows
        if row.get("Name")
    }
    terrain_labels = {
        row.get("Name", ""): clean_display_label(parse_nsloctext(row.get("TerrainName", "")))
        for row in terrain_rows
        if row.get("Name")
    }

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in bestiary_rows:
        creature_name = clean_display_label(parse_nsloctext(row.get("CreatureName", "")))
        page_title = canonical_page_title(creature_name) or canonical_page_title(row.get("Name", ""))
        if not page_title:
            continue

        biomes = sorted_unique(
            atmosphere_labels.get(ref.get("RowName", ""), "")
            for ref in row.get("Biomes", [])
            if isinstance(ref, dict)
        )
        maps = sorted_unique(
            terrain_labels.get(ref.get("RowName", ""), "")
            for ref in row.get("Maps", [])
            if isinstance(ref, dict)
        )

        record = {
            "page_title": page_title,
            "source_key": row.get("Name", ""),
            "image": clean_icon_path(row.get("Image", "None")),
            "biomes": biomes,
            "maps": maps,
            "lore1": clean_display_label(parse_nsloctext(row.get("Lore1", ""))),
            "lore2": clean_display_label(parse_nsloctext(row.get("Lore2", ""))),
            "lore3": clean_display_label(parse_nsloctext(row.get("Lore3", ""))),
            "is_boss": bool(row.get("bIsBoss", False)),
        }
        record = apply_archetype_metadata(
            record,
            archetypes,
            classify_record_archetype("creature", record, archetypes),
            expected_kind="creature",
        )
        grouped[page_title].append(record)

    records = []
    review_pages = 0
    for page_title in sorted(grouped):
        variants = grouped[page_title]
        preferred = max(
            variants,
            key=lambda variant: (
                len(variant.get("lore1", "")),
                len(variant.get("lore2", "")),
                len(variant.get("lore3", "")),
            ),
        )

        review_reasons = []
        if len(variants) > 1:
            review_reasons.append("multiple internal rows share this page title")
        if any(variant.get("is_boss") for variant in variants):
            review_reasons.append("boss creature row present")
        if review_reasons:
            review_pages += 1

        records.append(
            apply_archetype_metadata(
                {
                    "page_title": page_title,
                    "image": preferred["image"],
                    "biomes": sorted_unique(
                        biome for variant in variants for biome in variant.get("biomes", [])
                    ),
                    "maps": sorted_unique(
                        map_name for variant in variants for map_name in variant.get("maps", [])
                    ),
                    "lore1": choose_preferred_text([variant["lore1"] for variant in variants]),
                    "lore2": choose_preferred_text([variant["lore2"] for variant in variants]),
                    "lore3": choose_preferred_text([variant["lore3"] for variant in variants]),
                    "source_keys": sorted_unique([variant["source_key"] for variant in variants]),
                    "primary_variant": preferred,
                    "review_reasons": review_reasons,
                    "variants": variants,
                },
                archetypes,
                preferred["archetype"],
                expected_kind="creature",
            )
        )

    summary = {
        "total_pages": len(records),
        "review_pages": review_pages,
    }
    return records, summary


def annotate_existing_status(records: list[dict[str, Any]], existing_titles: set[str]) -> dict[str, int]:
    normalized_existing = {comparable_title(title) for title in existing_titles}
    existing_count = 0
    missing_count = 0
    review_missing_count = 0

    for record in records:
        exists = comparable_title(record["page_title"]) in normalized_existing
        record["exists_on_wiki"] = exists
        record["is_missing"] = not exists
        if exists:
            existing_count += 1
        else:
            missing_count += 1
            if record.get("review_reasons"):
                review_missing_count += 1

    return {
        "existing_pages": existing_count,
        "missing_pages": missing_count,
        "missing_review_pages": review_missing_count,
    }


def wiki_parameter_text(value: Any) -> str:
    text = str(value or "")
    return text.replace("|", "{{!}}").replace("\n", "<br>")


def itemable_template_name(record: dict[str, Any]) -> str:
    itemable_name = record["itemable_keys"][0] if record["itemable_keys"] else record["display_name"]
    if itemable_name.startswith("Item_"):
        itemable_name = itemable_name[5:]
    return itemable_name


def anchor_template_name(record: dict[str, Any]) -> str:
    return record.get("anchor_template") or record.get("template", "")


def format_weight_kg(weight: Any) -> str:
    try:
        numeric_weight = float(weight)
    except (TypeError, ValueError):
        return str(weight or "")
    kilograms = numeric_weight / 1000.0
    if kilograms.is_integer():
        return f"{int(kilograms)} Kg"
    return f"{kilograms:.3f}".rstrip("0").rstrip(".") + " Kg"


def render_comment_block(record: dict[str, Any]) -> list[str]:
    lines = [
        "<!-- Auto-generated from game data for manual review. -->",
        (
            f"<!-- Archetype: {record.get('archetype', '')} | Anchor: {record.get('anchor_template', '')} | "
            f"Renderer: {record.get('renderer', '')} | Linked tables: {', '.join(record.get('linked_tables', []))} -->"
        ),
        "<!-- Rendering mode: game-data-safe. Cargo helper templates are metadata only and are not emitted automatically. -->",
    ]
    if record.get("components"):
        lines.append("<!-- Available components: " + ", ".join(record["components"]) + " -->")
    if record.get("cargo_component_families"):
        lines.append(
            "<!-- Optional Cargo component families on the wiki: "
            + ", ".join(record["cargo_component_families"])
            + " -->"
        )
    if record.get("cargo_helper_templates"):
        lines.append(
            "<!-- Optional Cargo helper templates with relevant data: "
            + ", ".join(record["cargo_helper_templates"])
            + " -->"
        )
    if record.get("review_reasons"):
        lines.append("<!-- Review flags: " + "; ".join(record["review_reasons"]) + " -->")
    return lines


def render_workshop_cost_lines(prefix: str, cost_map: dict[str, Any]) -> list[str]:
    return [
        f"|{prefix}_ren = {cost_map.get('ren', '')}",
        f"|{prefix}_exotics = {cost_map.get('exotics', '')}",
        f"|{prefix}_redExotics = {cost_map.get('redExotics', '')}",
        f"|{prefix}_biomass = {cost_map.get('biomass', '')}",
    ]


def render_resource_summary_template(record: dict[str, Any]) -> list[str]:
    benches = "<br>".join(f"{{{{Item icon|{bench}}}}}" for bench in record.get("crafted_in", []))
    return [
        "{{Resource Template",
        "  | title1={{PAGENAME}}",
        f"  | image1={wiki_parameter_text(record.get('icon', ''))}.png",
        "  | caption1=",
        "  | category=",
        "  | techlvl=",
        "  | required_level=",
        f"  | description={wiki_parameter_text(record.get('description', ''))}",
        f"  | flavor={wiki_parameter_text(record.get('flavor_text', ''))}",
        f"  | bench={benches}",
        f"  | weight={wiki_parameter_text(format_weight_kg(record.get('weight', '')))}",
        f"  | stack_size={record.get('max_stack', '')}",
        "}}",
    ]


def render_item_firearm_workshop(record: dict[str, Any]) -> str:
    workshop_row = record.get("workshop_rows", [{}])[0] if record.get("workshop_rows") else {}
    talent_row = record.get("talent_rows", [{}])[0] if record.get("talent_rows") else {}
    durable_row = record.get("durable_row", {})
    firearm_row = record.get("firearm_row", {})
    valid_ammo_row = record.get("valid_ammo_row", {})
    itemable_name = itemable_template_name(record)
    repair_items = [
        f"{entry.get('Item', {}).get('RowName', '')}:{entry.get('Amount', '')}"
        for entry in durable_row.get("ItemsForRepair", [])
        if entry.get("Item", {}).get("RowName", "")
    ]
    research_costs = map_workshop_costs(workshop_row.get("ResearchCost", []))
    craft_costs = map_workshop_costs(workshop_row.get("ReplicationCost", []))
    valid_ammo_desc = clean_display_label(parse_nsloctext(valid_ammo_row.get("Description", "")))

    lines = [
        *render_comment_block(record),
        "{{" + anchor_template_name(record),
        "<!-- Wiki Data -->",
        "|isWorkshop = yes",
        "|categories = ",
        "|techLevelUnlock = Orbital",
        "|techLevelNeeded = ",
        "|prerequisite = ",
        "|prerequisiteMission = ",
        "|RequiredFeatureLevel = ",
        "",
        "<!-- D_Itemable.json and D_ItemsStatic.json -->",
        "|Itemable_displayName = {{PAGENAME}}",
        f"|Itemable_icon = {wiki_parameter_text(record['icon'])}.png",
        f"|Itemable_name = {wiki_parameter_text(itemable_name)}",
        f"|Itemable_flavorText = {wiki_parameter_text(record['flavor_text'])}",
        f"|Itemable_description = {wiki_parameter_text(record['description'])}",
        f"|Itemable_weight = {record['weight']}",
        f"|AdditionalStats = {wiki_parameter_text(', '.join(record.get('additional_stats', [])))}",
        f"|GameplayTags = {wiki_parameter_text(', '.join(record['gameplay_tags']))}",
        "",
        "<!-- D_Talents.json -->",
        f"|Talents_name = {wiki_parameter_text(talent_row.get('Name', ''))}",
        f"|Talents_positionX = {talent_row.get('Position', {}).get('X', '')}",
        f"|Talents_positionY = {talent_row.get('Position', {}).get('Y', '')}",
        f"|Talents_sizeX = {talent_row.get('Size', {}).get('X', '')}",
        f"|Talents_sizeY = {talent_row.get('Size', {}).get('Y', '')}",
        f"|TalentTree = {wiki_parameter_text(talent_row.get('TalentTree', {}).get('RowName', ''))}",
        "",
        "<!-- D_Durable.json -->",
        f"|Durable_maxDurability = {durable_row.get('Max_Durability', '')}",
        f"|ItemsForRepair = {wiki_parameter_text(', '.join(repair_items))}",
        "",
        "<!-- D_FirearmData.json -->",
        f"|hipAccuracyX = {firearm_row.get('HipAccuracy', {}).get('X', '')}",
        f"|hipAccuracyY = {firearm_row.get('HipAccuracy', {}).get('Y', '')}",
        f"|aimAccuracyX = {firearm_row.get('AimAccuracy', {}).get('X', '')}",
        f"|aimAccuracyY = {firearm_row.get('AimAccuracy', {}).get('Y', '')}",
        f"|launchForce = {firearm_row.get('LaunchForce', '')}",
        f"|ammoCapacity = {firearm_row.get('AmmoCapacity', '')}",
        f"|roundsPerMinute = {firearm_row.get('RoundsPerMinute', '')}",
        f"|reloadTime = {firearm_row.get('ReloadTime', '')}",
        f"|weaponLoudness = {firearm_row.get('WeaponLoudness', '')}",
        "",
        f"|ChargeData_chargeSpeed = {firearm_row.get('ChargeData', {}).get('ChargeSpeed', '')}",
        f"|VisualData_visualRecoil = {firearm_row.get('VisualData', {}).get('VisualRecoil', '')}",
        f"|VisualData_aimFOVMultiplier = {firearm_row.get('VisualData', {}).get('AimFOVMultiplier', '')}",
        "",
        f"|ValidAmmoTypes = {wiki_parameter_text(record.get('ammo_type_name', ''))}",
        "",
        "<!-- D_WorkshopItems.json -->",
        *render_workshop_cost_lines("WorkshopItems_research", research_costs),
        *render_workshop_cost_lines("WorkshopItems_crafting", craft_costs),
        "}}",
    ]

    if record.get("description") and "description_section" in record.get("components", []):
        lines.extend(["==Description==", record["description"]])

    if "obtaining_section" in record.get("components", []):
        lines.append("==Obtaining==")
        if workshop_row:
            lines.append("* Workshop item: yes")
    if "obtaining_section" in record.get("components", []) and any(value != "" for value in research_costs.values()):
        lines.append(
            "* Research cost: "
            + ", ".join(
                f"{value} {label}"
                for label, value in [
                    ("Ren", research_costs.get("ren")),
                    ("Exotics", research_costs.get("exotics")),
                    ("Red Exotics", research_costs.get("redExotics")),
                    ("Biomass", research_costs.get("biomass")),
                ]
                if value != ""
            )
        )
    if "obtaining_section" in record.get("components", []) and any(value != "" for value in craft_costs.values()):
        lines.append(
            "* Crafting cost: "
            + ", ".join(
                f"{value} {label}"
                for label, value in [
                    ("Ren", craft_costs.get("ren")),
                    ("Exotics", craft_costs.get("exotics")),
                    ("Red Exotics", craft_costs.get("redExotics")),
                    ("Biomass", craft_costs.get("biomass")),
                ]
                if value != ""
            )
        )

    if valid_ammo_desc and "ammo_section" in record.get("components", []):
        lines.extend(["==Ammo==", f"* Valid ammo category: [[:Category:{valid_ammo_desc}|{valid_ammo_desc}]]"])

    if durable_row and "repair_section" in record.get("components", []):
        lines.extend(["==Repair==", f"* Max durability: {durable_row.get('Max_Durability', '')}"])
        if repair_items:
            lines.append("* Repair items: " + ", ".join(repair_items))

    lines.append("")
    return "\n".join(lines)


def render_item_generic(record: dict[str, Any]) -> str:
    itemable_name = itemable_template_name(record)
    lines = [
        *render_comment_block(record),
    ]

    lines.extend(
        [
            "{{" + anchor_template_name(record),
            "|NOINFOBOX = true",
            "|categories = ",
            "|techLevelUnlock = ",
            "|techLevelNeeded = ",
            "|prerequisite = ",
            "|prerequisiteMission = ",
            "|RequiredFeatureLevel = ",
            f"|Itemable_displayName = {wiki_parameter_text(record['display_name'])}",
            f"|Itemable_icon = {wiki_parameter_text(record['icon'])}.png",
            f"|Itemable_name = {wiki_parameter_text(itemable_name)}",
            f"|Itemable_flavorText = {wiki_parameter_text(record['flavor_text'])}",
            f"|Itemable_description = {wiki_parameter_text(record['description'])}",
            f"|Itemable_weight = {record['weight']}",
            f"|Itemable_maxStack = {record['max_stack']}",
            f"|Itemable_craftingExperience = {record['crafting_experience']}",
            f"|AdditionalStats = {wiki_parameter_text(', '.join(record.get('additional_stats', [])))}",
            f"|GameplayTags = {wiki_parameter_text(', '.join(record['gameplay_tags']))}",
            "}}",
        ]
    )

    if "resource_summary_template" in record.get("components", []):
        lines.extend(render_resource_summary_template(record))

    if "usage_section" in record.get("components", []):
        lines.append("==Usage==")
        lines.append("<!-- Add human-written gameplay usage notes when available. -->")

    crafted_in = record.get("crafted_in", [])
    used_in = record.get("used_in", [])

    if "crafting_section" in record.get("components", []) and (crafted_in or used_in):
        lines.append("==Crafting==")
        if crafted_in:
            lines.append("Crafted in:")
            for bench in crafted_in:
                lines.append(f"* {{{{Item icon|{bench}}}}}")
        if used_in:
            lines.append("Used in:")
            for title in used_in:
                lines.append(f"* {{{{Item icon|{title}}}}}")

    lines.append("")
    return "\n".join(lines)


def render_prospect_page(record: dict[str, Any]) -> str:
    lines = [
        *render_comment_block(record),
        "{{" + anchor_template_name(record),
        "  | title={{PAGENAME}}",
        f"  | image={wiki_parameter_text(record.get('image', ''))}.png",
        f"  | map=[[{wiki_parameter_text(record.get('map_name', ''))}]]",
        f"  | region=[[{wiki_parameter_text(record.get('biome', ''))}]]",
        f"  | difficulty={wiki_parameter_text(record.get('difficulty', ''))}",
        f"  | duration={wiki_parameter_text(record.get('duration', ''))}",
        f"  | required_tech={wiki_parameter_text(record.get('required_tech', ''))}",
        "}}",
    ]

    lead_bits = []
    if record.get("prospect_type") == "mission":
        lead_bits.append("[[Missions|Mission]]")
    elif record.get("prospect_type") == "outpost":
        lead_bits.append("[[Outposts|Outpost]]")
    if record.get("map_name"):
        lead_bits.append(f"on [[{record['map_name']}]]")
    if record.get("biome"):
        lead_bits.append(f"in the [[{record['biome']}|{record['biome']} Biome]]")
    if lead_bits and "lead_section" in record.get("components", []):
        lines.append("")
        lines.append(f"[[{{{{PAGENAME}}}}]] is a {' '.join(lead_bits)}.")

    if record.get("description") and "description_section" in record.get("components", []):
        lines.extend(["", "==Description==", record["description"]])

    if "mission_briefing" in record.get("components", []) and any(record.get(field) for field in ["operator", "biome", "background", "mission", "terms"]):
        lines.extend(
            [
                "",
                "{{Mission Briefing",
                f"|operator={wiki_parameter_text(record.get('operator', ''))}",
                f"|biome={wiki_parameter_text(record.get('biome', ''))}",
                f"|background={wiki_parameter_text(record.get('background', ''))}",
                f"|mission={wiki_parameter_text(record.get('mission', ''))}",
                f"|terms={wiki_parameter_text(record.get('terms', ''))}",
                "}}",
            ]
        )

    lines.append("")
    return "\n".join(lines)


def render_creature_page(record: dict[str, Any]) -> str:
    lines = [
        *render_comment_block(record),
        "{{" + anchor_template_name(record),
        "  | title1={{PAGENAME}}",
        f"  | image1={wiki_parameter_text(record.get('image', ''))}.png",
        "  | caption1=",
        "  | category=[[Category:Creatures]]",
        f"  | atmosphere={'<br>'.join(f'[[{biome}]]' for biome in record.get('biomes', []))}",
        f"  | region={'<br>'.join(f'[[{map_name}]]' for map_name in record.get('maps', []))}",
        "  | behavior=",
        "  | resource=",
        "  | tool=",
        "}}",
    ]

    if record.get("lore1") and "description_section" in record.get("components", []):
        lines.extend(["==Description==", record["lore1"]])
    if (record.get("lore2") or record.get("lore3")) and "lore_section" in record.get("components", []):
        lines.append("==Lore==")
        if record.get("lore2"):
            lines.append(record["lore2"])
        if record.get("lore3"):
            lines.append(record["lore3"])

    lines.append("")
    return "\n".join(lines)


def render_record(record: dict[str, Any], archetypes: dict[str, dict[str, Any]]) -> str:
    archetype_name = record.get("archetype", "")
    archetype = get_archetype_definition(
        archetypes,
        archetype_name,
        expected_kind=record.get("kind") or None,
    )
    renderer_name = archetype["renderer"]
    renderers = {
        "item_firearm_workshop": render_item_firearm_workshop,
        "item_generic": render_item_generic,
        "prospect": render_prospect_page,
        "creature": render_creature_page,
    }
    renderer = renderers.get(renderer_name)
    if not renderer:
        raise ValueError(
            f'Unsupported renderer "{renderer_name}" for record kind/archetype '
            f'{record.get("kind")} / {archetype_name}'
        )
    return renderer(record)


def render_item_stub(record: dict[str, Any], archetypes: dict[str, dict[str, Any]]) -> str:
    return render_record(record, archetypes)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def write_item_stubs(
    out_dir: Path,
    records: list[dict[str, Any]],
    only_missing: bool,
    archetypes: dict[str, dict[str, Any]],
) -> int:
    stub_dir = out_dir / "item_stubs"
    stub_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    for record in records:
        if only_missing and not record.get("is_missing", False):
            continue
        filename = safe_filename(record["page_title"]) + ".wiki"
        path = stub_dir / filename
        path.write_text(render_item_stub(record, archetypes), encoding="utf-8")
        written += 1
    return written


def find_record_by_title(
    manifests: dict[str, Any],
    title: str,
    render_kind: str | None,
) -> dict[str, Any]:
    matches = []
    for kind, payload in manifests.items():
        if render_kind and kind != render_kind:
            continue
        for record in payload["records"]:
            if comparable_title(record["page_title"]) == comparable_title(title):
                matches.append(record)

    if not matches:
        for kind, payload in manifests.items():
            if render_kind and kind != render_kind:
                continue
            for record in payload["records"]:
                if loose_title_key(record["page_title"]) == loose_title_key(title):
                    matches.append(record)

    if not matches:
        raise ValueError(f'No generated record found for title "{title}".')
    if len(matches) > 1:
        kinds = ", ".join(sorted({match["kind"] for match in matches}))
        raise ValueError(
            f'Multiple records matched "{title}" across {kinds}. Re-run with --render-kind.'
        )
    return matches[0]


def print_summary(kind: str, records: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    total = summary.get("total_pages", len(records))
    review_pages = summary.get("review_pages", 0)

    if "missing_pages" in summary:
        print(
            f"{kind}: {total} canonical pages, {summary['existing_pages']} existing, "
            f"{summary['missing_pages']} missing, {review_pages} needing review "
            f"({summary.get('missing_review_pages', 0)} of those missing)"
        )
    else:
        print(f"{kind}: {total} canonical pages, {review_pages} needing review")

    samples = [record["page_title"] for record in records if record.get("is_missing", True)][:10]
    if samples:
        print(f"  Sample titles: {', '.join(samples)}")

    if kind == "items" and summary.get("unresolved_keys"):
        unresolved = summary["unresolved_keys"][:10]
        print(f"  Unresolved item keys ({len(summary['unresolved_keys'])}): {', '.join(unresolved)}")


def main() -> None:
    args = parse_args()
    archetypes = load_archetype_config(args.archetype_config)

    live_titles: set[str] = set()
    existing_titles = load_existing_titles(args.existing_titles, args.existing_dir)
    if args.fetch_live_titles:
        live_titles = fetch_live_titles(args.wiki_api_url)
        existing_titles.update(live_titles)
        print(f"Fetched {len(live_titles)} live titles from {args.wiki_api_url}")
    if existing_titles:
        print(f"Loaded {len(existing_titles)} existing titles for comparison")

    manifests: dict[str, Any] = {}

    if "items" in args.kinds:
        item_records, item_summary = build_item_inventory(
            include_all_items=args.all_items,
            archetypes=archetypes,
        )
        if existing_titles:
            item_summary.update(annotate_existing_status(item_records, existing_titles))
        print_summary("items", item_records, item_summary)
        manifests["items"] = {"summary": item_summary, "records": item_records}

    if "prospects" in args.kinds:
        prospect_records, prospect_summary = build_prospect_inventory(archetypes=archetypes)
        if existing_titles:
            prospect_summary.update(annotate_existing_status(prospect_records, existing_titles))
        print_summary("prospects", prospect_records, prospect_summary)
        manifests["prospects"] = {"summary": prospect_summary, "records": prospect_records}

    if "creatures" in args.kinds:
        creature_records, creature_summary = build_creature_inventory(archetypes=archetypes)
        if existing_titles:
            creature_summary.update(annotate_existing_status(creature_records, existing_titles))
        print_summary("creatures", creature_records, creature_summary)
        manifests["creatures"] = {"summary": creature_summary, "records": creature_records}

    if args.summary_only:
        return

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for kind, payload in manifests.items():
        write_json(args.out_dir / f"{kind}.json", payload)

    if args.write_item_stubs and "items" in manifests:
        written = write_item_stubs(
            args.out_dir,
            manifests["items"]["records"],
            only_missing=args.only_missing and bool(existing_titles),
            archetypes=archetypes,
        )
        print(f"Wrote {written} item stub files to {args.out_dir / 'item_stubs'}")

    if args.render_title:
        record = find_record_by_title(manifests, args.render_title, args.render_kind)
        args.output_file.parent.mkdir(parents=True, exist_ok=True)
        args.output_file.write_text(render_record(record, archetypes), encoding="utf-8")
        print(
            f'Wrote {record["kind"]} page "{record["page_title"]}" '
            f'({record.get("archetype", "")}) to {args.output_file}'
        )


if __name__ == "__main__":
    main()
