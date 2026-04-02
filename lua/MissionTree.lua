local p = {}
local core = require("Module:TreeCore")
local missionData = require("Module:MissionTree/MissionData")

local FEATURE_ICONS = {
    ["New Frontiers"] = "T_FeatureLevelIcon_NewFrontiers3",
    ["Dangerous Horizons"] = "T_FeatureLevel_DH",
}

local function file_redirect_url(file_name)
    if not file_name or file_name == "" then
        return ""
    end

    return "/wiki/Special:Redirect/file/" .. file_name .. ".png"
end

local function set_preview_background(node, image_name)
    if not image_name or image_name == "" then
        return
    end

    node
        :addClass("has-image")
        :css("--iww-tree-mission-image-url", "url('" .. file_redirect_url(image_name) .. "')")
end

local function make_file_icon(file_name, size, alt_text)
    if not file_name or file_name == "" then
        return ""
    end

    return string.format(
        "[[File:%s.png|%s|link=|alt=%s]]",
        file_name,
        size,
        alt_text or ""
    )
end

local function css_key(value)
    return tostring(value or ""):lower():gsub("[^%w]+", "-"):gsub("^%-+", ""):gsub("%-+$", "")
end

local function add_world_skulls(parent, active_count, total_count, theme)
    if not active_count or active_count <= 0 then
        return
    end

    local wrap = parent:tag("span"):addClass("iww-mission-tree-world-danger")
    if theme and theme ~= "" then
        wrap:addClass("is-" .. css_key(theme))
    end

    for i = 1, total_count or 4 do
        local skull = wrap:tag("span"):addClass("iww-mission-tree-world-skull")
        if i <= active_count then
            skull:addClass("is-active")
        end
        skull:wikitext(make_file_icon("Icon_Skull", "18x18px", "Difficulty"))
    end
end

local function add_mission_skulls(parent, difficulty_rank, difficulty)
    local wrap = parent:tag("span"):addClass("iww-mission-tree-node-danger")
    local diff_key = css_key(difficulty)
    if diff_key ~= "" then
        wrap:addClass("is-" .. diff_key)
    end

    difficulty_rank = tonumber(difficulty_rank) or 1
    for i = 1, 5 do
        local skull = wrap:tag("span"):addClass("iww-mission-tree-node-danger-skull"):wikitext("&#9760;")
        if i <= difficulty_rank then
            skull:addClass("is-active")
        end
    end
end

