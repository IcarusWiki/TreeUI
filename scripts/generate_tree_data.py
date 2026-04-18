#!/usr/bin/env python3
"""
Reads Icarus game JSON data files and generates Lua data modules
for the wiki tree UIs (Talent, Workshop, Blueprint, Creature, Prospect).

Usage:
    python scripts/generate_tree_data.py
    python scripts/generate_tree_data.py --in-dir .\InGameFiles --out-dir .\generated

Reads from the extracted pak data under InGameFiles/ and writes to generated/
"""

import argparse
import json
import re
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = ROOT / "InGameFiles"
DEFAULT_OUT_DIR = ROOT / "generated"
DATA_DIR = DEFAULT_DATA_DIR
OUT_DIR = DEFAULT_OUT_DIR
DATA_FILE_CACHE = {}


def resolve_cli_path(path):
    expanded = Path(path).expanduser()
    if not expanded.is_absolute():
        expanded = ROOT / expanded
    return expanded.resolve()


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Read extracted Icarus game JSON files and generate the Lua data "
            "modules used by the wiki tree UIs."
        )
    )
    parser.add_argument(
        "--in-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help=f"Directory containing extracted game data (default: {DEFAULT_DATA_DIR})",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=f"Directory to write generated Lua modules into (default: {DEFAULT_OUT_DIR})",
    )
    return parser.parse_args()


def configure_paths(args):
    global DATA_DIR, OUT_DIR

    DATA_DIR = resolve_cli_path(args.in_dir)
    OUT_DIR = resolve_cli_path(args.out_dir)
    DATA_FILE_CACHE.clear()

# ── Rendering constants (web-only, not in game data) ──────────────────────
# Game stride → web pixel mapping. These control how game coordinates
# translate to CSS pixels for each view type (keyed by D_TalentViews Name).
SCALE = {
    "Player":    0.45,
    "Solo":      0.45,
    "Workshop":  0.4,
    "Blueprint": 0.5,
    "Creature":  0.65,
    "Prospect":  0.25,
}

NODE_SIZE = {
    "Player":    56,
    "Solo":      56,
    "Workshop":  100,
    "Blueprint": 100,
    "Creature":  80,
    "Prospect":  150,
}

GENERIC_PROSPECT_WORLD_IMAGE = "T_Prospect_Forest_ExtractorExpedition01_Large"

MISSION_REGION_STYLE = {
    "olympus": {
        "selector_key": "Outpost006_Olympus",
        "selector_difficulty": 2,
        "selector_color": "#b9c890",
        "selector_image": "T_IMG_Terrain_Olypmus",
        "background_image": "T_IMG_TRBG_Olympus",
        "selector_description": (
            "First Cohort ground zero, where fortunes are found and lives are lost. "
            "Earth-like terrazone centered on a mountainous arctic region."
        ),
    },
    "styx": {
        "selector_key": "OpenWorld_Styx",
        "selector_difficulty": 3,
        "selector_color": "#c39637",
        "selector_image": "T_IMG_Terrain_Styx",
        "background_image": "T_IMG_TRBG_Styx",
        "selector_description": (
            "Repealed red zone, ripe for the taking. Earth-like terrazone centered "
            "around large braided rivers, teeming with wildlife."
        ),
    },
    "prometheus": {
        "selector_key": "OpenWorld_Prometheus",
        "selector_difficulty": 4,
        "selector_color": "#b94a24",
        "selector_image": "T_IMG_Terrain_Prometheus",
        "background_image": "T_IMG_TRBG_Prometheus",
        "selector_description": (
            "Previously accessible only to scientists, this perilous area and its "
            "wildlife has been warped by the violence of the terraforming process."
        ),
    },
    "elysium": {
        "selector_key": "OpenWorld_Elysium",
        "selector_difficulty": 4,
        "selector_color": "#b124d0",
        "selector_image": "T_IMG_Terrain_Elysium",
        "background_image": "T_IMG_TRBG_Elysium",
        "selector_description": (
            "An uncharted sector of Icarus only explored by select Group 15 agents. "
            "What secrets could be hiding in this deceptively beautiful landscape?"
        ),
    },
}

# Required rank → badge number. Would need D_TalentRanks.json to derive.
RANK_BADGES = {
    "Apprentice": 1,
    "Journeyman": 2,
    "Master":     3,
}

# ── Helpers ────────────────────────────────────────────────────────────────

def _static_fallback_keys(static_key, item_row):
    """Generate candidate D_ItemsStatic keys when the normal chain fails.

    Handles game data naming inconsistencies:
    - Extra _Armor_ segment in carbon armor refs
    - _Helmet_ vs _Head_ mismatch
    - Word-order swaps (workshop Name may be the correct key)
    - Meta_ prefix on refs where D_ItemsStatic lacks it
    - Missing underscore before trailing digits (e.g. Shengong2 → Shengong_2)
    - Extra underscore between compound words (World_Boss → WorldBoss)
    """
    seen = set()
    for key in [static_key, item_row]:
        if key in seen:
            continue
        candidates = [
            key,
            # Strip _Armor_ and fix _Helmet_ → _Head_
            key.replace("_Armor_", "_").replace("_Helmet_", "_Head_"),
            key.replace("_Armor_", "_"),
            key.replace("_Helmet_", "_Head_"),
        ]
        # Strip Meta_ prefix and try with/without _Alpha suffix
        if key.startswith("Meta_"):
            stripped = key[5:]
            candidates.append(stripped)
            candidates.append(stripped + "_Alpha")
        # Fix missing underscore before trailing digits
        candidates.append(re.sub(r'([a-zA-Z])(\d+)$', r'\1_\2', key))
        # Try joining last two underscore segments (World_Boss → WorldBoss)
        parts = key.split("_")
        if len(parts) >= 3:
            candidates.append("_".join(parts[:-2]) + "_" + parts[-2] + parts[-1])
        for c in candidates:
            if c not in seen:
                seen.add(c)
                yield c


def resolve_item(item_row, workshop_to_static, static_to_itemable,
                 itemable_icons, itemable_names):
    """Resolve an ExtraData.RowName through the full chain with fallbacks.

    Returns (icon, display_name, description).
    """
    # Fast path: item_row directly in D_Itemable
    if item_row in itemable_icons:
        info = itemable_names.get(item_row, {})
        return itemable_icons[item_row], info.get("display", ""), info.get("desc", "")

    # Normal chain: D_WorkshopItems → D_ItemsStatic → D_Itemable
    static_key = workshop_to_static.get(item_row, item_row)
    itemable_key = static_to_itemable.get(static_key, "")
    if itemable_key and itemable_key in itemable_icons:
        info = itemable_names.get(itemable_key, {})
        return itemable_icons[itemable_key], info.get("display", ""), info.get("desc", "")

    # Fallback: try variant keys in D_ItemsStatic
    for candidate in _static_fallback_keys(static_key, item_row):
        ik = static_to_itemable.get(candidate, "")
        if ik and ik in itemable_icons:
            info = itemable_names.get(ik, {})
            return itemable_icons[ik], info.get("display", ""), info.get("desc", "")

    # Last resort: try Item_ prefix directly in D_Itemable
    for key in [static_key, item_row]:
        if key.startswith("Meta_"):
            direct = "Item_" + key[5:]
            if direct in itemable_icons:
                info = itemable_names.get(direct, {})
                return itemable_icons[direct], info.get("display", ""), info.get("desc", "")

    return "", "", ""


def find_data_file(*path_parts):
    """Resolve a data file from the extracted pak directory tree."""
    if not path_parts:
        raise ValueError("find_data_file requires at least one path part")

    cache_key = tuple(str(part) for part in path_parts)
    cached = DATA_FILE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    if not DATA_DIR.is_dir():
        raise FileNotFoundError(
            f"Expected extracted game data under {DATA_DIR}, but that directory does not exist. "
            "Rebuild InGameFiles with scripts/rebuild_ingamefiles.py first."
        )

    filename = Path(path_parts[-1]).name
    candidates = sorted(DATA_DIR.rglob(filename))
    if not candidates:
        raise FileNotFoundError(
            f"Could not find {filename} anywhere under {DATA_DIR}. "
            "The extracted pak data appears to be incomplete."
        )

    if len(candidates) == 1:
        DATA_FILE_CACHE[cache_key] = candidates[0]
        return candidates[0]

    hints = [part.lower() for part in Path(*path_parts[:-1]).parts if part not in ("", ".")]
    if hints:
        scored = []
        for candidate in candidates:
            lowered_parts = [part.lower() for part in candidate.parts]
            score = sum(1 for hint in hints if hint in lowered_parts)
            scored.append((score, len(candidate.parts), str(candidate).lower(), candidate))
        scored.sort(key=lambda item: (-item[0], item[1], item[2]))

        best_score = scored[0][0]
        best_matches = [item[3] for item in scored if item[0] == best_score]
        if best_score > 0 and len(best_matches) == 1:
            DATA_FILE_CACHE[cache_key] = best_matches[0]
            return best_matches[0]

    candidate_list = "\n".join(f"  - {candidate}" for candidate in candidates)
    raise FileNotFoundError(
        f"Found multiple matches for {filename} under {DATA_DIR} and could not choose one:\n"
        f"{candidate_list}"
    )


