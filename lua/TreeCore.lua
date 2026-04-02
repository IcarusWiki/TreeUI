-- ============================================================================
-- Module:TreeCore - Shared utilities for Workshop and Talent tree UIs
-- ============================================================================
-- Provides canvas-based absolute positioning, SVG connection data,
-- and common string utilities.
-- ============================================================================

local core = {}

local DEFAULT_LINE_COLOR = "#808080"

-- -- String utilities ----------------------------------------------------

function core.trim(value)
    return tostring(value or ""):gsub("^%s+", ""):gsub("%s+$", "")
end

function core.make_tab_id(cat, idx)
    local id = tostring(cat or ""):gsub("[^%w_-]", "")
    if id == "" then
        return "tab" .. tostring(idx or "")
    end
    return id
end

-- -- Canvas-based layout (absolute positioning) ---------------------------

--- Create a canvas container for absolutely-positioned nodes.
-- @param width   Canvas width in px
-- @param height  Canvas height in px
-- @return mw.html element
function core.create_canvas(width, height)
    return mw.html.create("div")
        :addClass("iww-tree-canvas")
        :css({
            ["position"]   = "relative",
            ["width"]      = width .. "px",
            ["height"]     = height .. "px",
        })
end

local function merge_intervals(intervals)
    if #intervals == 0 then
        return {}
    end

    table.sort(intervals, function(a, b)
        if a[1] == b[1] then
            return a[2] < b[2]
        end
        return a[1] < b[1]
    end)

    local merged = {
        { intervals[1][1], intervals[1][2] }
    }

    for i = 2, #intervals do
        local last = merged[#merged]
        local current = intervals[i]
        if current[1] < last[2] then
            last[2] = math.max(last[2], current[2])
        else
            merged[#merged + 1] = { current[1], current[2] }
        end
    end

    return merged
end

local function append_interval(segment_map, key, start_pos, end_pos)
    if start_pos == end_pos then
        return
    end

    if not segment_map[key] then
        segment_map[key] = {}
    end

    table.insert(segment_map[key], {
        math.min(start_pos, end_pos),
        math.max(start_pos, end_pos)
    })
end

local function build_connection_segments(nodes, connections, line_method, node_size)
    local centers = {}
    local node_visibility = {}
    local node_degrees = {}

    for _, edge in ipairs(connections) do
        node_degrees[edge.from] = (node_degrees[edge.from] or 0) + 1
        node_degrees[edge.to] = (node_degrees[edge.to] or 0) + 1
    end

    for _, item in ipairs(nodes) do
        if item.id then
            local w = item.w or node_size
            local h = item.h or node_size

            node_visibility[item.id] = not item.is_reroute
            centers[item.id] = {
                x = item.x + (w / 2),
                y = item.y + (h / 2),
            }
        end
    end

    local verticals = {}
    local horizontals = {}
    local diagonals = {}

    for _, conn in ipairs(connections) do
        local from_is_dead_end_reroute = node_visibility[conn.from] == false
            and (node_degrees[conn.from] or 0) <= 1
        local to_is_dead_end_reroute = node_visibility[conn.to] == false
            and (node_degrees[conn.to] or 0) <= 1
        if not from_is_dead_end_reroute and not to_is_dead_end_reroute then
            local from = centers[conn.from]
            local to = centers[conn.to]
            if from and to then
                local method = conn.method or line_method or "YThenX"

                if method == "YThenX" then
                    append_interval(verticals, from.x, from.y, to.y)
                    append_interval(horizontals, to.y, from.x, to.x)
                elseif method == "XThenY" then
                    append_interval(horizontals, from.y, from.x, to.x)
                    append_interval(verticals, to.x, from.y, to.y)
                else
                    table.insert(diagonals, {
                        x1 = from.x,
                        y1 = from.y,
                        x2 = to.x,
                        y2 = to.y,
                    })
                end
            end
        end
    end

    return verticals, horizontals, diagonals
end

local function get_sorted_keys(tbl)
    local keys = {}
    for key in pairs(tbl) do
        table.insert(keys, key)
    end
    table.sort(keys)
    return keys
end

local function make_line_tag(x1, y1, x2, y2, line_color)
    return tostring(
        mw.html.create("line")
            :attr("x1", tostring(x1))
            :attr("y1", tostring(y1))
            :attr("x2", tostring(x2))
            :attr("y2", tostring(y2))
            :attr("stroke", line_color or DEFAULT_LINE_COLOR)
            :attr("stroke-width", "2")
            :attr("stroke-linecap", "round")
    )
end

function core.add_connection_fallback(canvas, canvas_w, canvas_h, nodes, connections, line_method, node_size, line_color)
    if not canvas or not connections or #connections == 0 then
        return false
    end
    if not (mw and mw.svg and mw.svg.new) then
        return false
    end

    local verticals, horizontals, diagonals =
        build_connection_segments(nodes, connections, line_method, node_size)
    local parts = {}

    for _, x in ipairs(get_sorted_keys(verticals)) do
        local segs = merge_intervals(verticals[x])
        for _, seg in ipairs(segs) do
            parts[#parts + 1] = make_line_tag(x, seg[1], x, seg[2], line_color)
        end
    end

    for _, y in ipairs(get_sorted_keys(horizontals)) do
        local segs = merge_intervals(horizontals[y])
        for _, seg in ipairs(segs) do
            parts[#parts + 1] = make_line_tag(seg[1], y, seg[2], y, line_color)
        end
    end

    for _, diagonal in ipairs(diagonals) do
        parts[#parts + 1] = make_line_tag(
            diagonal.x1,
            diagonal.y1,
            diagonal.x2,
            diagonal.y2,
            line_color
        )
    end

    if #parts == 0 then
        return false
    end

    local image = mw.svg.new()
        :setAttribute("viewBox", string.format("0 0 %s %s", tostring(canvas_w), tostring(canvas_h)))
        :setAttribute("width", tostring(canvas_w))
        :setAttribute("height", tostring(canvas_h))
        :setAttribute("preserveAspectRatio", "none")
        :setContent(table.concat(parts))
        :setImgAttribute("class", "iww-tree-conn-fallback")
        :setImgAttribute("alt", "")
        :setImgAttribute(
            "style",
            "position:absolute;top:0;left:0;width:100%;height:100%;display:block;pointer-events:none;z-index:0;"
        )
        :toImage()

    canvas:addClass("has-conn-fallback")
    canvas:wikitext(image)
    return true
end

--- Place nodes on a canvas using absolute positioning.
-- Each node_data entry must have: x, y, and a pre-built 'element' (mw.html node).
-- @param canvas     The canvas container element
-- @param nodes      Array of { element, x, y } tables
-- @param node_size  Node size in px
function core.populate_canvas(canvas, nodes, node_size)
    for _, item in ipairs(nodes) do
        local w = item.w or node_size
        local h = item.h or node_size
        item.element:css({
            ["position"] = "absolute",
            ["left"]     = item.x .. "px",
            ["top"]      = item.y .. "px",
            ["width"]    = w .. "px",
            ["height"]   = h .. "px",
        })
        canvas:node(item.element)
    end
end

--- Attach connection data as JSON attributes for JS to render as SVG.
-- The server-rendered fallback still gets attached separately so mobile/no-JS
-- views can show connections without depending on the client redraw path.
-- @param canvas       The canvas container element
-- @param connections  Array of { from = "id", to = "id" } tables
-- @param line_method  "YThenX", "XThenY", or "ShortestDistance"
-- @param node_size    Node size in px (for center calculation)
function core.set_connection_data(canvas, connections, line_method, node_size)
    if not connections or #connections == 0 then
        return
    end

    -- Build JSON array manually (mw.text.jsonEncode may not be available)
    local parts = {}
    for _, conn in ipairs(connections) do
        if conn.method then
            parts[#parts + 1] = '{"from":"' .. conn.from .. '","to":"' .. conn.to .. '","method":"' .. conn.method .. '"}'
        else
            parts[#parts + 1] = '{"from":"' .. conn.from .. '","to":"' .. conn.to .. '"}'
        end
    end
    local json_str = "[" .. table.concat(parts, ",") .. "]"

    canvas:attr("data-iww-tree-connections", json_str)
    canvas:attr("data-iww-tree-node-size", tostring(node_size))
    canvas:attr("data-iww-tree-line-method", line_method or "YThenX")
end

return core