local function make_mission_node(node_id, data)
    if data.reroute then
        return mw.html.create("div")
            :attr("data-iww-tree-node-id", node_id)
            :css({ ["position"] = "absolute", ["width"] = "0", ["height"] = "0" })
    end

    local node = mw.html.create("div")
        :addClass("iww-tree-node")
        :addClass("iww-mission-tree-node")
        :attr("data-iww-tree-node-id", node_id)

    local display_name = data.name ~= "" and data.name or node_id
    local page_name = data.page_name ~= "" and data.page_name or display_name
    local tooltip_text = data.mission ~= "" and data.mission
        or data.background ~= "" and data.background
        or display_name

    node:tag("span"):addClass("iww-tree-node-frame"):addClass("iww-mission-tree-node-frame")

    local card = node:tag("div"):addClass("iww-mission-tree-node-card")

    card:tag("div")
        :addClass("iww-mission-tree-node-header")
        :tag("div")
            :addClass("iww-mission-tree-node-title")
            :wikitext(display_name)

    local body = card:tag("div"):addClass("iww-mission-tree-node-body")
    local image = body:tag("span"):addClass("iww-mission-tree-node-image")
    if data.image and data.image ~= "" then
        set_preview_background(image, data.image)
    else
        image:addClass("is-empty")
    end

    local top = body:tag("div"):addClass("iww-mission-tree-node-top")
    if data.tech and data.tech ~= "" then
        top:tag("span"):addClass("iww-mission-tree-node-tech"):wikitext(data.tech)
    else
        top:tag("span"):addClass("iww-mission-tree-node-tech"):addClass("is-empty")
    end
    add_mission_skulls(top, data.difficulty_rank, data.difficulty)

    local mission_types = data.types or {}
    if #mission_types > 0 then
        local rail = body:tag("div"):addClass("iww-mission-tree-node-rail")
        for _, mission_type in ipairs(mission_types) do
            if mission_type.icon and mission_type.icon ~= "" then
                local label = mission_type.label ~= "" and mission_type.label or mission_type.name or ""
                rail:tag("span")
                    :addClass("iww-mission-tree-node-type")
                    :addClass("iww-tree-hover-tip")
                    :attr("data-iww-tree-tooltip", label)
                    :attr("tabindex", "0")
                    :wikitext(make_file_icon(mission_type.icon, "15x15px", label))
            end
        end
    end

    local footer = card:tag("div"):addClass("iww-mission-tree-node-footer")
    local rewards = data.rewards or {}
    local reward_wrap = footer:tag("div"):addClass("iww-mission-tree-node-rewards")
    for _, reward in ipairs(rewards) do
        local label = reward.label ~= "" and reward.label or reward.name or ""
        local chip = reward_wrap:tag("span")
            :addClass("iww-mission-tree-node-reward")
            :addClass("iww-mission-tree-node-reward--" .. css_key(reward.name))
            :attr("title", label)

        if reward.icon and reward.icon ~= "" then
            chip:tag("span")
                :addClass("iww-mission-tree-node-reward-icon")
                :wikitext(make_file_icon(reward.icon, "13x13px", label))
        end

        chip:tag("span")
            :addClass("iww-mission-tree-node-reward-amount")
            :wikitext(tostring(reward.amount or ""))
    end

    local duration = footer:tag("div"):addClass("iww-mission-tree-node-duration")
    duration:tag("span"):addClass("iww-mission-tree-node-duration-value"):wikitext(data.duration or "00 00 00")
    duration:tag("span"):addClass("iww-mission-tree-node-duration-label"):wikitext("DAYS HOURS MINS")

    node:tag("span"):addClass("iww-tree-page-link")
        :wikitext(string.format("[[%s|&#8203;]]", page_name))
    node:tag("span"):addClass("iww-tree-glow")
    node:tag("span"):addClass("iww-tree-tooltip"):wikitext(tooltip_text)

    return node
end