def load_json(*path_parts):
    """Load a JSON file from the extracted pak directory tree."""
    path = find_data_file(*path_parts)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_nsloctext(value):
    """Extract English text from NSLOCTEXT(...) or INVTEXT(...)."""
    if not value or not isinstance(value, str):
        return ""
    for pattern in [
        r'NSLOCTEXT\([^,]+,\s*[^,]+,\s*"((?:[^"\\]|\\.)*)"\s*\)',
        r'INVTEXT\(\s*"((?:[^"\\]|\\.)*)"\s*\)',
    ]:
        m = re.search(pattern, value)
        if m:
            text = m.group(1)
        # Remove spurious backslash escapes from the game data (e.g. \' → ')
            text = text.replace("\\'", "'").replace('\\"', '"')
            return text
    return value


def clean_icon_path(icon_str):
    """
    Convert game icon path to wiki file name.
    "Texture2D /Game/.../T_Icon.T_Icon" → "T_Icon"
    "/Game/Assets/.../ITEM_Fibre.ITEM_Fibre" → "ITEM_Fibre"
    """
    if not icon_str or icon_str == "None":
        return ""
    # Strip Texture2D prefix if present
    icon_str = re.sub(r"^Texture2D\s*'?", "", icon_str)
    icon_str = icon_str.rstrip("'")
    # Take the part after the last /
    parts = icon_str.rsplit("/", 1)
    name_part = parts[-1] if len(parts) > 1 else parts[0]
    # Remove .DuplicateName suffix (e.g. "ITEM_Fibre.ITEM_Fibre" → "ITEM_Fibre")
    if "." in name_part:
        name_part = name_part.split(".")[0]
    return name_part


def lua_string(s):
    """Escape a string for Lua double-quoted string output."""
    s = str(s)
    s = s.replace("\\", "\\\\")  # escape backslashes first
    s = s.replace('"', '\\"')    # escape double quotes
    s = s.replace("\n", "\\n")   # escape newlines
    # Single quotes don't need escaping in Lua double-quoted strings
    return f'"{s}"'


def title_case_words(s):
    """Capitalize first letter of each space-separated word.

    Handles leading punctuation (e.g. "'em" → "'Em").
    """
    def cap_word(w):
        for i, c in enumerate(w):
            if c.isalpha():
                return w[:i] + c.upper() + w[i + 1:]
        return w
    return " ".join(cap_word(w) for w in s.split(" "))


def format_page_title(text):
    """Convert export labels like POTSHOT into wiki page titles."""
    text = clean_display_label(parse_nsloctext(text))
    if not text:
        return ""

    def repl(match):
        word = match.group(0)
        if word.isupper() or word.islower():
            return word[:1].upper() + word[1:].lower()
        return word

    return re.sub(r"[A-Za-z]+(?:'[A-Za-z]+)?", repl, text)


def color_to_css(color):
    """Convert exported RGBA colors into #rrggbb strings."""
    if not isinstance(color, dict):
        return ""

    alpha = int(color.get("A", 0) or 0)
    if alpha <= 0:
        return ""

    red = max(0, min(255, int(color.get("R", 0) or 0)))
    green = max(0, min(255, int(color.get("G", 0) or 0)))
    blue = max(0, min(255, int(color.get("B", 0) or 0)))
    return f"#{red:02x}{green:02x}{blue:02x}"


def normalize_lookup_key(value):
    """Normalize a user-facing label/tree name into a lookup key."""
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def format_tech_tier_label(tree_name):
    """Convert Blueprint_T3_Machine -> Tier 3."""
    match = re.search(r"Blueprint_T(\d+)_", tree_name or "")
    if match:
        return f"Tier {match.group(1)}"
    return (tree_name or "").replace("Blueprint_", "").replace("_", " ")


def format_tier_short(req_tech):
    """Convert Tier3 -> T3."""
    match = re.search(r"Tier(\d+)", str(req_tech or ""))
    if match:
        return f"T{match.group(1)}"
    return str(req_tech or "")


def format_enum_label(value):
    """Convert DangerousHorizons -> Dangerous Horizons."""
    value = str(value or "").strip()
    if not value or value == "None":
        return ""
    value = value.replace("_", " ")
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    return value.strip()


def format_mission_fallback_name(node_name, prospect_key=""):
    """Derive a readable fallback mission name from internal IDs."""
    if prospect_key == "None":
        prospect_key = ""
    raw = prospect_key or node_name or ""
    raw = re.sub(r"^Prospect_", "", raw)
    raw = re.sub(r"^(OLY|PRO|ELY|STYX)_", "", raw)
    raw = re.sub(r"^Tier\d+_", "", raw)
    raw = raw.replace("_", " ").strip()
    return title_case_words(raw) if raw else node_name


def extract_duration_parts(duration):
    """Extract a stable Days/Hours/Mins tuple from TimeDuration."""
    duration = duration or {}

    def pick(part):
        part_info = duration.get(part, {}) or {}
        if isinstance(part_info, dict):
            minimum = part_info.get("Min", 0)
            maximum = part_info.get("Max", minimum)
            if minimum == maximum:
                return int(minimum or 0)
            return int(minimum or 0)
        return int(part_info or 0)

    return pick("Days"), pick("Hours"), pick("Mins")


def format_duration_label(duration):
    """Format TimeDuration like 07 00 00 (days hours mins)."""
    days, hours, mins = extract_duration_parts(duration)
    return f"{days:02d} {hours:02d} {mins:02d}"


def parse_flavor_sections(text):
    """Parse structured flavour text sections like //BIOME: Forest."""
    sections = {}
    if not text:
        return sections

    pattern = re.compile(r"//\s*([A-Z]+):\s*(.*?)(?=(?:\s*//\s*[A-Z]+:)|\s*$)")
    for key, value in pattern.findall(text):
        sections[key.lower()] = value.strip()
    return sections


def clean_display_label(text):
    """Remove common export markers from user-facing labels."""
    text = str(text or "").replace("[DNT]", "").strip()
    return re.sub(r"\s+", " ", text)


def get_selector_description(prospect):
    """Pick the most useful world-selector description from a prospect row."""
    if not prospect:
        return ""

    for key in ["background", "flavour", "description"]:
        value = clean_display_label(prospect.get(key, ""))
        if value:
            return value
    return ""


def get_region_style(theme_key):
    """Return the curated selector styling block for a mission region theme."""
    return MISSION_REGION_STYLE.get(normalize_lookup_key(theme_key), {})


def extract_stat_name(key):
    """Parse '(Value=\"BaseBowProjectileAccuracy_+%\")' into a D_Stats row name."""
    m = re.search(r'Value="([^"]+)"', str(key or ""))
    if m:
        return m.group(1)
    return str(key or "")


def humanize_stat_name(stat_name):
    """Fallback humanization for stats that lack localized display strings."""
    stat_name = str(stat_name or "")
    stat_name = re.sub(r"^Base", "", stat_name)
    stat_name = re.sub(r"_[+%?-]+$", "", stat_name)
    stat_name = re.sub(r"Alt$", "", stat_name)
    stat_name = stat_name.replace("_", " ")
    stat_name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", stat_name)
    stat_name = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", stat_name)
    return re.sub(r"\s+", " ", stat_name).strip()


def build_stats_lookup(stats_rows):
    """Build a localized stat description lookup from D_Stats."""
    lookup = {}
    for row in stats_rows:
        name = row.get("Name", "")
        if not name:
            continue
        lookup[name] = {
            "title": parse_nsloctext(row.get("Title", "")),
            "positive_title_format": parse_nsloctext(row.get("PositiveTitleFormat", "")),
            "negative_title_format": parse_nsloctext(row.get("NegativeTitleFormat", "")),
            "positive_description": parse_nsloctext(row.get("PositiveDescription", "")),
            "negative_description": parse_nsloctext(row.get("NegativeDescription", "")),
            "display_operations": row.get("DisplayOperations", []) or [],
        }
    return lookup


def apply_display_operations(value, operations):
    """Apply D_Stats display operations before inserting values into strings."""
    result = float(value)
    for operation in operations or []:
        kind = operation.get("Operation", "")
        operand = operation.get("Value", 0)
        if kind == "Addition":
            result += operand
        elif kind == "Subtraction":
            result -= operand
        elif kind == "Multiplication":
            result *= operand
        elif kind == "Division" and operand:
            result /= operand
    return result


def format_display_number(value):
    """Format numbers for localized display strings."""
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:g}"
    return str(value)


def fill_display_template(template, value):
    """Substitute {0} placeholders in localized stat templates."""
    return str(template or "").replace("{0}", format_display_number(value)).strip()


