local p = {}
local core = require("Module:TreeCore")
local talentData = require("Module:TalentTree/TalentData")

local TALENT_LINE_COLOR = "#d4a832"

-- Rank badge images (index = rank level)
local rank_icons = {
    [1] = "ICARUS-Talents_Tree-Rank-1.png",  -- Apprentice
    [2] = "ICARUS-Talents_Tree-Rank-2.png",  -- Journeyman
    [3] = "ICARUS-Talents_Tree-Rank-3.png",  -- Master
}

-- ============================================================================
-- TALENT NODE BUILDER
-- ============================================================================

local function make_talent_node(node_id, data)
    -- Reroute nodes: invisible position anchors for line routing
    if data.reroute then
        return mw.html.create("div")
            :attr("data-iww-tree-node-id", node_id)
            :css({ ["position"] = "absolute", ["width"] = "0", ["height"] = "0" })
    end

    local node = mw.html.create("div")
        :addClass("iww-tree-node")
        :addClass("iww-talent-tree-node")
        :attr("data-iww-tree-node-id", node_id)

    -- Icon
    local icon_file = data.icon ~= "" and (data.icon .. ".png") or "OrbitalNode_Unlocked_Normal.png"
    local display_name = data.name ~= "" and data.name or node_id
    local icon_wt = string.format(
        '[[File:%s|44x44px|link=|alt=%s]]',
        icon_file, display_name
    )
    node:tag("span"):addClass("iww-tree-icon-wrap"):wikitext(icon_wt)

    -- Frame
    node:tag("span"):addClass("iww-tree-node-frame"):addClass("iww-talent-tree-node-frame")

    -- Rank badge (Apprentice/Journeyman/Master)
    if data.rank and rank_icons[data.rank] then
        node:tag("span"):addClass("iww-talent-tree-rank-badge")
            :wikitext(string.format('[[File:%s|18x18px]]', rank_icons[data.rank]))
    end

    -- Glow
    node:tag("span"):addClass("iww-tree-glow")

    -- Level counter
    local max_level = 1
    if data.levels and data.levels ~= "" then
        -- Count levels by counting \n separators + 1
        local _, count = string.gsub(data.levels, "\\n", "")
        max_level = count + 1
    end
    node:tag("span"):addClass("iww-talent-tree-level-counter")
        :wikitext("0/" .. tostring(max_level))

    -- Structured tooltip data (JSON for JS to parse)
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

    -- Simple tooltip fallback
    node:tag("span"):addClass("iww-tree-tooltip"):wikitext(display_name)

    return node
end

-- ============================================================================
-- RENDER FUNCTION
-- ============================================================================

