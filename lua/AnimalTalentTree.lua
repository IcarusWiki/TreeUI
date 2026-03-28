local p = {}
local core = require("Module:TreeCore")
local animalData = require("Module:AnimalTalentTree/AnimalTalentData")

local TALENT_LINE_COLOR = "#d4a832"

local function normalize_lookup_key(value)
    return string.lower(tostring(value or "")):gsub("[^%a%d]+", "")
end

local function get_frame_arg(frame, key, index)
    if frame and frame.args then
        local value = frame.args[key] or frame.args[index]
        if value and tostring(value) ~= "" then
            return value
        end
    end

    if frame and frame.getParent then
        local parent = frame:getParent()
        if parent and parent.args then
            local value = parent.args[key] or parent.args[index]
            if value and tostring(value) ~= "" then
                return value
            end
        end
    end

    return nil
end

local function render_error(message)
    return tostring(
        mw.html.create("div")
            :addClass("error")
            :wikitext(mw.text.nowiki(message))
    )
end

local function resolve_animal(frame)
    local raw_value = core.trim(get_frame_arg(frame, "animal", 1) or "")
    if raw_value == "" then
        return nil, "AnimalTalentTree: specify |animal=... e.g. {{#invoke:AnimalTalentTree|render|animal=Moa}}"
    end

    local normalized = normalize_lookup_key(raw_value)
    local tree_key = animalData.lookup[normalized]

    if not tree_key and animalData.animals[raw_value] then
        tree_key = raw_value
    end

    if not tree_key then
        return nil, "AnimalTalentTree: unknown animal '" .. raw_value
            .. "'. Use a display name like Moa, Buffalo, Horse, Hyena, or Dune Raptor."
    end

    return tree_key, animalData.animals[tree_key]
end

local function make_talent_node(node_id, data)
    if data.reroute then
        return mw.html.create("div")
            :attr("data-iww-tree-node-id", node_id)
            :css({ ["position"] = "absolute", ["width"] = "0", ["height"] = "0" })
    end

    local node = mw.html.create("div")
        :addClass("iww-tree-node")
        :addClass("iww-talent-tree-node")
        :attr("data-iww-tree-node-id", node_id)

    local icon_file = data.icon ~= "" and (data.icon .. ".png") or "OrbitalNode_Unlocked_Normal.png"
    local display_name = data.name ~= "" and data.name or node_id
    local icon_wt = string.format(
        "[[File:%s|44x44px|link=|alt=%s]]",
        icon_file, display_name
    )
    node:tag("span"):addClass("iww-tree-icon-wrap"):wikitext(icon_wt)

    node:tag("span"):addClass("iww-tree-node-frame"):addClass("iww-talent-tree-node-frame")

    node:tag("span"):addClass("iww-tree-glow")

    local max_level = 1
    if data.levels and data.levels ~= "" then
        local _, count = string.gsub(data.levels, "\\n", "")
        max_level = count + 1
    end
    node:tag("span"):addClass("iww-talent-tree-level-counter")
        :wikitext("0/" .. tostring(max_level))

    local levels_arr = {}
    if data.levels and data.levels ~= "" then
        for part in string.gmatch(data.levels .. "\\n", "(.-)(\\n)") do
            table.insert(levels_arr, part)
        end
    end
    local tooltip_obj = {
        name = display_name,
        description = data.desc or "",
        levels = levels_arr,
        max_level = max_level,
    }
    node:attr("data-iww-talent-tree-info", mw.text.jsonEncode(tooltip_obj))

    node:tag("span"):addClass("iww-tree-tooltip"):wikitext(display_name)

    return node
end

local function make_styles(frame)
    return frame:extensionTag(
        "templatestyles", "",
        { src = "Module:TalentTree/styles.css" }
    )
end

local function build_collapsible_wrapper(content_node)
    local wrapper = mw.html.create("div")
        :addClass("mw-collapsible")
        :addClass("mw-collapsed")
        :addClass("iww-animal-talent-tree-collapsible")
        :attr("data-expandtext", "Show Talent Tree")
        :attr("data-collapsetext", "Hide Talent Tree")

    local toggle_bar = wrapper:tag("div")
        :addClass("iww-animal-talent-tree-toggle-bar")
    toggle_bar:tag("span"):addClass("mw-collapsible-toggle-placeholder")

    wrapper:tag("div")
        :addClass("mw-collapsible-content")
        :addClass("iww-animal-talent-tree-collapsible-content")
        :node(content_node)

    return wrapper