def is_simple_value_format(template):
    """Detect compact value formats like '+{0}%' or '{0}cm'."""
    return bool(re.fullmatch(r"\s*[+\-xX]?\{0\}(?:[%a-zA-Z°]+)?\s*", str(template or "")))


def format_granted_flag(flag, stats_lookup):
    """Best-effort formatting for GrantedFlags entries."""
    if isinstance(flag, dict):
        row_name = flag.get("RowName", "")
        if not row_name or row_name == "None":
            return ""
        if row_name in stats_lookup:
            return format_stat_modifier(row_name, 1, stats_lookup)
        return humanize_stat_name(row_name)
    if not flag:
        return ""
    flag_name = str(flag)
    if flag_name in stats_lookup:
        return format_stat_modifier(flag_name, 1, stats_lookup)
    return humanize_stat_name(flag_name)


def format_stat_modifier(stat_key, value, stats_lookup):
    """Localize a reward stat from D_Stats, with humanized fallback."""
    stat_name = extract_stat_name(stat_key)
    if not stat_name:
        return ""

    info = stats_lookup.get(stat_name, {})
    display_value = apply_display_operations(value, info.get("display_operations", []))
    use_positive = display_value >= 0
    abs_value = abs(display_value)

    description = info.get("positive_description" if use_positive else "negative_description", "")
    if description:
        return fill_display_template(description, abs_value)

    title = info.get("title", "")
    title_format = info.get("positive_title_format" if use_positive else "negative_title_format", "")

    if stat_name.endswith("_?"):
        if title_format and " " in title_format.strip():
            return fill_display_template(title_format, abs_value)
        if title:
            return title
        if title_format:
            return fill_display_template(title_format, abs_value)

    if title and title_format:
        value_text = fill_display_template(title_format, abs_value)
        if is_simple_value_format(title_format):
            return f"{title} {value_text}".strip()
        return value_text

    if title:
        sign = "+" if display_value > 0 else ""
        return f"{title} {sign}{format_display_number(display_value)}".strip()

    sign = "+" if display_value > 0 else ""
    return f"{humanize_stat_name(stat_name)} {sign}{format_display_number(display_value)}".strip()


def format_stat_key(key):
    return humanize_stat_name(extract_stat_name(key))
    """Parse stat key like '(Value="BaseBowProjectileAccuracy_+%")' → readable."""
    m = re.search(r'Value="([^"]+)"', key)
    if m:
        raw = m.group(1)
        # Make human-readable: BaseBowProjectileAccuracy_+% → Bow Projectile Accuracy +%
        raw = raw.replace("Base", "").replace("_", " ").strip()
        return raw
    return key


def build_levels_string(rewards, stats_lookup):
    """Build a multi-line localized levels string from Rewards array."""
    if not rewards or len(rewards) <= 1:
        return ""
    parts = []
    for reward in rewards:
        stats = reward.get("GrantedStats", {}) or {}
        flags = reward.get("GrantedFlags", []) or []
        desc_parts = []
        for key, val in stats.items():
            text = format_stat_modifier(key, val, stats_lookup)
            if text:
                desc_parts.append(text)
        for flag in flags:
            text = format_granted_flag(flag, stats_lookup)
            if text:
                desc_parts.append(text)
        if desc_parts:
            parts.append(", ".join(desc_parts))
    return "\\n".join(parts) if parts else ""


# ── Main processing ───────────────────────────────────────────────────────

