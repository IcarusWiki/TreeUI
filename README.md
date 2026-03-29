# Icarus Wiki Tree UI

Interactive tree viewers for [icarus.wiki.gg](https://icarus.wiki.gg) showing the game's Talent Trees, Animal Talent Trees, Workshop Trees, Mission Trees, and Blueprint (Tech) Trees. The repo is built around MediaWiki Lua modules, TemplateStyles CSS, and a shared JavaScript gadget.

## Wiki Page Mapping

| Local file | Wiki page |
|-----------|-----------|
| `lua/TreeCore.lua` | [Module:TreeCore](https://icarus.wiki.gg/wiki/Module:TreeCore) |
| `css/shared.css` | [MediaWiki:Gadgets/treeCore/main.css](https://icarus.wiki.gg/wiki/MediaWiki:Gadgets/treeCore/main.css) |
| `common.js` | [MediaWiki:Gadgets/treeCore/main.js](https://icarus.wiki.gg/wiki/MediaWiki:Gadgets/treeCore/main.js) |
| `lua/TalentTree.lua` | [Module:TalentTree](https://icarus.wiki.gg/wiki/Module:TalentTree) |
| `generated/TalentData.lua` | [Module:TalentTree/TalentData](https://icarus.wiki.gg/wiki/Module:TalentTree/TalentData) |
| `css/talent.css` | [Module:TalentTree/styles.css](https://icarus.wiki.gg/wiki/Module:TalentTree/styles.css) |
| `lua/AnimalTalentTree.lua` | [Module:AnimalTalentTree](https://icarus.wiki.gg/wiki/Module:AnimalTalentTree) |
| `generated/AnimalTalentData.lua` | [Module:AnimalTalentTree/AnimalTalentData](https://icarus.wiki.gg/wiki/Module:AnimalTalentTree/AnimalTalentData) |
| `lua/WorkshopTree.lua` | [Module:WorkshopTree](https://icarus.wiki.gg/wiki/Module:WorkshopTree) |
| `generated/WorkshopData.lua` | [Module:WorkshopTree/WorkshopData](https://icarus.wiki.gg/wiki/Module:WorkshopTree/WorkshopData) |
| `css/workshop.css` | [Module:WorkshopTree/styles.css](https://icarus.wiki.gg/wiki/Module:WorkshopTree/styles.css) |
| `lua/MissionTree.lua` | [Module:MissionTree](https://icarus.wiki.gg/wiki/Module:MissionTree) |
| `generated/MissionData.lua` | [Module:MissionTree/MissionData](https://icarus.wiki.gg/wiki/Module:MissionTree/MissionData) |
| `css/mission.css` | [Module:MissionTree/styles.css](https://icarus.wiki.gg/wiki/Module:MissionTree/styles.css) |
| `lua/TechTree.lua` | [Module:TechTree](https://icarus.wiki.gg/wiki/Module:TechTree) |
| `generated/TechData.lua` | [Module:TechTree/TechData](https://icarus.wiki.gg/wiki/Module:TechTree/TechData) |
| `css/tech.css` | [Module:TechTree/styles.css](https://icarus.wiki.gg/wiki/Module:TechTree/styles.css) |

The shared CSS and JS are loaded as a MediaWiki gadget (`treeCore`) on all pages. Tree-specific CSS is loaded with TemplateStyles only when the corresponding Lua module is invoked.

## Architecture

```text
data.pak                    Installed Icarus data archive
        |
scripts/rebuild_ingamefiles.py  Python script: clears InGameFiles/ and extracts pak paths as-is
        |
InGameFiles/*               Game data exports (source of truth)
        |
scripts/generate_tree_data.py   Python script: parses, resolves, normalizes
        |
generated/*.lua             Lua data modules (positions, metadata, connections)
        |
lua/*.lua                   MediaWiki Lua: builds HTML from data
        |
css/*.css                   TemplateStyles: layout, theming, responsiveness
        |
common.js                   MediaWiki gadget: tabs, tooltips, SVG lines, pan
```

## Files

### Lua Modules

| File | Purpose |
|------|---------|
| `lua/TreeCore.lua` | Shared utilities: canvas creation, node positioning, SVG connection fallback, string helpers |
| `lua/TalentTree.lua` | Talent tree renderer with top-bar tabs, talents/solo mode toggle, rank badges, level counters, and rich JSON tooltips |
| `lua/AnimalTalentTree.lua` | Animal talent renderer using the same talent presentation patterns |
| `lua/WorkshopTree.lua` | Workshop tree renderer with sidebar navigation and wiki page links |
| `lua/MissionTree.lua` | Mission tree renderer with region selector cards and pannable prospect graphs |
| `lua/TechTree.lua` | Blueprint tree renderer with tier tab bar (Tier 1-5) |

### Generated Data

All files in `generated/` are auto-generated. Do not edit them manually; re-run the script instead.

| File | Contents |
|------|----------|
| `TalentData.lua` | `tab_order`, `views`, `nodes`, `connections` |
| `AnimalTalentData.lua` | `animal_order`, `animals`, `lookup`, `nodes`, `connections` |
| `WorkshopData.lua` | `category_order`, `categories`, `nodes`, `connections` |
| `MissionData.lua` | `region_order`, `regions`, `nodes` (including card sizes and prospect metadata), `connections` |
| `TechData.lua` | `tier_order`, `tiers`, `nodes`, `connections` |

### Stylesheets

| File | Scope | Theme color |
|------|-------|-------------|
| `css/shared.css` | All trees: layout, nodes, tooltips, shared mobile behavior | n/a |
| `css/talent.css` | Talent tree tabs, mode toggle, rank badges, sub-sections | `#d4a832` |
| `css/workshop.css` | Workshop sidebar and category buttons | `#b580c9` |
| `css/mission.css` | Mission selector cards, topographical backdrop, prospect card chrome | `#a7c96d` |
| `css/tech.css` | Blueprint tier top bar and tier buttons | `#59bfff` |

### JavaScript

`common.js` is loaded as a MediaWiki gadget and provides:

- Tab/sidebar switching for workshop, mission, and blueprint trees, plus talent tabs and modes
- Floating tooltips: simple text for workshop, mission, and tech; rich structured tooltips for talents
- SVG connection drawing from `data-iww-tree-connections`
- Drag-to-pan on `.iww-tree-content-area`
- Mobile behavior: workshop and tech nodes are scaled to 50 percent on small viewports; mission and talent trees keep their native sizing; JS panning is disabled so native scrolling takes over

### Scripts

`scripts/rebuild_ingamefiles.py` clears `InGameFiles/` and re-extracts the installed
`data.pak` into it, preserving the archive's directory structure exactly as stored.

`scripts/generate_tree_data.py` reads `InGameFiles/` JSON and writes the Lua data modules in `generated/`.
Use `--in-dir` and `--out-dir` to point it at a sibling checkout during automation.

Usage:

```bash
python -m pip install -r requirements.txt
python scripts/rebuild_ingamefiles.py
python scripts/generate_tree_data.py
```

By default the extractor reads:

```text
D:\Games\Steam\steamapps\common\Icarus\Icarus\Content\Data\data.pak
```

If needed, override the source or destination with `--pak` and `--out`.
If `pyuepak` cannot download `oo2core_9_win64.dll` automatically on first run,
provide a local copy with `--oodle-dll`.
On Windows, `scripts/rebuild_ingamefiles.py` will prefer
`%USERPROFILE%\anaconda3\python.exe` when it exists so it uses the same Anaconda
3.11 environment that the repo's dependency install typically targets. Override
that choice with the `ICARUS_WIKI_PREFERRED_PYTHON` environment variable if needed.

### Wiki Templates

| File | Purpose |
|------|---------|
| `Sandbox.wiki` | Example wiki page invoking workshop, mission, talent, tech, and animal talent trees via `{{#invoke:}}` |
| `TalentsSolo.wiki` | Legacy static reference for solo talents |

## Game Data Files

All files live under `InGameFiles/` in subdirectories matching the pak's directory structure.

### Talents/

| File | Key fields | Used for |
|------|-----------|----------|
| `D_TalentArchetypes.json` | `Model`, `DisplayName`, `Icon` | Top-level ordering and labels. Models group archetypes like `Player`, `Workshop`, `Blueprint`, `Prospect`, `Creature`, and `Solo`. Row order is display order. |
| `D_TalentTrees.json` | `Archetype`, `DisplayName`, `Icon` | Sub-tree definitions. Each tree belongs to one archetype. |
| `D_TalentViews.json` | `LineMethod` | Default routing hint per view type (`YThenX`, `XThenY`, `ShortestDistance`) |
| `D_Talents.json` | `Position`, `Size`, `DisplayName`, `Description`, `Icon`, `ExtraData`, `RequiredTalents`, `RequiredRank`, `Rewards`, `DrawMethodOverride`, `TalentType` | Main node table for talents, workshop items, blueprints, animal talents, and mission graph nodes. `TalentType: "Reroute"` marks invisible routing waypoints. |

### Prospects/

| File | Key fields | Used for |
|------|-----------|----------|
| `D_ProspectList.json` | `DropName`, `Description`, `FlavourText`, `ProspectImage`, `Difficulty`, `RequiredTech`, `TimeDuration`, `OnProspectAvailability`, `Metadata.RequiredFeatureLevel` | Mission card content: player-facing titles, descriptions, preview art, difficulty, timers, feature gates, and structured flavor text such as operator, biome, mission, and terms |

### Items/

| File | Key fields | Used for |
|------|-----------|----------|
| `D_ItemsStatic.json` | `Itemable.RowName` | Bridge table from item names to `D_Itemable` entries |

### MetaWorkshop/

| File | Key fields | Used for |
|------|-----------|----------|
| `D_WorkshopItems.json` | `Item.RowName` | Bridge table from workshop item names to `D_ItemsStatic` entries |

### Traits/

| File | Key fields | Used for |
|------|-----------|----------|
| `D_Itemable.json` | `DisplayName`, `Description`, `Icon` | Terminal table providing display names, descriptions, and icon paths for items |

### Item Resolution Chain

Workshop and blueprint nodes reference items through `ExtraData.RowName`. The resolution chain is:

```text
D_Talents.ExtraData.RowName
    -> D_WorkshopItems.Item.RowName
    -> D_ItemsStatic.Itemable.RowName
    -> D_Itemable.Icon / DisplayName / Description
```

The generator includes fallback strategies for game-data naming inconsistencies:

- `_Armor_` segment stripping
- `_Helmet_` -> `_Head_` conversion
- `Meta_` prefix handling
- Missing underscores before trailing digits such as `Shengong2` -> `Shengong_2`
- Compound-word joins such as `World_Boss` -> `WorldBoss`

## Wiki Icon Naming

Icons are referenced in wiki markup as `[[File:<name>.png|...]]`. The Lua modules append the `.png` extension; generated data stores only the base name.

### How game icon paths become wiki filenames

The script's `clean_icon_path()` extracts the base name from Unreal asset paths:

| Game data value | Wiki filename |
|----------------|---------------|
| `/Game/Assets/.../T_ITEM_Bone_Chest.T_ITEM_Bone_Chest` | `T_ITEM_Bone_Chest.png` |
| `/Game/Assets/.../ITEM_Fibre.ITEM_Fibre` | `ITEM_Fibre.png` |
| `/Game/Assets/.../Icon_Survival.Icon_Survival` | `Icon_Survival.png` |
| `Texture2D '/Game/.../T_Icon.T_Icon'` | `T_Icon.png` |

Transformation steps:

1. Strip the `Texture2D` prefix and quotes if present.
2. Take the part after the last `/`.
3. Remove the duplicated `.AssetName` suffix.
4. Use the remaining base name and append `.png` in Lua.

### Assumptions

For icons and prospect art to display correctly on the wiki, uploaded files should match the cleaned asset names with `.png` appended.

- Workshop and tech item nodes resolve icons from `D_Itemable.Icon`, unless a direct `D_Talents.Icon` is present.
- Mission cards resolve preview art from `D_ProspectList.ProspectImage`.
- Talent nodes use the wiki convention `Talent <Display Name>.png`.
- Archetype and tier icons come from `D_TalentArchetypes.Icon`.
- Nodes without a resolved icon fall back to `OrbitalNode_Unlocked_Normal.png`.
- Talent rank badges are hardcoded wiki filenames such as `ICARUS-Talents_Tree-Rank-1.png`.

## Rendering Constants

These values are set in the generator and control how game coordinates translate to CSS pixels:

| View type | Scale factor | Node size (px) |
|-----------|--------------|----------------|
| Player (talents) | 0.45 | 56 |
| Solo | 0.45 | 56 |
| Workshop | 0.40 | 100 |
| Blueprint | 0.50 | 100 |
| Creature | 0.65 | 80 |
| Prospect | 0.25 | 150 |

## Connection Line Methods

Each tree view has a default routing method from `D_TalentViews.LineMethod`, and individual nodes can override it with `DrawMethodOverride`:

- `YThenX`: vertical trunk from source, then horizontal branch to target
- `XThenY`: horizontal trunk from source, then vertical branch to target
- `ShortestDistance`: straight diagonal line with no merging

Overlapping horizontal or vertical segments are merged to reduce clutter.

## Mobile Behavior

On viewports at or below 768px:

- All tabs/views are unfolded vertically with section headers
- Navigation bars and sidebars are hidden
- Workshop and tech trees are scaled to 50 percent
- Mission and talent trees keep their native node/card sizing
- JS panning is disabled and native scroll takes over

## Remaining Hardcoding

| What | Where | Why it cannot be derived |
|------|-------|--------------------------|
| Rank badge filenames | `lua/TalentTree.lua` | Wiki-uploaded files with custom naming; would need `D_TalentRanks.json` |
| Scale factors and node sizes | `scripts/generate_tree_data.py` | Presentation choices, not raw game data |
| Line colors | `*Tree.lua` | Presentation choices per tree type |
| Talent icon naming convention | `scripts/generate_tree_data.py` | Wiki convention, not game icon paths |