function p.render(frame)
    local talent_styles = frame:extensionTag(
        "templatestyles", "",
        { src = "Module:TalentTree/styles.css" }
    )

    local wrapper = mw.html.create("div")
        :addClass("iww-tree-ui-wrapper")
        :addClass("iww-talent-tree-ui-wrapper")

    -- TOP BAR (horizontal tabs + mode toggle)
    local top_bar = wrapper:tag("div"):addClass("iww-talent-tree-top-bar")

    -- Main category tabs (order and labels from generated data)
    local tab_group = top_bar:tag("div"):addClass("iww-talent-tree-tab-group")
    for i, tab_key in ipairs(talentData.tab_order) do
        local view_data = talentData.views[tab_key]
        if view_data then
            local btn = tab_group:tag("div")
                :addClass("iww-talent-tree-tab-btn")
                :attr("data-iww-tree-tab", core.make_tab_id(tab_key, i))
            if view_data.icon ~= "" then
                btn:tag("span"):addClass("iww-talent-tree-tab-icon")
                    :wikitext(string.format('[[File:%s.png|frameless|20px|link=]]', view_data.icon))
            end
            btn:tag("span"):wikitext(view_data.label)
            if i == 1 then
                btn:addClass("active")
            end
        end
    end

    -- Talents / Solo mode toggle
    local toggle = top_bar:tag("div"):addClass("iww-talent-tree-mode-toggle")
    toggle:tag("span"):addClass("iww-talent-tree-mode-btn"):addClass("active")
        :attr("data-iww-tree-mode", "talents"):wikitext("TALENTS")
    toggle:tag("span"):addClass("iww-talent-tree-mode-btn")
        :attr("data-iww-tree-mode", "solo"):wikitext("SOLO")

    -- CONTENT AREA
    local content = wrapper:tag("div"):addClass("iww-tree-content-area")

    -- Index connections by tree for quick lookup
    local conns_by_tree = {}
    for _, conn in ipairs(talentData.connections) do
        -- Determine tree from the "from" node
        local from_node = talentData.nodes[conn.from]
        if from_node then
            local tree = from_node.tree
            if not conns_by_tree[tree] then
                conns_by_tree[tree] = {}
            end
            table.insert(conns_by_tree[tree], conn)
        end
    end

    -- Build views for each tab
    for i, tab_key in ipairs(talentData.tab_order) do
        local tab_id = core.make_tab_id(tab_key, i)
        local view_data = talentData.views[tab_key]

        if view_data then
            -- TALENTS mode view
            local view = content:tag("div")
                :addClass("iww-tree-view")
                :attr("data-iww-tree-tab", tab_id)
                :attr("data-iww-tree-mode", "talents")

            if i == 1 then
                view:addClass("active")
            end

            -- Section title (visible on mobile when tabs are unfolded)
            view:tag("h3"):addClass("iww-tree-section-title"):wikitext(view_data.label)

            -- Container for side-by-side sub-trees
            local sub_container = view:tag("div"):addClass("iww-talent-tree-sub-container")

            for _, tree_name in ipairs(view_data.trees) do
                local tree_meta = view_data.tree_info[tree_name]
                if tree_meta then
                    local section = sub_container:tag("div"):addClass("iww-talent-tree-sub-section")

                    -- Sub-category header
                    section:tag("h4"):addClass("iww-talent-tree-sub-header")
                        :wikitext(tree_meta.label)

                    -- Build canvas
                    local canvas = core.create_canvas(tree_meta.canvas_w, tree_meta.canvas_h)

                    -- Collect and place nodes for this tree
                    local tree_nodes = {}
                    for node_id, node_data in pairs(talentData.nodes) do
                        if node_data.tree == tree_name then
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

                    local tree_conns = conns_by_tree[tree_name] or {}
                    if #tree_conns > 0 then
                        core.add_connection_fallback(
                            canvas,
                            tree_meta.canvas_w,
                            tree_meta.canvas_h,
                            tree_nodes,
                            tree_conns,
                            view_data.line_method,
                            view_data.node_size,
                            TALENT_LINE_COLOR
                        )
                    end

                    core.populate_canvas(canvas, tree_nodes, view_data.node_size)

                    -- Set connection data for JS SVG rendering
                    if #tree_conns > 0 then
                        core.set_connection_data(
                            canvas, tree_conns,
                            view_data.line_method,
                            view_data.node_size
                        )
                    end

                    section:node(canvas)
                end
            end
        end
    end

    -- SOLO mode view (shared across tabs; the content is identical)
    local solo_view_data = talentData.views["Solo"]
    if solo_view_data then
        local view = content:tag("div")
            :addClass("iww-tree-view")
            :addClass("iww-talent-tree-solo-view")
            :attr("data-iww-tree-tab", "solo")
            :attr("data-iww-tree-mode", "solo")

        view:tag("h3"):addClass("iww-tree-section-title"):wikitext("Solo")

        local sub_container = view:tag("div"):addClass("iww-talent-tree-sub-container")

        for _, tree_name in ipairs(solo_view_data.trees) do
            local tree_meta = solo_view_data.tree_info[tree_name]
            if tree_meta then
                local section = sub_container:tag("div"):addClass("iww-talent-tree-sub-section")
                -- No sub-header for solo mode

                local canvas = core.create_canvas(tree_meta.canvas_w, tree_meta.canvas_h)

                local tree_nodes = {}
                for node_id, node_data in pairs(talentData.nodes) do
                    if node_data.tree == tree_name then
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

                local tree_conns = conns_by_tree[tree_name] or {}
                if #tree_conns > 0 then
                    core.add_connection_fallback(
                        canvas,
                        tree_meta.canvas_w,
                        tree_meta.canvas_h,
                        tree_nodes,
                        tree_conns,
                        solo_view_data.line_method,
                        solo_view_data.node_size,
                        TALENT_LINE_COLOR
                    )
                end

                core.populate_canvas(canvas, tree_nodes, solo_view_data.node_size)

                if #tree_conns > 0 then
                    core.set_connection_data(
                        canvas, tree_conns,
                        solo_view_data.line_method,
                        solo_view_data.node_size
                    )
                end

                section:node(canvas)
            end
        end
    end

    return talent_styles .. tostring(wrapper) .. '[[Category:Pages using TreeCore]]'
end

return p