def main():
    args = parse_args()
    configure_paths(args)

    print("Loading game data files...")
    print(f"  Input directory: {DATA_DIR}")
    print(f"  Output directory: {OUT_DIR}")
    talents_data = load_json("Talents", "D_Talents.json")
    trees_data = load_json("Talents", "D_TalentTrees.json")
    views_data = load_json("Talents", "D_TalentViews.json")
    archetypes_data = load_json("Talents", "D_TalentArchetypes.json")
    itemable_data = load_json("Traits", "D_Itemable.json")
    items_static_data = load_json("Items", "D_ItemsStatic.json")
    workshop_items_data = load_json("MetaWorkshop", "D_WorkshopItems.json")
    stats_data = load_json("Stats", "D_Stats.json")
    prospect_list_data = load_json("Prospects", "D_ProspectList.json")
    faction_missions_data = load_json("Factions", "D_FactionMissions.json")
    mission_types_data = load_json("Factions", "D_MissionTypes.json")
    meta_currency_data = load_json("Currency", "D_MetaCurrency.json")
    character_flags_data = load_json("Flags", "D_CharacterFlags.json")

    # ── Build archetype lookup from D_TalentArchetypes ─────────────────────
    # archetype_info: name → { model, display_name, icon, row_index }
    # model_archetypes: model → [archetype_names in row order]
    archetype_info = {}
    model_archetypes = {}
    for i, row in enumerate(archetypes_data.get("Rows", [])):
        name = row.get("Name", "")
        model = row.get("Model", {}).get("RowName", "None")
        display = parse_nsloctext(row.get("DisplayName", ""))
        icon = clean_icon_path(row.get("Icon", "None"))
        if name:
            archetype_info[name] = {
                "model": model,
                "display_name": display or name,
                "icon": icon,
                "row_index": i,
                "metadata": row.get("Metadata", {}) or {},
            }
            if model not in model_archetypes:
                model_archetypes[model] = []
            model_archetypes[model].append(name)

    # ── Build view lookup from D_TalentViews ───────────────────────────────
    # view_info: view_name → { line_method }
    view_info = {}
    for row in views_data.get("Rows", []):
        name = row.get("Name", "")
        line_method = row.get("LineMethod", "Unspecified")
        if line_method == "Unspecified":
            line_method = "YThenX"  # Default fallback
        if name:
            view_info[name] = {"line_method": line_method}

    # ── Derive orderings from game data ────────────────────────────────────

    # Talent tab order: archetypes with Model=="Player", row order = display order
    # Each archetype's DisplayName is the tab label.
    player_archetypes = model_archetypes.get("Player", [])
    talent_tab_map = {}    # archetype_name → display_name (tab label)
    talent_tab_order = []  # display_names in order
    for arch_name in player_archetypes:
        info = archetype_info[arch_name]
        tab_label = info["display_name"]
        talent_tab_map[arch_name] = tab_label
        talent_tab_order.append(tab_label)
    # Append Solo
    if "Solo" in archetype_info:
        talent_tab_map["Solo"] = archetype_info["Solo"]["display_name"]
        talent_tab_order.append(archetype_info["Solo"]["display_name"])

    # Tech tree order: archetypes with Model=="Blueprint", row order = tier order
    tech_archetype_order = model_archetypes.get("Blueprint", [])

    # Workshop archetype order: archetypes with Model=="Workshop", row order
    workshop_archetype_order = model_archetypes.get("Workshop", [])

    # Creature archetype order: archetypes with Model=="Creature", row order
    creature_archetype_order = model_archetypes.get("Creature", [])

    # Prospect tree order: archetypes with Model=="Prospect", excluding the dev-only tree
    prospect_archetype_order = [
        name for name in model_archetypes.get("Prospect", [])
        if name != "Prospect_Dev"
    ]

    print(f"  Talent tabs: {talent_tab_order}")
    print(f"  Blueprint tiers: {len(tech_archetype_order)}")
    print(f"  Workshop categories: {len(workshop_archetype_order)}")
    print(f"  Creature animals: {len(creature_archetype_order)}")
    print(f"  Prospect regions: {len(prospect_archetype_order)}")

    # ── Build itemable icon/name lookup (keyed by D_Itemable.Name) ─────────
    itemable_icons = {}
    itemable_names = {}
    for row in itemable_data.get("Rows", []):
        name = row.get("Name", "")
        icon = row.get("Icon", "None")
        display = parse_nsloctext(row.get("DisplayName", ""))
        desc = parse_nsloctext(row.get("Description", ""))
        if name:
            itemable_icons[name] = clean_icon_path(icon)
            itemable_names[name] = {"display": display, "desc": desc}

    # Build D_ItemsStatic bridge: static Name → Itemable.RowName
    static_to_itemable = {}
    for row in items_static_data.get("Rows", []):
        name = row.get("Name", "")
        itemable_ref = row.get("Itemable", {}).get("RowName", "None")
        if name and itemable_ref != "None":
            static_to_itemable[name] = itemable_ref

    # Build D_WorkshopItems bridge: workshop Name → Item.RowName (D_ItemsStatic key)
    workshop_to_static = {}
    for row in workshop_items_data.get("Rows", []):
        name = row.get("Name", "")
        item_ref = row.get("Item", {}).get("RowName", "None")
        if name and item_ref != "None":
            workshop_to_static[name] = item_ref
    stat_lookup = build_stats_lookup(stats_data.get("Rows", []))

    mission_type_info = {}
    for row in mission_types_data.get("Rows", []):
        name = row.get("Name", "")
        if not name:
            continue
        label = clean_display_label(parse_nsloctext(row.get("DisplayText", "")))
        mission_type_info[name] = {
            "label": label or format_enum_label(name),
            "icon": clean_icon_path(row.get("Icon", "None")),
        }

    meta_currency_info = {}
    for row in meta_currency_data.get("Rows", []):
        name = row.get("Name", "")
        if not name:
            continue
        label = clean_display_label(parse_nsloctext(row.get("DisplayName", "")))
        decorator = clean_display_label(parse_nsloctext(row.get("DecoratorText", "")))
        meta_currency_info[name] = {
            "label": label or decorator or format_enum_label(name),
            "icon": clean_icon_path(row.get("Icon", "None")),
            "decorator": decorator,
            "color": color_to_css(row.get("Color", {})),
        }

    character_flag_descriptions = {}
    for row in character_flags_data.get("Rows", []):
        flag_name = row.get("Name", "")
        if not flag_name:
            continue
        description = parse_nsloctext(row.get("Description", ""))
        if description:
            character_flag_descriptions[flag_name] = description

    faction_defaults = faction_missions_data.get("Defaults", {})
    faction_mission_info = {}
    for row in faction_missions_data.get("Rows", []):
        name = row.get("Name", "")
        if not name:
            continue

        mission_types = []
        for type_ref in row.get("Types", faction_defaults.get("Types", [])) or []:
            type_name = type_ref.get("RowName", "None")
            if not type_name or type_name == "None":
                continue
            type_info = mission_type_info.get(type_name, {})
            mission_types.append({
                "name": type_name,
                "label": type_info.get("label", format_enum_label(type_name)),
                "icon": type_info.get("icon", ""),
            })

        currency_rewards = []
        for reward in row.get("CurrencyRewarded", faction_defaults.get("CurrencyRewarded", [])) or []:
            meta_name = reward.get("Meta", {}).get("RowName", "None")
            amount = reward.get("Amount", 0)
            if not meta_name or meta_name == "None" or not amount:
                continue
            currency_info = meta_currency_info.get(meta_name, {})
            currency_rewards.append({
                "name": meta_name,
                "label": currency_info.get("label", format_enum_label(meta_name)),
                "icon": currency_info.get("icon", ""),
                "color": currency_info.get("color", ""),
                "amount": amount,
            })

        effect_text = ""
        flags_rewarded = row.get(
            "CharacterFlagsRewarded",
            faction_defaults.get("CharacterFlagsRewarded", []),
        ) or []
        for flag_ref in flags_rewarded:
            flag_name = (flag_ref or {}).get("RowName", "")
            if flag_name.endswith("_TooltipOnly"):
                description = character_flag_descriptions.get(flag_name, "")
                if description:
                    effect_text = description.strip()
                    break

        faction_mission_info[name] = {
            "types": mission_types,
            "currency_rewards": currency_rewards,
            "effect": effect_text,
            "account_experience": row.get(
                "AccountExperience",
                faction_defaults.get("AccountExperience", 0),
            ),
        }

    # ── Build tree lookup from D_TalentTrees ───────────────────────────────
    # tree_info: tree_name → { archetype, display, icon, model, row_index }
    # trees_by_archetype: archetype → [tree_names in row order]
    tree_info = {}
    trees_by_archetype = {}
    for i, row in enumerate(trees_data.get("Rows", [])):
        name = row.get("Name", "")
        archetype = row.get("Archetype", {}).get("RowName", "None")
        display = parse_nsloctext(row.get("DisplayName", ""))
        if not display:
            display = name.split("_", 1)[-1].replace("_", " ") if "_" in name else name
        icon = clean_icon_path(row.get("Icon", "None"))
        model = archetype_info.get(archetype, {}).get("model", "")
        tree_info[name] = {
            "archetype": archetype,
            "display": display,
            "icon": icon,
            "model": model,
            "row_index": i,
            "metadata": row.get("Metadata", {}) or {},
        }
        if archetype not in trees_by_archetype:
            trees_by_archetype[archetype] = []
        trees_by_archetype[archetype].append(name)

    # ── Process all talents ────────────────────────────────────────────────
    defaults = talents_data.get("Defaults", {})
    all_nodes = []
    all_connections = []

    for row in talents_data.get("Rows", []):
        node_name = row.get("Name", "")
        tree_name = row.get("TalentTree", defaults.get("TalentTree", {})).get("RowName", "None")

        if tree_name == "None" or not node_name:
            continue

        # Position
        pos = row.get("Position", defaults.get("Position", {"X": 0, "Y": 0}))
        x = pos.get("X", 0)
        y = pos.get("Y", 0)

        # Size (tells us what type of node: 128=talent, 184=blueprint, 250=workshop)
        size = row.get("Size", defaults.get("Size", {"X": 64, "Y": 64}))

        # TalentType: "Reroute" nodes are invisible line routing waypoints
        talent_type = row.get("TalentType", defaults.get("TalentType", ""))
        is_reroute = talent_type == "Reroute"

        # Display name and description
        display_name = parse_nsloctext(row.get("DisplayName", ""))
        description = parse_nsloctext(row.get("Description", ""))

        # Icon resolution
        icon = ""
        direct_icon = row.get("Icon", defaults.get("Icon", "None"))
        extra_data = row.get("ExtraData", defaults.get("ExtraData", {}))

        if direct_icon and direct_icon != "None":
            icon = clean_icon_path(direct_icon)
        elif extra_data.get("RowName", "None") != "None":
            item_row = extra_data["RowName"]
            resolved_icon, resolved_name, resolved_desc = resolve_item(
                item_row, workshop_to_static, static_to_itemable,
                itemable_icons, itemable_names
            )
            icon = resolved_icon
            if not display_name and resolved_name:
                display_name = resolved_name
            if not description and resolved_desc:
                description = resolved_desc

        # Required rank
        req_rank = row.get("RequiredRank", defaults.get("RequiredRank", {}))
        rank_name = req_rank.get("RowName", "None") if isinstance(req_rank, dict) else "None"
        rank_level = RANK_BADGES.get(rank_name, 0)

        # Levels from Rewards
        rewards = row.get("Rewards", defaults.get("Rewards", []))
        levels = build_levels_string(rewards, stat_lookup)

        # DrawMethodOverride
        draw_override = row.get("DrawMethodOverride", defaults.get("DrawMethodOverride", "Unspecified"))

        # Required talents → connections
        required = row.get("RequiredTalents", defaults.get("RequiredTalents", []))
        for req in required:
            req_name = req.get("RowName", "None")
            if req_name != "None":
                all_connections.append({
                    "from": req_name,
                    "to": node_name,
                    "draw_override": draw_override,
                })

        all_nodes.append({
            "name": node_name,
            "tree": tree_name,
            "display_name": display_name,
            "description": description,
            "icon": icon,
            "x": x,
            "y": y,
            "size_x": size.get("X", 64),
            "size_y": size.get("Y", 64),
            "levels": levels,
            "draw_override": draw_override,
            "reroute": is_reroute,
            "rank": rank_level,
            "extra_row": extra_data.get("RowName", ""),
            "extra_table": extra_data.get("DataTableName", ""),
            "feature_level": (row.get("Metadata", {}) or {}).get("RequiredFeatureLevel", {}).get("RowName", ""),
            "default_unlocked": bool(row.get("bDefaultUnlocked", False)),
        })

    print(f"Parsed {len(all_nodes)} nodes, {len(all_connections)} connections")

    # ── Group nodes by view type (using Model from archetype chain) ──────
    talent_nodes = []    # Player view
    solo_nodes = []      # Solo view
    workshop_nodes = []  # Workshop view
    blueprint_nodes = [] # Blueprint view
    creature_nodes = []  # Creature view
    prospect_nodes = []  # Prospect view

    for node in all_nodes:
        tree = node["tree"]
        info = tree_info.get(tree, {})
        model = info.get("model", "")
        archetype = info.get("archetype", "")

        if model == "Workshop":
            workshop_nodes.append(node)
        elif model == "Blueprint":
            blueprint_nodes.append(node)
        elif model == "Creature":
            creature_nodes.append(node)
        elif model == "Prospect":
            prospect_nodes.append(node)
        elif model == "Solo":
            solo_nodes.append(node)
        elif model == "Player" and archetype in talent_tab_map:
            node["tab"] = talent_tab_map[archetype]
            talent_nodes.append(node)

    print(f"  Talent nodes: {len(talent_nodes)}")
    print(f"  Solo nodes: {len(solo_nodes)}")
    print(f"  Workshop nodes: {len(workshop_nodes)}")
    print(f"  Blueprint nodes: {len(blueprint_nodes)}")
    print(f"  Creature nodes: {len(creature_nodes)}")
    print(f"  Prospect nodes: {len(prospect_nodes)}")

    prospect_defaults = prospect_list_data.get("Defaults", {})
    prospect_info = {}
    for row in prospect_list_data.get("Rows", []):
        name = row.get("Name", "")
        if not name:
            continue

        drop_name = parse_nsloctext(row.get("DropName", ""))
        description = parse_nsloctext(row.get("Description", ""))
        flavour = parse_nsloctext(row.get("FlavourText", ""))
        flavour_sections = parse_flavor_sections(flavour)
        faction_mission = row.get(
            "FactionMission",
            prospect_defaults.get("FactionMission", {}),
        )
        terrain = row.get(
            "Terrain",
            prospect_defaults.get("Terrain", {}),
        )
        time_duration = row.get(
            "TimeDuration",
            prospect_defaults.get("TimeDuration", {}),
        )
        prospect_info[name] = {
            "name": name,
            "drop_name": drop_name,
            "description": description,
            "flavour": flavour,
            "prospect_image": clean_icon_path(row.get("ProspectImage", "None")),
            "difficulty": row.get("Difficulty", prospect_defaults.get("Difficulty", "Easy")),
            "required_tech": row.get("RequiredTech", ""),
            "time_duration": time_duration,
            "time_label": format_duration_label(time_duration),
            "availability": row.get("OnProspectAvailability", ""),
            "feature_level": (row.get("Metadata", {}) or {}).get("RequiredFeatureLevel", {}).get("RowName", ""),
            "terrain": terrain.get("RowName", ""),
            "faction_mission": faction_mission.get("RowName", ""),
            "mission_types": faction_mission_info.get(faction_mission.get("RowName", ""), {}).get("types", []),
            "currency_rewards": faction_mission_info.get(faction_mission.get("RowName", ""), {}).get("currency_rewards", []),
            "effect": faction_mission_info.get(faction_mission.get("RowName", ""), {}).get("effect", ""),
            "operator": flavour_sections.get("operator", ""),
            "biome": flavour_sections.get("biome", ""),
            "mission": flavour_sections.get("mission", ""),
            "background": flavour_sections.get("background", ""),
            "terms": flavour_sections.get("terms", ""),
        }

    # ── Generate Lua data files ──────────────────────────────────────────
    generate_talent_data(
        talent_nodes, solo_nodes, all_connections, tree_info,
        talent_tab_order, trees_by_archetype, talent_tab_map, view_info,
        archetype_info,
    )
    generate_animal_talent_data(
        creature_nodes, all_connections, tree_info,
        creature_archetype_order, archetype_info, trees_by_archetype, view_info,
    )
    generate_workshop_data(
        workshop_nodes, all_connections, tree_info,
        workshop_archetype_order, archetype_info, trees_by_archetype, view_info,
    )
    generate_tech_data(
        blueprint_nodes, all_connections, tree_info,
        tech_archetype_order, archetype_info, trees_by_archetype, view_info,
    )
    generate_mission_data(
        prospect_nodes, all_connections, tree_info,
        prospect_archetype_order, archetype_info, trees_by_archetype,
        view_info, prospect_info,
    )

    print(f"\nDone! Files written to {OUT_DIR}")


