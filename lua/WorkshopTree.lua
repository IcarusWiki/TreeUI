local p = {}
local core = require("Module:TreeCore")
local workshopData = require("Module:WorkshopTree/WorkshopData")

local WORKSHOP_LINE_COLOR = "#b580c9"

-- ============================================================================
-- WORKSHOP NODE BUILDER
-- ============================================================================

local function make_workshop_node(node_id, data)
    -- Reroute nodes: invisible position anchors for line routing
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

    -- Icon
    local icon_wt = string.format(
        '[[File:%s|64x64px|link=%s|alt=%s]]',
        icon_file, display_name, display_name
    )
    node:tag("span"):addClass("iww-tree-icon-wrap"):wikitext(icon_wt)

    -- Page link
    node:tag("span"):addClass("iww-tree-page-link")
        :wikitext(string.format("[[%s|&#8203;]]", display_name))

    -- Frame
    node:tag("span"):addClass("iww-tree-node-frame")

    -- Glow
    node:tag("span"):addClass("iww-tree-glow")

    -- Tooltip
    node:tag("span"):addClass("iww-tree-tooltip"):wikitext(display_name)

    return node
end

-- ============================================================================
-- RENDER FUNCTION
-- ============================================================================

function p.render(frame)
    local workshop_styles = frame:extensionTag(
        "templatestyles", "",
        { src = "Module:WorkshopTree/styles.css" }
    )

    local wrapper = mw.html.create("div")
        :addClass("iww-tree-ui-wrapper")
        :addClass("iww-workshop-tree-ui-wrapper")

    -- SIDEBAR
    local sidebar = wrapper:tag("div"):addClass("iww-tree-sidebar")
    sidebar:tag("div"):addClass("iww-tree-sidebar-title"):wikitext("WORKSHOP")

    for i, cat_key in ipairs(workshopData.category_order) do
        local cat_data = workshopData.categories[cat_key]
        if cat_data then
            local tab_id = core.make_tab_id(cat_key, i)
            local btn = sidebar:tag("div")
                :addClass("iww-tree-sidebar-button")
                :attr("data-iww-tree-tab", tab_id)
                :wikitext(cat_data.label)
            if i == 1 then
                btn:addClass("active")
            end
        end
    end

    -- CONTENT AREA
    local content_area = wrapper:tag("div"):addClass("iww-tree-content-area")

    -- Index connections by tree
    local conns_by_tree = {}
    for _, conn in ipairs(workshopData.connections) do
        local from_node = workshopData.nodes[conn.from]
        if from_node then
            local tree = from_node.tree
            if not conns_by_tree[tree] then
                conns_by_tree[tree] = {}
            end
            table.insert(conns_by_tree[tree], conn)
        end
    end

    for i, cat_key in ipairs(workshopData.category_order) do
        local cat_data = workshopData.categories[cat_key]
        if cat_data then
            local tab_id = core.make_tab_id(cat_key, i)
            local view = content_area:tag("div")
                :addClass("iww-tree-view")
                :attr("data-iww-tree-tab", tab_id)

            -- Section title (visible without JS)
            view:tag("h3"):addClass("iww-tree-section-title"):wikitext(cat_data.label)

            -- Build canvas for this category
            local canvas = core.create_canvas(cat_data.canvas_w, cat_data.canvas_h)

            -- Collect nodes for this tree
            local tree_nodes = {}
            for node_id, node_data in pairs(workshopData.nodes) do
                if node_data.tree == cat_key then
                    local element = make_workshop_node(node_id, node_data)
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

            local tree_conns = conns_by_tree[cat_key] or {}
            if #tree_conns > 0 then
                core.add_connection_fallback(
                    canvas,
                    cat_data.canvas_w,
                    cat_data.canvas_h,
                    tree_nodes,
                    tree_conns,
                    cat_data.line_method,
                    cat_data.node_size,
                    WORKSHOP_LINE_COLOR
                )
            end

            core.populate_canvas(canvas, tree_nodes, cat_data.node_size)

            -- Connection data for JS SVG rendering
            if #tree_conns > 0 then
                core.set_connection_data(
                    canvas, tree_conns,
                    cat_data.line_method,
                    cat_data.node_size
                )
            end

            view:node(canvas)
        end
    end

    return workshop_styles .. tostring(wrapper) .. '[[Category:Pages using TreeCore]]'
end

return p
