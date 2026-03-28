local p = {}
local core = require("Module:TreeCore")
local techData = require("Module:TechTree/TechData")

local TECH_LINE_COLOR = "#59bfff"

local function make_tech_node(node_id, data)
    if data.reroute then
        return mw.html.create("div")
            :attr("data-iww-tree-node-id", node_id)
            :css({ ["position"] = "absolute", ["width"] = "0", ["height"] = "0" })
    end

    local node = mw.html.create("div")
        :addClass("iww-tree-node")
        :attr("data-iww-tree-node-id", node_id)

    local display_name = data.name ~= "" and data.name or node_id
    local icon_file = data.icon ~= "" and (data.icon .. ".png") or "OrbitalNode_Unlocked_Normal.png"

    local icon_wt = string.format(
        "[[File:%s|64x64px|link=%s|alt=%s]]",
        icon_file, display_name, display_name
    )
    node:tag("span"):addClass("iww-tree-icon-wrap"):wikitext(icon_wt)

    node:tag("span"):addClass("iww-tree-page-link")
        :wikitext(string.format("[[%s|&#8203;]]", display_name))

    node:tag("span"):addClass("iww-tree-node-frame")
    node:tag("span"):addClass("iww-tree-glow")
    node:tag("span"):addClass("iww-tree-tooltip"):wikitext(display_name)

    return node
end

function p.render(frame)
    local tech_styles = frame:extensionTag(
        "templatestyles", "",
        { src = "Module:TechTree/styles.css" }
    )

    local wrapper = mw.html.create("div")
        :addClass("iww-tree-ui-wrapper")
        :addClass("iww-tree-tier-ui-wrapper")

    local top_bar = wrapper:tag("div"):addClass("iww-tree-tier-top-bar")
    local tab_group = top_bar:tag("div"):addClass("iww-tree-tier-tab-group")
    for i, tree_name in ipairs(techData.tier_order) do
        local tier_data = techData.tiers[tree_name]
        if tier_data then
            local button = tab_group:tag("div")
                :addClass("iww-tree-sidebar-button")
                :addClass("iww-tree-tier-btn")
                :attr("data-iww-tree-tab", core.make_tab_id(tree_name, i))

            if tier_data.icon ~= "" then
                button:tag("span"):addClass("iww-tree-tier-icon")
                    :wikitext(string.format("[[File:%s.png|frameless|18px|link=]]", tier_data.icon))
            end

            button:tag("span"):wikitext(string.upper(tier_data.label))

            if i == 1 then
                button:addClass("active")
            end
        end
    end

    local content_area = wrapper:tag("div"):addClass("iww-tree-content-area")

    local conns_by_tree = {}
    for _, conn in ipairs(techData.connections) do
        local from_node = techData.nodes[conn.from]
        if from_node then
            local tree = from_node.tree
            if not conns_by_tree[tree] then
                conns_by_tree[tree] = {}
            end
            table.insert(conns_by_tree[tree], conn)
        end
    end

    for i, tree_name in ipairs(techData.tier_order) do
        local tier_data = techData.tiers[tree_name]
        if tier_data then
            local view = content_area:tag("div")
                :addClass("iww-tree-view")
                :attr("data-iww-tree-tab", core.make_tab_id(tree_name, i))

            view:tag("h3"):addClass("iww-tree-section-title"):wikitext(string.upper(tier_data.label))

            local canvas = core.create_canvas(tier_data.canvas_w, tier_data.canvas_h)

            local tree_nodes = {}
            for node_id, node_data in pairs(techData.nodes) do
                if node_data.tree == tree_name then
                    local element = make_tech_node(node_id, node_data)
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
                    tier_data.canvas_w,
                    tier_data.canvas_h,
                    tree_nodes,
                    tree_conns,
                    tier_data.line_method,
                    tier_data.node_size,
                    TECH_LINE_COLOR
                )
            end

            core.populate_canvas(canvas, tree_nodes, tier_data.node_size)

            if #tree_conns > 0 then
                core.set_connection_data(
                    canvas, tree_conns,
                    tier_data.line_method,
                    tier_data.node_size
                )
            end

            view:node(canvas)
        end
    end

    return tech_styles .. tostring(wrapper) .. '[[Category:Pages using TreeCore]]'
end

return p