def normalize_tree_positions(nodes_by_tree, scale, node_size, padding=10,
                             use_node_dimensions=False):
    """
    Normalize positions per tree: subtract min coords so each tree starts
    near (padding, padding). Mutates nodes in-place by setting 'sx' and 'sy'.
    Returns dict of tree_name → {canvas_w, canvas_h}.
    """
    tree_dims = {}
    for tree_name, nodes in nodes_by_tree.items():
        if not nodes:
            tree_dims[tree_name] = {"canvas_w": 0, "canvas_h": 0}
            continue
        min_x = min(n["x"] for n in nodes)
        min_y = min(n["y"] for n in nodes)
        max_edge_x = 0
        max_edge_y = 0
        for n in nodes:
            n["sx"] = round((n["x"] - min_x) * scale) + padding
            n["sy"] = round((n["y"] - min_y) * scale) + padding

            if use_node_dimensions:
                n["sw"] = max(0, round(n.get("size_x", node_size) * scale))
                n["sh"] = max(0, round(n.get("size_y", node_size) * scale))
            else:
                n["sw"] = node_size
                n["sh"] = node_size

            max_edge_x = max(max_edge_x, n["sx"] + n["sw"])
            max_edge_y = max(max_edge_y, n["sy"] + n["sh"])
        tree_dims[tree_name] = {
            "canvas_w": max_edge_x + padding,
            "canvas_h": max_edge_y + padding,
        }
    return tree_dims


def generate_talent_data(talent_nodes, solo_nodes, all_connections, tree_info,
                         tab_order, trees_by_archetype, talent_tab_map, view_info,
                         archetype_info):
    """Generate TalentData.lua module."""
    scale = SCALE["Player"]
    node_size = NODE_SIZE["Player"]

    # Invert talent_tab_map: tab_label → archetype_name
    tab_to_archetype = {v: k for k, v in talent_tab_map.items()}

    # Group talent nodes by tab → tree → nodes
    tabs = {}
    for node in talent_nodes:
        tab = node.get("tab", "")
        tree = node["tree"]
        if tab not in tabs:
            tabs[tab] = {}
        if tree not in tabs[tab]:
            tabs[tab][tree] = []
        tabs[tab][tree].append(node)

    # Add solo as its own tab
    solo_tab_label = talent_tab_map.get("Solo", "Solo")
    solo_trees = {}
    for node in solo_nodes:
        tree = node["tree"]
        if tree not in solo_trees:
            solo_trees[tree] = []
        solo_trees[tree].append(node)
    if solo_trees:
        tabs[solo_tab_label] = solo_trees

    # Normalize positions per tree (each tree gets its own canvas)
    all_trees = {}
    for tab_trees in tabs.values():
        all_trees.update(tab_trees)
    tree_dims = normalize_tree_positions(all_trees, scale, node_size)

    # Build connection set (only talent/solo connections)
    talent_node_names = {n["name"] for n in talent_nodes + solo_nodes}
    talent_connections = [
        c for c in all_connections
        if c["from"] in talent_node_names and c["to"] in talent_node_names
    ]

    lines = []
    lines.append("-- Auto-generated from game data by scripts/generate_tree_data.py")
    lines.append("-- Do not edit manually. Re-run the script to update.")
    lines.append("local data = {}")
    lines.append("")

    # ── Tab order (Lua array, excludes Solo which is handled separately) ──
    main_tab_order = [t for t in tab_order if tab_to_archetype.get(t, t) != "Solo"]
    lines.append("data.tab_order = {")
    for tab_name in main_tab_order:
        lines.append(f"    \"{tab_name}\",")
    lines.append("}")
    lines.append("")

    # ── Views table ───────────────────────────────────────────────────────
    lines.append("data.views = {")
    for tab_name in tab_order:
        if tab_name not in tabs:
            continue
        tab_trees = tabs[tab_name]
        # Tree order from D_TalentTrees row order, grouped by archetype
        archetype = tab_to_archetype.get(tab_name, tab_name)
        tree_names = [t for t in trees_by_archetype.get(archetype, []) if t in tab_trees]
        if not tree_names:
            tree_names = sorted(tab_trees.keys())

        # Line method from D_TalentViews via the archetype's Model
        model = "Solo" if archetype == "Solo" else "Player"
        lm = view_info.get(model, {}).get("line_method", "YThenX")

        # Archetype icon
        arch_icon = archetype_info.get(archetype, {}).get("icon", "")

        lines.append(f"    {tab_name} = {{")
        lines.append(f"        label = {lua_string(tab_name)},")
        lines.append(f"        icon = {lua_string(arch_icon)},")
        trees_lua = ", ".join(f'"{t}"' for t in tree_names)
        lines.append(f"        trees = {{ {trees_lua} }},")
        lines.append(f"        line_method = \"{lm}\",")
        lines.append(f"        node_size = {node_size},")

        # Per-tree dimensions
        lines.append("        tree_info = {")
        for t in tree_names:
            info = tree_info.get(t, {})
            display = info.get("display", t)
            icon = info.get("icon", "")
            dims = tree_dims.get(t, {"canvas_w": 0, "canvas_h": 0})
            lines.append(f"            [\"{t}\"] = {{ label = {lua_string(display)}, icon = {lua_string(icon)}, canvas_w = {dims['canvas_w']}, canvas_h = {dims['canvas_h']} }},")
        lines.append("        },")

        lines.append("    },")
    lines.append("}")
    lines.append("")

    # ── Nodes table ───────────────────────────────────────────────────────
    lines.append("data.nodes = {")
    for node in sorted(talent_nodes + solo_nodes, key=lambda n: (n["tree"], n["name"])):
        # Wiki icon: prefer game-declared icon, fall back to "Talent <Name>"
        display = node["display_name"]
        is_reroute = node.get("reroute", False)
        if node["icon"]:
            wiki_icon = node["icon"]
        elif display:
            wiki_icon = f"Talent {title_case_words(display)}"
        else:
            wiki_icon = ""
        lines.append(f"    [\"{node['name']}\"] = {{")
        lines.append(f"        name = {lua_string(display)},")
        lines.append(f"        icon = {lua_string(wiki_icon)},")
        lines.append(f"        tree = \"{node['tree']}\",")
        lines.append(f"        x = {node['sx']},")
        lines.append(f"        y = {node['sy']},")
        if node["description"]:
            lines.append(f"        desc = {lua_string(node['description'])},")
        if node["levels"]:
            lines.append(f"        levels = {lua_string(node['levels'])},")
        if node.get("rank", 0) > 0:
            lines.append(f"        rank = {node['rank']},")
        if is_reroute:
            lines.append(f"        reroute = true,")
        lines.append("    },")
    lines.append("}")
    lines.append("")

    # ── Connections table ─────────────────────────────────────────────────
    lines.append("data.connections = {")
    for conn in sorted(talent_connections, key=lambda c: (c["from"], c["to"])):
        method = conn.get("draw_override", "Unspecified")
        if method and method != "Unspecified":
            lines.append(f"    {{ from = \"{conn['from']}\", to = \"{conn['to']}\", method = \"{method}\" }},")
        else:
            lines.append(f"    {{ from = \"{conn['from']}\", to = \"{conn['to']}\" }},")
    lines.append("}")
    lines.append("")
    lines.append("return data")

    # Write file
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "TalentData.lua"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  Wrote {out_path} ({len(lines)} lines)")