function p.render(frame)
    local mission_styles = frame:extensionTag(
        "templatestyles", "",
        { src = "Module:MissionTree/styles.css" }
    )

    local wrapper = mw.html.create("div")
        :addClass("iww-tree-ui-wrapper")
        :addClass("iww-mission-tree-ui-wrapper")

    local default_tree = missionData.region_order[1] or ""
    local default_region = missionData.regions[default_tree] or {}
    wrapper:attr("data-iww-mission-tree-default-tab", core.make_tab_id(default_tree, 1))
    wrapper:attr("data-iww-mission-tree-default-bg-image", default_region.background_image or "")
    wrapper:attr("data-iww-mission-tree-default-bg-url", file_redirect_url(default_region.background_image or ""))

    wrapper:tag("div"):addClass("iww-mission-tree-ui-background")

    local selector = wrapper:tag("div"):addClass("iww-mission-tree-selector-surface")
    local selector_grid = selector:tag("div"):addClass("iww-mission-tree-selector-grid")

    for i, tree_name in ipairs(missionData.region_order) do
        local region = missionData.regions[tree_name]
        if region then
            local selector_shell = selector_grid:tag("div")
                :addClass("iww-mission-tree-selector-shell")

            selector_shell:tag("div"):addClass("iww-mission-tree-selector-world-label")
                :wikitext(string.upper(region.label) .. ".")

            local selector_card = selector_shell:tag("div")
                :addClass("iww-mission-tree-selector-card")
                :attr("role", "button")
                :attr("tabindex", "0")
                :attr("data-iww-mission-tree-tab", core.make_tab_id(tree_name, i))
                :attr("data-iww-mission-tree-bg-image", region.background_image or "")
                :attr("data-iww-mission-tree-bg-url", file_redirect_url(region.background_image or ""))

            if i == 1 then
                selector_card:addClass("active")
            end

            local preview = selector_card:tag("span"):addClass("iww-mission-tree-selector-preview")
            if region.selector_image ~= "" then
                preview:tag("span")
                    :addClass("iww-mission-tree-selector-preview-media")
                    :wikitext(make_file_icon(region.selector_image, "300x405px", region.label .. " terrain"))
            else
                preview:addClass("is-empty")
            end

            local copy = selector_card:tag("span"):addClass("iww-mission-tree-selector-copy")
            local top = copy:tag("span"):addClass("iww-mission-tree-selector-top")
            local count = top:tag("span"):addClass("iww-mission-tree-selector-count")
            local count_icon = count:tag("span"):addClass("iww-mission-tree-selector-count-icon")
            count_icon:tag("span")
                :addClass("iww-mission-tree-selector-count-diamond")
                :addClass("is-fill")
            count_icon:tag("span")
                :addClass("iww-mission-tree-selector-count-diamond")
                :addClass("is-outline")
            count:tag("span"):addClass("iww-mission-tree-selector-count-text")
                :wikitext(region.mission_count_label or (tostring(region.mission_count) .. "/" .. tostring(region.mission_count)))
            add_world_skulls(
                top,
                tonumber(region.selector_difficulty) or 0,
                4,
                region.theme or ""
            )

            if region.selector_description and region.selector_description ~= "" then
                copy:tag("span"):addClass("iww-mission-tree-selector-desc")
                    :wikitext(region.selector_description)
            end

            local bottom = copy:tag("span"):addClass("iww-mission-tree-selector-bottom")
            if region.feature and region.feature ~= "" then
                local feature = bottom:tag("span"):addClass("iww-mission-tree-selector-feature")
                local feature_icon = FEATURE_ICONS[region.feature]
                if feature_icon then
                    feature:tag("span")
                        :addClass("iww-mission-tree-selector-feature-icon")
                        :wikitext(make_file_icon(feature_icon, "18x18px", region.feature))
                end
                feature:tag("span")
                    :addClass("iww-mission-tree-selector-feature-label")
                    :wikitext(string.upper(region.feature))
            end
        end
    end

    local detail = wrapper:tag("div"):addClass("iww-mission-tree-detail-surface")
    detail:tag("div"):addClass("iww-mission-tree-detail-corners")
    detail:tag("div")
        :addClass("iww-mission-tree-back-button")
        :attr("role", "button")
        :attr("tabindex", "0")
        :wikitext("BACK")

    local content_area = detail:tag("div"):addClass("iww-tree-content-area")

    local conns_by_tree = {}
    for _, conn in ipairs(missionData.connections) do
        local from_node = missionData.nodes[conn.from]
        if from_node then
            local tree = from_node.tree
            if not conns_by_tree[tree] then
                conns_by_tree[tree] = {}
            end
            table.insert(conns_by_tree[tree], conn)
        end
    end

    for i, tree_name in ipairs(missionData.region_order) do
        local region = missionData.regions[tree_name]
        if region then
            local view = content_area:tag("div")
                :addClass("iww-tree-view")
                :addClass("iww-mission-tree-view")
                :attr("data-iww-tree-tab", core.make_tab_id(tree_name, i))
                :attr("data-iww-mission-tree-bg-image", region.background_image or "")
                :attr("data-iww-mission-tree-bg-url", file_redirect_url(region.background_image or ""))
                :attr("data-iww-mission-tree-theme", region.theme or "")

            if i == 1 then
                view:addClass("active")
            end

            local canvas = core.create_canvas(region.canvas_w, region.canvas_h)
            local tree_nodes = {}

            for node_id, node_data in pairs(missionData.nodes) do
                if node_data.tree == tree_name then
                    local element = make_mission_node(node_id, node_data)
                    local item = {
                        id = node_id,
                        element = element,
                        x = node_data.x,
                        y = node_data.y,
                    }
                    if node_data.w then
                        item.w = node_data.w
                    end
                    if node_data.h then
                        item.h = node_data.h
                    end
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
                    region.canvas_w,
                    region.canvas_h,
                    tree_nodes,
                    tree_conns,
                    region.line_method,
                    region.node_size
                )
            end

            core.populate_canvas(canvas, tree_nodes, region.node_size)

            if #tree_conns > 0 then
                core.set_connection_data(
                    canvas,
                    tree_conns,
                    region.line_method,
                    region.node_size
                )
            end

            view:node(canvas)
        end
    end

    return mission_styles .. tostring(wrapper) .. "[[Category:Pages using TreeCore]]"
end

return p