end

local function build_tree_wrapper(tree_key, tree_meta)
    local tab_id = core.make_tab_id(tree_key, 1)

    local tree_nodes = {}
    for node_id, node_data in pairs(animalData.nodes) do
        if node_data.tree == tree_key then
            local element = make_talent_node(node_id, node_data)
            local item = {
                id = node_id,
                element = element,
                x = node_data.x,
                y = node_data.y,
            }
            if node_data.reroute then
                item.is_reroute = true
            end
            table.insert(tree_nodes, item)
        end
    end

    local tree_conns = {}
    for _, conn in ipairs(animalData.connections) do
        local from_node = animalData.nodes[conn.from]
        if from_node and from_node.tree == tree_key then
            table.insert(tree_conns, conn)
        end
    end

    local canvas = core.create_canvas(tree_meta.canvas_w, tree_meta.canvas_h)
    if #tree_conns > 0 then
        core.add_connection_fallback(
            canvas,
            tree_meta.canvas_w,
            tree_meta.canvas_h,
            tree_nodes,
            tree_conns,
            tree_meta.line_method,
            tree_meta.node_size,
            TALENT_LINE_COLOR
        )
    end

    core.populate_canvas(canvas, tree_nodes, tree_meta.node_size)

    if #tree_conns > 0 then
        core.set_connection_data(
            canvas, tree_conns,
            tree_meta.line_method,
            tree_meta.node_size
        )
    end

    local wrapper = mw.html.create("div")
        :addClass("iww-tree-ui-wrapper")
        :addClass("iww-talent-tree-ui-wrapper")
        :addClass("iww-animal-talent-tree-ui-wrapper")

    -- Hidden tab/mode controls keep the existing talent JS path working.
    local top_bar = wrapper:tag("div")
        :addClass("iww-talent-tree-top-bar")
        :css("display", "none")

    top_bar:tag("div"):addClass("iww-talent-tree-tab-group")
        :tag("div")
            :addClass("iww-talent-tree-tab-btn")
            :attr("data-iww-tree-tab", tab_id)
            :wikitext(tree_meta.label)

    top_bar:tag("div"):addClass("iww-talent-tree-mode-toggle")
        :tag("span")
            :addClass("iww-talent-tree-mode-btn")
            :attr("data-iww-tree-mode", "talents")
            :wikitext("TALENTS")

    local content = wrapper:tag("div"):addClass("iww-tree-content-area")
    local view = content:tag("div")
        :addClass("iww-tree-view")
        :addClass("active")
        :attr("data-iww-tree-tab", tab_id)
        :attr("data-iww-tree-mode", "talents")

    view:tag("h3"):addClass("iww-tree-section-title"):wikitext(tree_meta.label)

    local sub_container = view:tag("div"):addClass("iww-talent-tree-sub-container")
    local section = sub_container:tag("div"):addClass("iww-talent-tree-sub-section")
    section:tag("h4"):addClass("iww-talent-tree-sub-header"):wikitext(tree_meta.label)
    section:node(canvas)

    return wrapper
end

local function make_tabber(tabs)
    if mw.ext and mw.ext.tabber and mw.ext.tabber.render then
        return mw.ext.tabber.render(tabs)
    end

    local parts = { "<tabber>" }
    for i, tab in ipairs(tabs) do
        if i > 1 then
            parts[#parts + 1] = "|-|"
        end
        parts[#parts + 1] = tab.label .. "="
        parts[#parts + 1] = tab.content
    end
    parts[#parts + 1] = "</tabber>"

    return table.concat(parts, "\n")
end

function p.render(frame)
    local tree_key, tree_meta = resolve_animal(frame)
    if not tree_key or not tree_meta then
        return render_error(tree_meta or "AnimalTalentTree: unable to resolve animal.")
    end

    return make_styles(frame)
        .. tostring(build_collapsible_wrapper(build_tree_wrapper(tree_key, tree_meta)))
        .. "[[Category:Pages using TreeCore]]"
end

function p.renderTabber(frame)
    local tabs = {}
    for _, tree_key in ipairs(animalData.animal_order) do
        local tree_meta = animalData.animals[tree_key]
        if tree_meta then
            table.insert(tabs, {
                label = tree_meta.label,
                content = tostring(build_tree_wrapper(tree_key, tree_meta)),
            })
        end
    end

    return make_styles(frame)
        .. make_tabber(tabs)
        .. "[[Category:Pages using TreeCore]]"
end

return p