def generate_animal_talent_data(creature_nodes, all_connections, tree_info,
                                creature_archetype_order, archetype_info,
                                trees_by_archetype, view_info):
    """Generate AnimalTalentData.lua module from Creature trees."""
    scale = SCALE["Creature"]
    node_size = NODE_SIZE["Creature"]
    line_method = view_info.get("Creature", {}).get("line_method", "YThenX")

    trees = {}
    for node in creature_nodes:
        tree = node["tree"]
        if tree not in trees:
            trees[tree] = []
        trees[tree].append(node)

    tree_dims = normalize_tree_positions(trees, scale, node_size)

    creature_names = {n["name"] for n in creature_nodes}
    creature_connections = [
        c for c in all_connections
        if c["from"] in creature_names and c["to"] in creature_names
    ]

    ordered_trees = []
    for arch_name in creature_archetype_order:
        for t in trees_by_archetype.get(arch_name, []):
            if t in trees:
                ordered_trees.append(t)
    for t in trees:
        if t not in ordered_trees:
            ordered_trees.append(t)

    lookup = {}
    for tree_name in ordered_trees:
        info = tree_info.get(tree_name, {})
        arch = info.get("archetype", "")
        arch_display = archetype_info.get(arch, {}).get("display_name", "")
        label = (arch_display or info.get("display", tree_name)).strip()
        for candidate in [tree_name, arch, label]:
            key = normalize_lookup_key(candidate)
            if key and key not in lookup:
                lookup[key] = tree_name

    lines = []
    lines.append("-- Auto-generated from game data by scripts/generate_tree_data.py")
    lines.append("-- Do not edit manually. Re-run the script to update.")
    lines.append("local data = {}")
    lines.append("")

    lines.append("data.animal_order = {")
    for tree_name in ordered_trees:
        lines.append(f"    \"{tree_name}\",")
    lines.append("}")
    lines.append("")

    lines.append("data.animals = {")
    for tree_name in ordered_trees:
        info = tree_info.get(tree_name, {})
        arch = info.get("archetype", "")
        arch_display = archetype_info.get(arch, {}).get("display_name", "")
        label = (arch_display or info.get("display", tree_name.replace("Creature_", "").replace("_", " "))).strip()
        dims = tree_dims.get(tree_name, {"canvas_w": 0, "canvas_h": 0})
        lines.append(f"    [\"{tree_name}\"] = {{")
        lines.append(f"        label = {lua_string(label)},")
        lines.append(f"        line_method = \"{line_method}\",")
        lines.append(f"        node_size = {node_size},")
        lines.append(f"        canvas_w = {dims['canvas_w']},")
        lines.append(f"        canvas_h = {dims['canvas_h']},")
        lines.append("    },")
    lines.append("}")
    lines.append("")

    lines.append("data.lookup = {")
    for key in sorted(lookup.keys()):
        lines.append(f"    [\"{key}\"] = \"{lookup[key]}\",")
    lines.append("}")
    lines.append("")

    lines.append("data.nodes = {")
    for node in sorted(creature_nodes, key=lambda n: (n["tree"], n["name"])):
        display = (node["display_name"] or "").strip()
        is_reroute = node.get("reroute", False)
        if node["icon"]:
            wiki_icon = node["icon"]
        elif display:
            wiki_icon = f"Talent {title_case_words(display)}"
        else:
            wiki_icon = ""
        lines.append(f"    [\"{node['name']}\"] = {{")
        lines.append(f"        name = {lua_string(display)},")
        lines.append(f"        icon = {lua_string(wiki_icon)},")
        lines.append(f"        tree = \"{node['tree']}\",")
        lines.append(f"        x = {node['sx']},")
        lines.append(f"        y = {node['sy']},")
        if node["description"]:
            lines.append(f"        desc = {lua_string(node['description'])},")
        if node["levels"]:
            lines.append(f"        levels = {lua_string(node['levels'])},")
        if is_reroute:
            lines.append("        reroute = true,")
        lines.append("    },")
    lines.append("}")
    lines.append("")

    lines.append("data.connections = {")
    for conn in sorted(creature_connections, key=lambda c: (c["from"], c["to"])):
        method = conn.get("draw_override", "Unspecified")
        if method and method != "Unspecified":
            lines.append(f"    {{ from = \"{conn['from']}\", to = \"{conn['to']}\", method = \"{method}\" }},")
        else:
            lines.append(f"    {{ from = \"{conn['from']}\", to = \"{conn['to']}\" }},")
    lines.append("}")
    lines.append("")
    lines.append("return data")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "AnimalTalentData.lua"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  Wrote {out_path} ({len(lines)} lines)")


def generate_workshop_data(workshop_nodes, all_connections, tree_info,
                           workshop_archetype_order, archetype_info,
                           trees_by_archetype, view_info):
    """Generate WorkshopData.lua module."""
    scale = SCALE["Workshop"]
    node_size = NODE_SIZE["Workshop"]
    line_method = view_info.get("Workshop", {}).get("line_method", "XThenY")

    # Group by tree
    trees = {}
    for node in workshop_nodes:
        tree = node["tree"]
        if tree not in trees:
            trees[tree] = []
        trees[tree].append(node)

    # Normalize positions per tree
    tree_dims = normalize_tree_positions(trees, scale, node_size)

    # Build connection set
    workshop_names = {n["name"] for n in workshop_nodes}
    workshop_connections = [
        c for c in all_connections
        if c["from"] in workshop_names and c["to"] in workshop_names
    ]

    # Workshop category order: from D_TalentArchetypes row order (Model==Workshop)
    # Each archetype maps to one tree in D_TalentTrees
    ordered_trees = []
    for arch_name in workshop_archetype_order:
        for t in trees_by_archetype.get(arch_name, []):
            if t in trees:
                ordered_trees.append(t)
    # Append any trees not covered by archetypes (safety net)
    for t in trees:
        if t not in ordered_trees:
            ordered_trees.append(t)

    lines = []
    lines.append("-- Auto-generated from game data by scripts/generate_tree_data.py")
    lines.append("-- Do not edit manually. Re-run the script to update.")
    lines.append("local data = {}")
    lines.append("")

    # ── Categories table (ordered by D_TalentArchetypes row order) ────────
    lines.append("data.categories = {")
    for tree_name in ordered_trees:
        info = tree_info.get(tree_name, {})
        # Prefer archetype DisplayName for sidebar label
        arch = info.get("archetype", "")
        arch_display = archetype_info.get(arch, {}).get("display_name", "")
        display = arch_display or info.get("display", tree_name.replace("Workshop_", ""))
        icon = info.get("icon", "")
        dims = tree_dims.get(tree_name, {"canvas_w": 0, "canvas_h": 0})
        lines.append(f"    [\"{tree_name}\"] = {{")
        lines.append(f"        label = {lua_string(display)},")
        lines.append(f"        icon = {lua_string(icon)},")
        lines.append(f"        line_method = \"{line_method}\",")
        lines.append(f"        node_size = {node_size},")
        lines.append(f"        canvas_w = {dims['canvas_w']},")
        lines.append(f"        canvas_h = {dims['canvas_h']},")
        lines.append("    },")
    lines.append("}")
    lines.append("")

    # ── Category order (Lua array preserving game data row order) ─────────
    lines.append("data.category_order = {")
    for tree_name in ordered_trees:
        lines.append(f"    \"{tree_name}\",")
    lines.append("}")
    lines.append("")

    # ── Nodes table ───────────────────────────────────────────────────────
    lines.append("data.nodes = {")
    for node in sorted(workshop_nodes, key=lambda n: (n["tree"], n["name"])):
        display = node["display_name"]
        is_reroute = node.get("reroute", False)

        # Fallback: derive name from node ID (Workshop_Carbon_Arms_Alpha → Carbon Arms Alpha)
        if not display and not is_reroute:
            raw = node["name"]
            if raw.startswith("Workshop_"):
                raw = raw[len("Workshop_"):]
            display = raw.replace("_", " ")

        # Wiki icon: prefer game-declared icon, fall back to ITEM_<DisplayName>
        if node["icon"]:
            wiki_icon = node["icon"]
        elif display:
            clean = display.replace('"', '').replace(' ', '_')
            wiki_icon = f"ITEM_{clean}"
        else:
            wiki_icon = ""

        lines.append(f"    [\"{node['name']}\"] = {{")
        lines.append(f"        name = {lua_string(display)},")
        lines.append(f"        icon = {lua_string(wiki_icon)},")
        lines.append(f"        tree = \"{node['tree']}\",")
        lines.append(f"        x = {node['sx']},")
        lines.append(f"        y = {node['sy']},")
        if node["description"]:
            lines.append(f"        desc = {lua_string(node['description'])},")
        if is_reroute:
            lines.append(f"        reroute = true,")
        lines.append("    },")
    lines.append("}")
    lines.append("")

    # ── Connections table ─────────────────────────────────────────────────
    lines.append("data.connections = {")
    for conn in sorted(workshop_connections, key=lambda c: (c["from"], c["to"])):
        method = conn.get("draw_override", "Unspecified")
        if method and method != "Unspecified":
            lines.append(f"    {{ from = \"{conn['from']}\", to = \"{conn['to']}\", method = \"{method}\" }},")
        else:
            lines.append(f"    {{ from = \"{conn['from']}\", to = \"{conn['to']}\" }},")
    lines.append("}")
    lines.append("")
    lines.append("return data")

    # Write file
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "WorkshopData.lua"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  Wrote {out_path} ({len(lines)} lines)")


def generate_tech_data(tech_nodes, all_connections, tree_info,
                       tech_archetype_order, archetype_info,
                       trees_by_archetype, view_info):
    """Generate TechData.lua module from Blueprint trees."""
    scale = SCALE["Blueprint"]
    node_size = NODE_SIZE["Blueprint"]
    line_method = view_info.get("Blueprint", {}).get("line_method", "XThenY")

    # Tech tree order: from D_TalentArchetypes row order (Model==Blueprint)
    # Each blueprint archetype maps to one tree in D_TalentTrees
    ordered_trees = []
    for arch_name in tech_archetype_order:
        for t in trees_by_archetype.get(arch_name, []):
            ordered_trees.append(t)
    allowed_trees = set(ordered_trees)

    tech_nodes = [node for node in tech_nodes if node["tree"] in allowed_trees]

    trees = {}
    for node in tech_nodes:
        tree = node["tree"]
        if tree not in trees:
            trees[tree] = []
        trees[tree].append(node)

    tree_dims = normalize_tree_positions(trees, scale, node_size)

    tech_names = {n["name"] for n in tech_nodes}
    tech_connections = [
        c for c in all_connections
        if c["from"] in tech_names and c["to"] in tech_names
    ]

    # Filter to only trees that have nodes
    ordered_trees = [t for t in ordered_trees if t in trees]

    lines = []
    lines.append("-- Auto-generated from game data by scripts/generate_tree_data.py")
    lines.append("-- Do not edit manually. Re-run the script to update.")
    lines.append("local data = {}")
    lines.append("")

    lines.append("data.tiers = {")
    for index, tree_name in enumerate(ordered_trees, start=1):
        info = tree_info.get(tree_name, {})
        dims = tree_dims.get(tree_name, {"canvas_w": 0, "canvas_h": 0})
        # Tier label from archetype DisplayName (e.g. "Tier 1", "Tier 5")
        arch = info.get("archetype", "")
        arch_label = archetype_info.get(arch, {}).get("display_name", "")
        label = arch_label or format_tech_tier_label(tree_name)
        lines.append(f"    [\"{tree_name}\"] = {{")
        lines.append(f"        label = {lua_string(label)},")
        lines.append(f"        icon = {lua_string(info.get('icon', ''))},")
        lines.append(f"        tier = {index},")
        lines.append(f"        line_method = \"{line_method}\",")
        lines.append(f"        node_size = {node_size},")
        lines.append(f"        canvas_w = {dims['canvas_w']},")
        lines.append(f"        canvas_h = {dims['canvas_h']},")
        lines.append("    },")
    lines.append("}")
    lines.append("")

    # ── Tier order (Lua array preserving game data row order) ─────────────
    lines.append("data.tier_order = {")
    for tree_name in ordered_trees:
        lines.append(f"    \"{tree_name}\",")
    lines.append("}")
    lines.append("")

    lines.append("data.nodes = {")
    for node in sorted(tech_nodes, key=lambda n: (n["tree"], n["name"])):
        display = node["display_name"]
        is_reroute = node.get("reroute", False)

        if not display and not is_reroute:
            raw = re.sub(r"^T\d+_", "", node["name"])
            display = raw.replace("_", " ")

        # Wiki icon: prefer game-declared icon, fall back to ITEM_<DisplayName>
        if node["icon"]:
            wiki_icon = node["icon"]
        elif display:
            clean = display.replace('"', '').replace(' ', '_')
            wiki_icon = f"ITEM_{clean}"
        else:
            wiki_icon = ""

        lines.append(f"    [\"{node['name']}\"] = {{")
        lines.append(f"        name = {lua_string(display)},")
        lines.append(f"        icon = {lua_string(wiki_icon)},")
        lines.append(f"        tree = \"{node['tree']}\",")
        lines.append(f"        x = {node['sx']},")
        lines.append(f"        y = {node['sy']},")
        if node["description"]:
            lines.append(f"        desc = {lua_string(node['description'])},")
        if is_reroute:
            lines.append("        reroute = true,")
        lines.append("    },")
    lines.append("}")
    lines.append("")

    lines.append("data.connections = {")
    for conn in sorted(tech_connections, key=lambda c: (c["from"], c["to"])):
        method = conn.get("draw_override", "Unspecified")
        if method and method != "Unspecified":
            lines.append(f"    {{ from = \"{conn['from']}\", to = \"{conn['to']}\", method = \"{method}\" }},")
        else:
            lines.append(f"    {{ from = \"{conn['from']}\", to = \"{conn['to']}\" }},")
    lines.append("}")
    lines.append("")
    lines.append("return data")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "TechData.lua"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  Wrote {out_path} ({len(lines)} lines)")


def generate_mission_data(prospect_nodes, all_connections, tree_info,
                          prospect_archetype_order, archetype_info,
                          trees_by_archetype, view_info, prospect_info):
    """Generate MissionData.lua module from Prospect trees."""
    scale = SCALE["Prospect"]
    node_size = NODE_SIZE["Prospect"]
    line_method = view_info.get("Prospect", {}).get("line_method", "ShortestDistance")

    ordered_trees = []
    for arch_name in prospect_archetype_order:
        for tree_name in trees_by_archetype.get(arch_name, []):
            ordered_trees.append(tree_name)
    allowed_trees = set(ordered_trees)

    prospect_nodes = [
        node for node in prospect_nodes
        if node["tree"] in allowed_trees
    ]

    trees = {}
    for node in prospect_nodes:
        tree = node["tree"]
        if tree not in trees:
            trees[tree] = []
        trees[tree].append(node)

    tree_dims = normalize_tree_positions(
        trees, scale, node_size, padding=30, use_node_dimensions=True
    )

    prospect_names = {n["name"] for n in prospect_nodes}
    mission_connections = [
        c for c in all_connections
        if c["from"] in prospect_names and c["to"] in prospect_names
    ]

    ordered_trees = [tree for tree in ordered_trees if tree in trees]

    difficulty_ranks = {
        "Easy": 1,
        "Normal": 2,
        "Hard": 3,
        "Extreme": 4,
    }

    def get_region_nodes(tree_name, visible_only=False):
        nodes = [
            node for node in prospect_nodes
            if node["tree"] == tree_name and (not visible_only or not node.get("reroute"))
        ]
        nodes.sort(key=lambda n: (n["x"], n["y"], n["name"]))
        return nodes

    def get_region_feature(tree_name):
        info = tree_info.get(tree_name, {})
        arch = info.get("archetype", "")
        arch_meta = archetype_info.get(arch, {}).get("metadata", {}) or {}
        tree_meta = info.get("metadata", {}) or {}
        feature = (
            arch_meta.get("RequiredFeatureLevel", {}).get("RowName", "")
            or tree_meta.get("RequiredFeatureLevel", {}).get("RowName", "")
        )
        return format_enum_label(feature)

    def get_region_theme(tree_name):
        info = tree_info.get(tree_name, {})
        arch = info.get("archetype", "")
        label = archetype_info.get(arch, {}).get("display_name", "") or info.get("display", tree_name)
        return normalize_lookup_key(label or tree_name)

    def get_region_preview(tree_name):
        visible_nodes = get_region_nodes(tree_name, visible_only=True)
        root = next((n for n in visible_nodes if n.get("default_unlocked")), None)
        if not root and visible_nodes:
            root = visible_nodes[0]
        if not root:
            return "", ""
        prospect = prospect_info.get(root.get("extra_row", ""), {})
        return prospect.get("prospect_image", "") or root.get("icon", ""), prospect.get("drop_name", "")

    def get_region_selector_prospect(tree_name):
        selector_key = get_region_style(get_region_theme(tree_name)).get("selector_key", "")
        return prospect_info.get(selector_key, {})

    def get_region_selector_image(tree_name):
        preview_image, _ = get_region_preview(tree_name)
        selector_image = get_region_style(get_region_theme(tree_name)).get("selector_image", "")
        if selector_image:
            return selector_image
        selector_prospect = get_region_selector_prospect(tree_name)
        prospect_image = selector_prospect.get("prospect_image", "")
        if prospect_image == GENERIC_PROSPECT_WORLD_IMAGE:
            prospect_image = ""
        return prospect_image or preview_image

    def get_region_background_image(tree_name):
        preview_image, _ = get_region_preview(tree_name)
        background_image = get_region_style(get_region_theme(tree_name)).get("background_image", "")
        if background_image:
            return background_image
        return get_region_selector_image(tree_name) or preview_image

    def get_region_selector_description(tree_name):
        selector_description = get_region_style(get_region_theme(tree_name)).get("selector_description", "")
        if selector_description:
            return selector_description
        return get_selector_description(get_region_selector_prospect(tree_name))

    lines = []
    lines.append("-- Auto-generated from game data by scripts/generate_tree_data.py")
    lines.append("-- Do not edit manually. Re-run the script to update.")
    lines.append("local data = {}")
    lines.append("")

    lines.append("data.region_order = {")
    for tree_name in ordered_trees:
        lines.append(f"    \"{tree_name}\",")
    lines.append("}")
    lines.append("")

    lines.append("data.regions = {")
    for tree_name in ordered_trees:
        info = tree_info.get(tree_name, {})
        arch = info.get("archetype", "")
        label = archetype_info.get(arch, {}).get("display_name", "") or info.get("display", tree_name)
        dims = tree_dims.get(tree_name, {"canvas_w": 0, "canvas_h": 0})
        theme = get_region_theme(tree_name)
        region_style = get_region_style(theme)
        preview_image, preview_title = get_region_preview(tree_name)
        selector_image = get_region_selector_image(tree_name)
        background_image = get_region_background_image(tree_name)
        mission_count = len(get_region_nodes(tree_name, visible_only=True))
        feature_label = get_region_feature(tree_name)
        lines.append(f"    [\"{tree_name}\"] = {{")
        lines.append(f"        label = {lua_string(label)},")
        lines.append(f"        theme = {lua_string(theme)},")
        lines.append(f"        icon = {lua_string(archetype_info.get(arch, {}).get('icon', ''))},")
        lines.append(f"        preview_image = {lua_string(preview_image)},")
        lines.append(f"        preview_title = {lua_string(preview_title)},")
        lines.append(f"        selector_image = {lua_string(selector_image)},")
        lines.append(f"        background_image = {lua_string(background_image)},")
        lines.append(f"        selector_description = {lua_string(get_region_selector_description(tree_name))},")
        lines.append(f"        selector_difficulty = {region_style.get('selector_difficulty', 0)},")
        lines.append(f"        selector_difficulty_color = {lua_string(region_style.get('selector_color', ''))},")
        lines.append(f"        feature = {lua_string(feature_label)},")
        lines.append(f"        mission_count = {mission_count},")
        lines.append(f"        mission_count_label = {lua_string(f'{mission_count}/{mission_count}')},")
        lines.append(f"        line_method = \"{line_method}\",")
        lines.append(f"        node_size = {node_size},")
        lines.append(f"        canvas_w = {dims['canvas_w']},")
        lines.append(f"        canvas_h = {dims['canvas_h']},")
        lines.append("    },")
    lines.append("}")
    lines.append("")

    lines.append("data.nodes = {")
    for node in sorted(prospect_nodes, key=lambda n: (n["tree"], n["x"], n["y"], n["name"])):
        extra_key = node.get("extra_row", "")
        prospect = prospect_info.get(extra_key, {})
        node_display = node.get("display_name", "")
        node_description = node.get("description", "")
        if node_display == "None":
            node_display = ""
        if node_description == "None":
            node_description = ""
        display = (
            prospect.get("drop_name")
            or node_display
            or format_mission_fallback_name(node["name"], extra_key)
        )
        page_name = (
            format_page_title(display)
            or format_page_title(format_mission_fallback_name(node["name"], extra_key))
            or node["name"]
        )
        description = prospect.get("description") or node_description
        flavour = prospect.get("flavour", "")
        difficulty = prospect.get("difficulty") or "Easy"
        difficulty_rank = difficulty_ranks.get(difficulty, 1)
        feature_label = format_enum_label(prospect.get("feature_level") or node.get("feature_level", ""))
        availability_label = format_enum_label(prospect.get("availability", ""))
        tech_short = format_tier_short(prospect.get("required_tech", ""))
        duration_days, duration_hours, duration_mins = extract_duration_parts(
            prospect.get("time_duration", {})
        )
        type_entries = [
            mission_type
            for mission_type in prospect.get("mission_types", [])
            if mission_type.get("icon", "")
        ]
        reward_entries = [
            reward
            for reward in prospect.get("currency_rewards", [])
            if reward.get("amount", 0)
        ]

        lines.append(f"    [\"{node['name']}\"] = {{")
        lines.append(f"        name = {lua_string(display)},")
        lines.append(f"        page_name = {lua_string(page_name)},")
        lines.append(f"        tree = \"{node['tree']}\",")
        lines.append(f"        x = {node['sx']},")
        lines.append(f"        y = {node['sy']},")
        lines.append(f"        w = {node.get('sw', node_size)},")
        lines.append(f"        h = {node.get('sh', node_size)},")
        if node.get("reroute"):
            lines.append("        reroute = true,")
        else:
            lines.append(f"        image = {lua_string(prospect.get('prospect_image', '') or node.get('icon', ''))},")
            lines.append(f"        desc = {lua_string(description)},")
            lines.append(f"        flavour = {lua_string(flavour)},")
            lines.append(f"        biome = {lua_string(prospect.get('biome', ''))},")
            lines.append(f"        operator = {lua_string(prospect.get('operator', ''))},")
            lines.append(f"        mission = {lua_string(prospect.get('mission', ''))},")
            lines.append(f"        background = {lua_string(prospect.get('background', ''))},")
            lines.append(f"        terms = {lua_string(prospect.get('terms', ''))},")
            lines.append(f"        tech = {lua_string(tech_short)},")
            lines.append(f"        difficulty = {lua_string(difficulty)},")
            lines.append(f"        difficulty_rank = {difficulty_rank},")
            lines.append(f"        duration = {lua_string(prospect.get('time_label', '00 00 00'))},")
            lines.append(f"        duration_days = {duration_days},")
            lines.append(f"        duration_hours = {duration_hours},")
            lines.append(f"        duration_mins = {duration_mins},")
            lines.append(f"        availability = {lua_string(availability_label)},")
            lines.append(f"        feature = {lua_string(feature_label)},")
            lines.append(f"        effect = {lua_string(prospect.get('effect', ''))},")
            lines.append(f"        prospect_key = {lua_string(extra_key)},")
            lines.append("        types = {")
            for mission_type in type_entries:
                lines.append("            {")
                lines.append(f"                name = {lua_string(mission_type.get('name', ''))},")
                lines.append(f"                label = {lua_string(mission_type.get('label', ''))},")
                lines.append(f"                icon = {lua_string(mission_type.get('icon', ''))},")
                lines.append("            },")
            lines.append("        },")
            lines.append("        rewards = {")
            for reward in reward_entries:
                amount = reward.get("amount", 0)
                if isinstance(amount, float) and amount.is_integer():
                    amount = int(amount)
                lines.append("            {")
                lines.append(f"                name = {lua_string(reward.get('name', ''))},")
                lines.append(f"                label = {lua_string(reward.get('label', ''))},")
                lines.append(f"                icon = {lua_string(reward.get('icon', ''))},")
                lines.append(f"                color = {lua_string(reward.get('color', ''))},")
                lines.append(f"                amount = {amount},")
                lines.append("            },")
            lines.append("        },")
        lines.append("    },")
    lines.append("}")
    lines.append("")

    lines.append("data.connections = {")
    for conn in sorted(mission_connections, key=lambda c: (c["from"], c["to"])):
        method = conn.get("draw_override", "Unspecified")
        if method and method != "Unspecified":
            lines.append(f"    {{ from = \"{conn['from']}\", to = \"{conn['to']}\", method = \"{method}\" }},")
        else:
            lines.append(f"    {{ from = \"{conn['from']}\", to = \"{conn['to']}\" }},")
    lines.append("}")
    lines.append("")
    lines.append("return data")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "MissionData.lua"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  Wrote {out_path} ({len(lines)} lines)")


if __name__ == "__main__":
    main()
