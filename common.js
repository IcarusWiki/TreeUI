/* JavaScript for Tree UI - Tab Switching + Tooltips */
mw.loader.using(['jquery'], function () {
    $(function() {
        var MOBILE_BP = 768;  // Must match CSS @media breakpoint
        var DEFAULT_TREE_LINE_COLOR = '#b8860b';
        var WIKI_FILE_REDIRECT_PATH = '/wiki/Special:Redirect/file/';
        var EVENT_NS = '.iwwTreeUi';
        var observedWrapperEntries = [];
        var dragState = null;

        function sanitizeTabId(value) {
            return String(value || '').replace(/[^\w-]/g, '');
        }

        function getPanBuffer(contentEl, side) {
            if (!contentEl) return 0;
            var prop = '--iww-tree-pan-buffer-' + side;
            var value = parseInt(getComputedStyle(contentEl).getPropertyValue(prop), 10);
            return isNaN(value) ? 0 : value;
        }

        function resetContentPan($wrapper) {
            var contentEl = $wrapper.find('.iww-tree-content-area').get(0);
            if (!contentEl) {
                return;
            }
            contentEl.scrollLeft = getPanBuffer(contentEl, 'left');
            contentEl.scrollTop = getPanBuffer(contentEl, 'top');
        }

        function applyMobileScale($wrapper) {
            if (window.innerWidth > MOBILE_BP) return;
            if ($wrapper.hasClass('iww-talent-tree-ui-wrapper') || $wrapper.hasClass('iww-mission-tree-ui-wrapper')) return;

            var SCALE = 0.5;

            $wrapper.find('.iww-tree-canvas').each(function() {
                var canvas = this;
                var $c = $(canvas);
                if ($c.attr('data-iww-tree-mobile-scaled')) return;
                $c.attr('data-iww-tree-mobile-scaled', '1');

                // Scale canvas dimensions
                var w = parseInt(canvas.style.width) || 0;
                var h = parseInt(canvas.style.height) || 0;
                canvas.style.width = Math.round(w * SCALE) + 'px';
                canvas.style.height = Math.round(h * SCALE) + 'px';

                // Scale data-iww-tree-node-size so drawConnections computes correct centers
                var nodeSize = parseInt($c.attr('data-iww-tree-node-size')) || 80;
                $c.attr('data-iww-tree-node-size', Math.round(nodeSize * SCALE));

                // Scale all positioned children (nodes + reroute points)
                $c.find('[data-iww-tree-node-id]').each(function() {
                    var el = this;
                    var left = parseInt(el.style.left) || 0;
                    var top = parseInt(el.style.top) || 0;
                    el.style.left = Math.round(left * SCALE) + 'px';
                    el.style.top = Math.round(top * SCALE) + 'px';

                    if (el.style.width) {
                        var nw = parseInt(el.style.width) || 0;
                        var nh = parseInt(el.style.height) || 0;
                        el.style.width = Math.round(nw * SCALE) + 'px';
                        el.style.height = Math.round(nh * SCALE) + 'px';
                    }
                });

                // Scale icon dimensions to match smaller nodes
                $c.find('.iww-tree-node img').each(function() {
                    this.style.maxWidth = Math.round(64 * SCALE) + 'px';
                    this.style.maxHeight = Math.round(64 * SCALE) + 'px';
                });
            });
        }

        function refreshTreeLayout($wrapper) {
            function applyLayout() {
                resetContentPan($wrapper);
                applyMobileScale($wrapper);
                drawConnections($wrapper);
            }

            window.requestAnimationFrame(function() {
                applyLayout();
                window.requestAnimationFrame(applyLayout);
            });
        }

        function refreshAllTreeLayouts() {
            $('.iww-tree-ui-wrapper').each(function() {
                refreshTreeLayout($(this));
            });
        }

        function isWrapperVisible($wrapper) {
            var wrapperEl = $wrapper && $wrapper.get ? $wrapper.get(0) : null;
            if (!wrapperEl) {
                return false;
            }

            return wrapperEl.getClientRects().length > 0
                && wrapperEl.offsetWidth > 0
                && wrapperEl.offsetHeight > 0;
        }

        function scheduleWrapperRevealRefresh($wrapper) {
            if (!$wrapper || !$wrapper.length) {
                return;
            }

            if ($wrapper.data('iwwTreeRevealRefreshQueued')) {
                return;
            }

            $wrapper.data('iwwTreeRevealRefreshQueued', true);
            window.requestAnimationFrame(function() {
                window.requestAnimationFrame(function() {
                    $wrapper.removeData('iwwTreeRevealRefreshQueued');
                    if (!isWrapperVisible($wrapper)) {
                        return;
                    }

                    refreshTreeLayout($wrapper);
                    scheduleTalentContentHeightLock($wrapper);
                });
            });
        }

        function cleanupDetachedWrapperObservers() {
            observedWrapperEntries = observedWrapperEntries.filter(function(entry) {
                if (entry.el && entry.el.isConnected) {
                    return true;
                }

                if (entry.resizeObserver) {
                    entry.resizeObserver.disconnect();
                }

                return false;
            });
        }

        function attachWrapperVisibilityObservers($wrapper) {
            var wrapperEl = $wrapper.get(0);
            if (!wrapperEl || $wrapper.data('iwwTreeVisibilityObserverAttached')) {
                return;
            }

            $wrapper.data('iwwTreeVisibilityObserverAttached', true);
            cleanupDetachedWrapperObservers();

            if (window.ResizeObserver) {
                var resizeObserver = new window.ResizeObserver(function() {
                    scheduleWrapperRevealRefresh($wrapper);
                });
                resizeObserver.observe(wrapperEl);
                $wrapper.data('iwwTreeResizeObserver', resizeObserver);
                observedWrapperEntries.push({
                    el: wrapperEl,
                    resizeObserver: resizeObserver
                });
            }

            scheduleWrapperRevealRefresh($wrapper);
        }

        function normalizeMissionImageName(value) {
            var raw = String(value || '').trim();
            if (!raw) {
                return '';
            }

            if (raw.indexOf('/') === -1) {
                raw = raw.replace(/\.png$/i, '');
                if (!raw || /[\\/<>"'\u0000-\u001F]/.test(raw)) {
                    return '';
                }
                return raw;
            }

            try {
                var parsed = new URL(raw, window.location.href);
                if (parsed.origin !== window.location.origin) {
                    return '';
                }
                if (parsed.search || parsed.hash) {
                    return '';
                }
                if (parsed.pathname.indexOf(WIKI_FILE_REDIRECT_PATH) !== 0) {
                    return '';
                }

                var fileName = decodeURIComponent(parsed.pathname.slice(WIKI_FILE_REDIRECT_PATH.length));
                fileName = fileName.replace(/\.png$/i, '');
                if (!fileName || /[\\/<>"'\u0000-\u001F]/.test(fileName)) {
                    return '';
                }
                return fileName;
            } catch (e) {
                return '';
            }
        }

        function resolveMissionImageUrl(value) {
            var fileName = normalizeMissionImageName(value);
            if (!fileName) {
                return '';
            }

            return WIKI_FILE_REDIRECT_PATH + encodeURIComponent(fileName) + '.png';
        }

        function isSafeSvgColor(value) {
            var color = String(value || '').trim();
            if (!color) {
                return false;
            }

            var lower = color.toLowerCase();
            if (
                lower === 'inherit'
                || lower === 'initial'
                || lower === 'unset'
                || lower === 'revert'
                || lower === 'revert-layer'
            ) {
                return false;
            }

            if (window.CSS && typeof window.CSS.supports === 'function') {
                return window.CSS.supports('color', color);
            }

            var probe = document.createElement('span');
            probe.style.color = '';
            probe.style.color = color;
            return probe.style.color !== '';
        }

        function setMissionBackground($wrapper, imageValue) {
            var $bg = $wrapper.find('.iww-mission-tree-ui-background').first();
            if (!$bg.length) {
                return;
            }

            var url = resolveMissionImageUrl(imageValue);
            if (!url) {
                $bg.css('background-image', '');
                return;
            }

            $bg.css('background-image', "url('" + url + "')");
        }

        function restoreMissionBackground($wrapper) {
            if (!$wrapper.hasClass('iww-mission-tree-view-detail')) {
                var selectorBg = String(
                    $wrapper.attr('data-iww-mission-tree-selector-bg-url')
                    || $wrapper.attr('data-iww-mission-tree-selector-bg-image')
                    || ''
                );
                if (selectorBg) {
                    setMissionBackground($wrapper, selectorBg);
                    return;
                }
            }

            var activeBg = String(
                $wrapper.attr('data-iww-mission-tree-active-bg-url')
                || $wrapper.attr('data-iww-mission-tree-active-bg-image')
                || ''
            );
            if (activeBg) {
                setMissionBackground($wrapper, activeBg);
                return;
            }

            setMissionBackground(
                $wrapper,
                $wrapper.attr('data-iww-mission-tree-default-bg-url')
                || $wrapper.attr('data-iww-mission-tree-default-bg-image')
                || ''
            );
        }

        function setMissionSelectorCard($wrapper, tab) {
            if (!$wrapper.hasClass('iww-mission-tree-ui-wrapper')) {
                return;
            }

            var safeTab = sanitizeTabId(tab);
            if (!safeTab) {
                return;
            }

            $wrapper.attr('data-iww-mission-tree-selector-tab', safeTab);
            $wrapper.find('.iww-mission-tree-selector-card').removeClass('active');
            $wrapper.find('.iww-mission-tree-selector-card').filter(function() {
                return (this.getAttribute('data-iww-mission-tree-tab') || '') === safeTab;
            }).first().addClass('active');
        }

        function activateMissionView($wrapper, tab) {
            if (!$wrapper.hasClass('iww-mission-tree-ui-wrapper')) {
                return;
            }

            var $targetView = $wrapper.find('.iww-tree-view').filter(function() {
                return (this.getAttribute('data-iww-tree-tab') || '') === tab;
            }).first();

            if (!$targetView.length) {
                return;
            }

            var $targetCard = $wrapper.find('.iww-mission-tree-selector-card').filter(function() {
                return (this.getAttribute('data-iww-mission-tree-tab') || '') === tab;
            }).first();

            $wrapper.find('.iww-tree-view').removeClass('active');
            $targetView.addClass('active');
            setMissionSelectorCard($wrapper, tab);

            var bgImage = String(
                $targetView.attr('data-iww-mission-tree-bg-url')
                || $targetCard.attr('data-iww-mission-tree-bg-url')
                || $targetView.attr('data-iww-mission-tree-bg-image')
                || $targetCard.attr('data-iww-mission-tree-bg-image')
                || $wrapper.attr('data-iww-mission-tree-default-bg-url')
                || $wrapper.attr('data-iww-mission-tree-default-bg-image')
                || ''
            );

            $wrapper.attr('data-iww-mission-tree-active-tab', tab);
            $wrapper.attr('data-iww-mission-tree-active-bg-image', bgImage);
            $wrapper.attr('data-iww-mission-tree-active-bg-url', bgImage);
            $wrapper.attr('data-iww-mission-tree-selector-bg-image', bgImage);
            $wrapper.attr('data-iww-mission-tree-selector-bg-url', bgImage);
            setMissionBackground($wrapper, bgImage);
            $wrapper.addClass('iww-mission-tree-view-detail');
            hideFloatingTooltip();
            refreshTreeLayout($wrapper);
        }

        function initMissionWrapper($wrapper) {
            if (!$wrapper.hasClass('iww-mission-tree-ui-wrapper')) {
                return;
            }

            var defaultTab = sanitizeTabId(
                $wrapper.attr('data-iww-mission-tree-default-tab')
                || $wrapper.find('.iww-mission-tree-selector-card').first().attr('data-iww-mission-tree-tab')
                || $wrapper.find('.iww-tree-view').first().attr('data-iww-tree-tab')
            );

            if (defaultTab) {
                var $defaultCard = $wrapper.find('.iww-mission-tree-selector-card').filter(function() {
                    return (this.getAttribute('data-iww-mission-tree-tab') || '') === defaultTab;
                }).first();
                var $defaultView = $wrapper.find('.iww-tree-view').filter(function() {
                    return (this.getAttribute('data-iww-tree-tab') || '') === defaultTab;
                }).first();

                $wrapper.find('.iww-tree-view').removeClass('active');

                if ($defaultView.length) {
                    $defaultView.addClass('active');
                }
                setMissionSelectorCard($wrapper, defaultTab);

                $wrapper.attr('data-iww-mission-tree-active-tab', defaultTab);
                $wrapper.attr(
                    'data-iww-mission-tree-active-bg-image',
                    String(
                        ($defaultView.attr('data-iww-mission-tree-bg-url'))
                        || ($defaultCard.attr('data-iww-mission-tree-bg-url'))
                        || ($defaultView.attr('data-iww-mission-tree-bg-image'))
                        || ($defaultCard.attr('data-iww-mission-tree-bg-image'))
                        || ($wrapper.attr('data-iww-mission-tree-default-bg-url'))
                        || ($wrapper.attr('data-iww-mission-tree-default-bg-image'))
                        || ''
                    )
                );
                $wrapper.attr(
                    'data-iww-mission-tree-active-bg-url',
                    String(
                        ($defaultView.attr('data-iww-mission-tree-bg-url'))
                        || ($defaultCard.attr('data-iww-mission-tree-bg-url'))
                        || ($defaultView.attr('data-iww-mission-tree-bg-image'))
                        || ($defaultCard.attr('data-iww-mission-tree-bg-image'))
                        || ($wrapper.attr('data-iww-mission-tree-default-bg-url'))
                        || ($wrapper.attr('data-iww-mission-tree-default-bg-image'))
                        || ''
                    )
                );
                $wrapper.attr(
                    'data-iww-mission-tree-selector-bg-image',
                    String(
                        ($defaultCard.attr('data-iww-mission-tree-bg-url'))
                        || ($defaultView.attr('data-iww-mission-tree-bg-url'))
                        || ($defaultCard.attr('data-iww-mission-tree-bg-image'))
                        || ($defaultView.attr('data-iww-mission-tree-bg-image'))
                        || ($wrapper.attr('data-iww-mission-tree-default-bg-url'))
                        || ($wrapper.attr('data-iww-mission-tree-default-bg-image'))
                        || ''
                    )
                );
                $wrapper.attr(
                    'data-iww-mission-tree-selector-bg-url',
                    String(
                        ($defaultCard.attr('data-iww-mission-tree-bg-url'))
                        || ($defaultView.attr('data-iww-mission-tree-bg-url'))
                        || ($defaultView.attr('data-iww-mission-tree-bg-image'))
                        || ($defaultCard.attr('data-iww-mission-tree-bg-image'))
                        || ($wrapper.attr('data-iww-mission-tree-default-bg-url'))
                        || ($wrapper.attr('data-iww-mission-tree-default-bg-image'))
                        || ''
                    )
                );
            }

            $wrapper.removeClass('iww-mission-tree-view-detail');
            restoreMissionBackground($wrapper);
        }

        // -- Tooltip state ----------------------------------------------
        var activeTooltipNode = null;
        // Append inside .mw-parser-output so TemplateStyles scoped CSS applies
        var $tooltipParent = $('.mw-parser-output').first();
        if (!$tooltipParent.length) $tooltipParent = $('body');
        var $floatingTooltip = $('.iww-tree-tooltip-floating[data-iww-tree-global-tooltip="1"]').first();
        if (!$floatingTooltip.length) {
            $floatingTooltip = $('<div class="iww-tree-tooltip-floating" data-iww-tree-global-tooltip="1" aria-hidden="true"></div>')
                .hide();
        }
        $floatingTooltip.appendTo($tooltipParent);
        $('.iww-tree-tooltip-floating[data-iww-tree-global-tooltip="1"]').not($floatingTooltip).remove();

        function clearFloatingTooltipTheme() {
            $floatingTooltip.css({
                color: '',
                backgroundColor: '',
                borderColor: '',
                borderTopColor: ''
            });
        }

        function applyFloatingTooltipTheme(nodeEl) {
            var wrapperEl = $(nodeEl).closest('.iww-tree-ui-wrapper').get(0);
            if (!wrapperEl) {
                clearFloatingTooltipTheme();
                return;
            }

            var styles = getComputedStyle(wrapperEl);
            var accent = styles.getPropertyValue('--iww-tree-theme-accent').trim();
            var text = styles.getPropertyValue('--iww-tree-theme-text').trim();
            var bg = styles.getPropertyValue('--iww-tree-theme-tooltip-bg').trim();

            $floatingTooltip.css({
                color: text || '',
                backgroundColor: bg || '',
                borderColor: accent || '',
                borderTopColor: accent || ''
            });
        }

        function hideFloatingTooltip() {
            activeTooltipNode = null;
            $floatingTooltip
                .hide()
                .css({ left: '-9999px', top: '-9999px' })
                .removeClass('iww-talent-tree-tooltip-rich');
            clearFloatingTooltipTheme();
        }

        // -- Rich talent tooltip builder (DOM-based) ---------
        function buildTalentTooltipDOM(info) {
            var frag = document.createDocumentFragment();

            // Title bar (yellow rectangle with underglow)
            var nameEl = document.createElement('div');
            nameEl.className = 'iww-talent-tree-tip-name';
            nameEl.textContent = String(info.name || '');
            frag.appendChild(nameEl);

            // Description (white medium text)
            var desc = String(info.description || '');
            if (desc) {
                var descEl = document.createElement('div');
                descEl.className = 'iww-talent-tree-tip-desc';
                descEl.textContent = desc;
                frag.appendChild(descEl);
            }

            // Levels section (yellow text, separated by horizontal lines)
            if (info.levels && info.levels.length > 0) {
                var hr1 = document.createElement('hr');
                hr1.className = 'iww-talent-tree-tip-hr';
                frag.appendChild(hr1);

                var levelsEl = document.createElement('div');
                levelsEl.className = 'iww-talent-tree-tip-levels';
                for (var i = 0; i < info.levels.length; i++) {
                    var levelEl = document.createElement('div');
                    levelEl.className = 'iww-talent-tree-tip-level';
                    levelEl.textContent = String(info.levels[i] || '');
                    levelsEl.appendChild(levelEl);
                }
                frag.appendChild(levelsEl);

                var hr2 = document.createElement('hr');
                hr2.className = 'iww-talent-tree-tip-hr';
                frag.appendChild(hr2);
            }

            return frag;
        }

        // -- Tooltip positioning ----------------------------------------
        function positionFloatingTooltip(nodeEl) {
            if (!nodeEl || !nodeEl.isConnected) {
                hideFloatingTooltip();
                return;
            }

            // Check for rich talent data first
            var talentJson = nodeEl.getAttribute('data-iww-talent-tree-info');
            if (talentJson) {
                try {
                    var info = JSON.parse(talentJson);
                    clearFloatingTooltipTheme();
                    $floatingTooltip
                        .empty().append(buildTalentTooltipDOM(info))
                        .addClass('iww-talent-tree-tooltip-rich')
                        .show();
                } catch (e) {
                    hideFloatingTooltip();
                    return;
                }
            } else {
                // Simple text tooltip (workshop items)
                var text = String(nodeEl.getAttribute('data-iww-tree-tooltip') || '').trim();
                if (!text) {
                    hideFloatingTooltip();
                    return;
                }
                applyFloatingTooltipTheme(nodeEl);
                $floatingTooltip
                    .text(text)
                    .removeClass('iww-talent-tree-tooltip-rich')
                    .show();
            }

            var nodeRect = nodeEl.getBoundingClientRect();
            var tipRect = $floatingTooltip.get(0).getBoundingClientRect();
            var viewportPadding = 8;
            var left = nodeRect.left + (nodeRect.width / 2) - (tipRect.width / 2);
            var top = nodeRect.top - tipRect.height - 10;

            left = Math.max(viewportPadding, Math.min(window.innerWidth - tipRect.width - viewportPadding, left));
            top = Math.max(viewportPadding, top);

            $floatingTooltip.css({
                left: Math.round(left) + 'px',
                top: Math.round(top) + 'px'
            });
        }

        // -- Tooltip node setup -----------------------------------------
        function setupTooltipNodes($scope) {
            $scope.find('.iww-tree-node').each(function() {
                var $node = $(this);
                // Only set data-iww-tree-tooltip for non-talent nodes (talent nodes use data-iww-talent-tree-info)
                if (!this.hasAttribute('data-iww-talent-tree-info')) {
                    var text = $.trim($node.find('.iww-tree-tooltip').first().text());
                    if (!text) {
                        text = $.trim($node.find('[title]').first().attr('title') || '');
                    }
                    this.setAttribute('data-iww-tree-tooltip', text);
                }
            });

            $scope.find('.iww-tree-node a, .iww-tree-page-link a, .iww-tree-node img, .iww-talent-tree-tab-icon a, .iww-talent-tree-tab-icon img, .iww-tree-tier-icon a, .iww-tree-tier-icon img')
                .attr('draggable', 'false');

            $scope.find('.iww-tree-node [title]').removeAttr('title');

            $scope.find('.iww-tree-node')
                .off('.techTooltip')
                .on('mouseenter.techTooltip', function() {
                    activeTooltipNode = this;
                    positionFloatingTooltip(this);
                })
                .on('mousemove.techTooltip', function() {
                    if (activeTooltipNode === this) {
                        positionFloatingTooltip(this);
                    }
                })
                .on('mouseleave.techTooltip', function() {
                    if (activeTooltipNode === this) {
                        hideFloatingTooltip();
                    }
                });

            $scope.find('.iww-tree-node a, .iww-tree-page-link a')
                .off('.techTooltip')
                .on('focus.techTooltip', function() {
                    var node = $(this).closest('.iww-tree-node').get(0);
                    if (!node) {
                        return;
                    }
                    activeTooltipNode = node;
                    positionFloatingTooltip(node);
                })
                .on('blur.techTooltip', function() {
                    var node = $(this).closest('.iww-tree-node').get(0);
                    if (node && activeTooltipNode === node) {
                        hideFloatingTooltip();
                    }
                });

            $scope.find('.iww-tree-hover-tip')
                .off('.techTooltip')
                .on('mouseenter.techTooltip focus.techTooltip', function() {
                    activeTooltipNode = this;
                    positionFloatingTooltip(this);
                })
                .on('mousemove.techTooltip', function() {
                    if (activeTooltipNode === this) {
                        positionFloatingTooltip(this);
                    }
                })
                .on('mouseleave.techTooltip blur.techTooltip', function() {
                    if (activeTooltipNode !== this) {
                        return;
                    }

                    var node = $(this).closest('.iww-tree-node').get(0);
                    if (node && $(node).is(':hover')) {
                        activeTooltipNode = node;
                        positionFloatingTooltip(node);
                        return;
                    }

                    hideFloatingTooltip();
                });
        }

        // -- SVG connection line drawing ----------------------------------

        // Merge overlapping intervals: [[start,end], ...] -> merged list
        function mergeIntervals(intervals) {
            if (intervals.length === 0) return [];
            intervals.sort(function(a, b) { return a[0] - b[0]; });
            var merged = [intervals[0].slice()];
            for (var i = 1; i < intervals.length; i++) {
                var last = merged[merged.length - 1];
                if (intervals[i][0] < last[1]) {
                    last[1] = Math.max(last[1], intervals[i][1]);
                } else {
                    merged.push(intervals[i].slice());
                }
            }
            return merged;
        }

        function drawConnections($wrapper) {
            $wrapper.find('.iww-tree-canvas[data-iww-tree-connections]').each(function() {
                var $canvas = $(this);
                var canvasWidth = parseInt($canvas.css('width')) || parseInt(this.style.width) || 0;
                var canvasHeight = parseInt($canvas.css('height')) || parseInt(this.style.height) || 0;

                // Remove any previously drawn SVG
                $canvas.find('.iww-tree-conn-svg').remove();

                var connData = $canvas.attr('data-iww-tree-connections');
                if (!connData) return;

                var connections;
                try { connections = JSON.parse(connData); }
                catch (e) { return; }

                var nodeSize = parseInt($canvas.attr('data-iww-tree-node-size')) || 80;
                var lineMethod = $canvas.attr('data-iww-tree-line-method') || 'YThenX';
                var lineColor = String($canvas.attr('data-iww-tree-line-color') || '').trim();
                if (!lineColor) {
                    var wrapperEl = $canvas.closest('.iww-tree-ui-wrapper').get(0);
                    if (wrapperEl) {
                        lineColor = String(
                            getComputedStyle(wrapperEl).getPropertyValue('--iww-tree-theme-accent') || ''
                        ).trim();
                    }
                }
                if (!isSafeSvgColor(lineColor)) {
                    lineColor = DEFAULT_TREE_LINE_COLOR;
                }

                var nodeVisibility = {};
                var nodeDegrees = {};
                for (var c = 0; c < connections.length; c++) {
                    var edge = connections[c];
                    nodeDegrees[edge.from] = (nodeDegrees[edge.from] || 0) + 1;
                    nodeDegrees[edge.to] = (nodeDegrees[edge.to] || 0) + 1;
                }

                var svgNS = 'http://www.w3.org/2000/svg';
                var svg = document.createElementNS(svgNS, 'svg');
                svg.setAttribute('class', 'iww-tree-conn-svg');
                if (canvasWidth > 0 && canvasHeight > 0) {
                    svg.setAttribute('viewBox', '0 0 ' + canvasWidth + ' ' + canvasHeight);
                    svg.setAttribute('width', canvasWidth);
                    svg.setAttribute('height', canvasHeight);
                    svg.setAttribute('preserveAspectRatio', 'none');
                }
                svg.style.position = 'absolute';
                svg.style.top = '0';
                svg.style.left = '0';
                svg.style.width = '100%';
                svg.style.height = '100%';
                svg.style.display = 'block';
                svg.style.pointerEvents = 'none';
                svg.style.zIndex = '0';
                svg.style.overflow = 'visible';

                // Build node center lookup - use [data-iww-tree-node-id] to include reroute nodes
                var centers = {};
                $canvas.find('[data-iww-tree-node-id]').each(function() {
                    var $n = $(this);
                    var left = parseInt($n.css('left')) || 0;
                    var top = parseInt($n.css('top')) || 0;
                    var w = parseInt($n.css('width')) || nodeSize;
                    var h = parseInt($n.css('height')) || nodeSize;
                    nodeVisibility[$n.attr('data-iww-tree-node-id')] = $n.hasClass('iww-tree-node');
                    centers[$n.attr('data-iww-tree-node-id')] = {
                        x: left + w / 2,
                        y: top + h / 2
                    };
                });

                // Decompose connections into segments, then merge overlapping
                var verticals = {};   // keyed by X -> [[y1,y2], ...]
                var horizontals = {}; // keyed by Y -> [[x1,x2], ...]
                var diagonals = [];   // for ShortestDistance

                for (var i = 0; i < connections.length; i++) {
                    var conn = connections[i];
                    var fromIsDeadEndReroute = nodeVisibility[conn.from] === false && (nodeDegrees[conn.from] || 0) <= 1;
                    var toIsDeadEndReroute = nodeVisibility[conn.to] === false && (nodeDegrees[conn.to] || 0) <= 1;
                    if (fromIsDeadEndReroute || toIsDeadEndReroute) continue;

                    var from = centers[conn.from];
                    var to = centers[conn.to];
                    if (!from || !to) continue;

                    // Per-connection method override (from DrawMethodOverride)
                    var method = conn.method || lineMethod;

                    if (method === 'YThenX') {
                        // Vertical trunk at from.x, then horizontal branch
                        if (from.y !== to.y) {
                            var vx = from.x;
                            if (!verticals[vx]) verticals[vx] = [];
                            verticals[vx].push([Math.min(from.y, to.y), Math.max(from.y, to.y)]);
                        }
                        if (from.x !== to.x) {
                            var hy = to.y;
                            if (!horizontals[hy]) horizontals[hy] = [];
                            horizontals[hy].push([Math.min(from.x, to.x), Math.max(from.x, to.x)]);
                        }
                    } else if (method === 'XThenY') {
                        // Horizontal trunk at from.y, then vertical branch
                        if (from.x !== to.x) {
                            var hy2 = from.y;
                            if (!horizontals[hy2]) horizontals[hy2] = [];
                            horizontals[hy2].push([Math.min(from.x, to.x), Math.max(from.x, to.x)]);
                        }
                        if (from.y !== to.y) {
                            var vx2 = to.x;
                            if (!verticals[vx2]) verticals[vx2] = [];
                            verticals[vx2].push([Math.min(from.y, to.y), Math.max(from.y, to.y)]);
                        }
                    } else {
                        // ShortestDistance - straight line, no merging
                        diagonals.push({x1: from.x, y1: from.y, x2: to.x, y2: to.y});
                    }
                }

                // Merge overlapping segments and draw
                var key;
                for (key in verticals) {
                    var segs = mergeIntervals(verticals[key]);
                    var x = parseFloat(key);
                    for (var s = 0; s < segs.length; s++) {
                        var line = document.createElementNS(svgNS, 'line');
                        line.setAttribute('x1', x);
                        line.setAttribute('y1', segs[s][0]);
                        line.setAttribute('x2', x);
                        line.setAttribute('y2', segs[s][1]);
                        line.setAttribute('stroke', lineColor);
                        line.setAttribute('stroke-width', '2');
                        line.setAttribute('stroke-linecap', 'round');
                        svg.appendChild(line);
                    }
                }
                for (key in horizontals) {
                    var segs2 = mergeIntervals(horizontals[key]);
                    var y = parseFloat(key);
                    for (var s2 = 0; s2 < segs2.length; s2++) {
                        var line2 = document.createElementNS(svgNS, 'line');
                        line2.setAttribute('x1', segs2[s2][0]);
                        line2.setAttribute('y1', y);
                        line2.setAttribute('x2', segs2[s2][1]);
                        line2.setAttribute('y2', y);
                        line2.setAttribute('stroke', lineColor);
                        line2.setAttribute('stroke-width', '2');
                        line2.setAttribute('stroke-linecap', 'round');
                        svg.appendChild(line2);
                    }
                }
                for (var d = 0; d < diagonals.length; d++) {
                    var dline = document.createElementNS(svgNS, 'line');
                    dline.setAttribute('x1', diagonals[d].x1);
                    dline.setAttribute('y1', diagonals[d].y1);
                    dline.setAttribute('x2', diagonals[d].x2);
                    dline.setAttribute('y2', diagonals[d].y2);
                    dline.setAttribute('stroke', lineColor);
                    dline.setAttribute('stroke-width', '2');
                    dline.setAttribute('stroke-linecap', 'round');
                    svg.appendChild(dline);
                }

                // Insert SVG before the nodes so lines are behind
                $canvas.prepend(svg);
            });
        }

        // -- Helper: activate a talent view by tab + mode ---------------
        function activateTalentView($wrapper, tab, mode) {
            var $targetView;

            $wrapper.find('.iww-tree-view').removeClass('active');
            if (mode === 'solo') {
                $targetView = $wrapper.find('.iww-talent-tree-solo-view').first();
            } else {
                $targetView = $wrapper.find('.iww-tree-view').filter(function() {
                    return (this.getAttribute('data-iww-tree-tab') || '') === tab
                        && (this.getAttribute('data-iww-tree-mode') || '') === mode;
                }).first();
            }

            if ($targetView && $targetView.length) {
                $targetView.addClass('active');
            }

            hideFloatingTooltip();

            // Reset scroll position on tab/mode switch
            refreshTreeLayout($wrapper);
            scheduleTalentContentHeightLock($wrapper);
        }

        function setTalentTabGroupVisibility($wrapper, mode) {
            var $tabGroup = $wrapper.find('.iww-talent-tree-tab-group');
            if (!$tabGroup.length) {
                return;
            }

            if (mode === 'solo') {
                $tabGroup.hide();
            } else {
                $tabGroup.show();
            }
        }

        function getElementBottomWithinView(el, viewRect) {
            if (!el || !viewRect || !el.getClientRects().length) {
                return 0;
            }

            var rect = el.getBoundingClientRect();
            if (!rect.width && !rect.height) {
                return 0;
            }

            return Math.max(0, rect.bottom - viewRect.top);
        }

        function measureTalentViewHeight(viewEl) {
            if (!viewEl) {
                return 0;
            }

            var viewRect = viewEl.getBoundingClientRect();
            var viewStyles = getComputedStyle(viewEl);
            var paddingBottom = parseFloat(viewStyles.paddingBottom) || 0;
            var renderedBottom = 0;

            Array.prototype.forEach.call(viewEl.children, function(child) {
                renderedBottom = Math.max(
                    renderedBottom,
                    getElementBottomWithinView(child, viewRect)
                );
            });

            $(viewEl).find('.iww-tree-canvas').each(function() {
                renderedBottom = Math.max(
                    renderedBottom,
                    getElementBottomWithinView(this, viewRect)
                );
            });

            if (renderedBottom > 0) {
                return Math.ceil(renderedBottom + paddingBottom);
            }

            return Math.max(
                Math.ceil($(viewEl).outerHeight(true) || 0),
                Math.ceil(viewEl.scrollHeight || 0)
            );
        }

        // -- Lock talent content area height to tallest active view ---
        function lockTalentContentHeight($wrapper) {
            if (!$wrapper.hasClass('iww-talent-tree-ui-wrapper')) {
                return;
            }

            var $content = $wrapper.find('.iww-tree-content-area');
            if (!$content.length) return;

            var contentEl = $content.get(0);
            var $views = $wrapper.find('.iww-tree-view');
            if (!$views.length) {
                return;
            }

            if (window.innerWidth <= MOBILE_BP) {
                $content.css('min-height', '');
                $views.css('min-height', '');
                return;
            }

            var activeStates = [];
            var prevInlineMinHeights = [];
            var prevScrollLeft = contentEl.scrollLeft;
            var prevScrollTop = contentEl.scrollTop;
            var maxH = 0;

            $views.each(function(index) {
                activeStates[index] = this.classList.contains('active');
                prevInlineMinHeights[index] = this.style.minHeight;
                this.style.minHeight = '';
            });

            contentEl.style.minHeight = '';
            $views.removeClass('active');

            $views.each(function() {
                this.classList.add('active');

                var h = measureTalentViewHeight(this);
                if (h > maxH) maxH = h;

                this.classList.remove('active');
            });

            $views.each(function(index) {
                if (activeStates[index]) {
                    this.classList.add('active');
                }
            });

            contentEl.scrollLeft = prevScrollLeft;
            contentEl.scrollTop = prevScrollTop;

            if (maxH > 0) {
                $views.css('min-height', maxH + 'px');
                contentEl.style.minHeight = '';
            } else {
                $views.each(function(index) {
                    this.style.minHeight = prevInlineMinHeights[index] || '';
                });
            }
        }

        function scheduleTalentContentHeightLock($wrapper) {
            if (!$wrapper.hasClass('iww-talent-tree-ui-wrapper')) {
                return;
            }

            window.requestAnimationFrame(function() {
                window.requestAnimationFrame(function() {
                    lockTalentContentHeight($wrapper);
                });
            });
        }

        // -- Drag-to-pan -------------------------------------------------
        var DRAG_DEAD_ZONE = 4;

        function isPanBlockedTarget(target) {
            return $(target).closest('button, input, select, textarea, .iww-talent-tree-tab-btn, .iww-talent-tree-mode-btn, .iww-tree-sidebar-button').length > 0;
        }

        function beginDrag(el, clientX, clientY) {
            if (!el) {
                return;
            }

            if (dragState && dragState.el !== el) {
                endActiveDrag();
            }

            dragState = {
                el: el,
                startX: clientX,
                startY: clientY,
                scrollStartX: el.scrollLeft,
                scrollStartY: el.scrollTop,
                dragActive: false
            };
        }

        function updateActiveDrag(clientX, clientY, eventObj) {
            if (!dragState) {
                return false;
            }

            var dx = clientX - dragState.startX;
            var dy = clientY - dragState.startY;

            if (!dragState.dragActive) {
                if (Math.abs(dx) + Math.abs(dy) < DRAG_DEAD_ZONE) {
                    return false;
                }

                dragState.dragActive = true;
                dragState.el.style.cursor = 'grabbing';
                dragState.el.style.userSelect = 'none';
                hideFloatingTooltip();
            }

            if (eventObj && typeof eventObj.preventDefault === 'function') {
                eventObj.preventDefault();
            }

            dragState.el.scrollLeft = dragState.scrollStartX - dx;
            dragState.el.scrollTop = dragState.scrollStartY - dy;
            return true;
        }

        function endActiveDrag() {
            if (!dragState) {
                return;
            }

            var finishedDrag = dragState;
            dragState = null;

            if (finishedDrag.dragActive) {
                $(finishedDrag.el).data('iwwTreeSuppressClickUntil', Date.now() + 250);
            }

            finishedDrag.el.style.cursor = 'grab';
            finishedDrag.el.style.removeProperty('user-select');
        }

        function bindContentAreaInteractions($scope) {
            $scope.find('.iww-tree-content-area').addBack('.iww-tree-content-area').each(function() {
                var el = this;
                var $el = $(el);

                el.style.cursor = 'grab';
                el.style.touchAction = window.innerWidth > MOBILE_BP ? 'none' : '';

                $el.off(EVENT_NS);
                $el.on('mousedown' + EVENT_NS, function(e) {
                    if (window.innerWidth <= MOBILE_BP) return;
                    if (e.button !== 0) return;
                    if (isPanBlockedTarget(e.target)) return;

                    beginDrag(el, e.clientX, e.clientY);
                });

                $el.on('click' + EVENT_NS, 'a', function(e) {
                    var suppressClickUntil = $el.data('iwwTreeSuppressClickUntil') || 0;
                    if (Date.now() >= suppressClickUntil) {
                        return;
                    }

                    e.preventDefault();
                    e.stopPropagation();
                    $el.removeData('iwwTreeSuppressClickUntil');
                });

                $el.on('scroll' + EVENT_NS, function() {
                    if (activeTooltipNode) {
                        positionFloatingTooltip(activeTooltipNode);
                    }
                });

                if ($el.data('iwwTreeTouchBound')) {
                    return;
                }

                var touchStartHandler = function(e) {
                    if (window.innerWidth <= MOBILE_BP) return;
                    if (isPanBlockedTarget(e.target)) return;
                    var touch = e.touches[0];
                    if (!touch) {
                        return;
                    }

                    beginDrag(el, touch.clientX, touch.clientY);
                };

                var touchMoveHandler = function(e) {
                    if (!dragState || dragState.el !== el) {
                        return;
                    }

                    var touch = e.touches[0];
                    if (!touch) {
                        return;
                    }

                    updateActiveDrag(touch.clientX, touch.clientY, e);
                };

                var touchEndHandler = function() {
                    if (!dragState || dragState.el !== el) {
                        return;
                    }

                    endActiveDrag();
                };

                el.addEventListener('touchstart', touchStartHandler, { passive: true });
                el.addEventListener('touchmove', touchMoveHandler, { passive: false });
                el.addEventListener('touchend', touchEndHandler);
                el.addEventListener('touchcancel', touchEndHandler);

                $el.data('iwwTreeTouchBound', true);
            });
        }

        function initTreeWrapper($wrapper) {
            var isNewWrapper = !$wrapper.data('iwwTreeInitialized');
            $wrapper.addClass('js-ready');

            if ($wrapper.hasClass('iww-talent-tree-ui-wrapper')) {
                var $tabButtons = $wrapper.find('.iww-talent-tree-tab-btn');
                var $modeButtons = $wrapper.find('.iww-talent-tree-mode-btn');

                if ($tabButtons.length && !$tabButtons.filter('.active').length) {
                    $tabButtons.first().addClass('active');
                }
                if ($modeButtons.length && !$modeButtons.filter('.active').length) {
                    $modeButtons.first().addClass('active');
                }

                var mode = $modeButtons.filter('.active').attr('data-iww-tree-mode') || 'talents';
                var tab = sanitizeTabId(
                    $tabButtons.filter('.active').attr('data-iww-tree-tab')
                    || $tabButtons.first().attr('data-iww-tree-tab')
                );

                setTalentTabGroupVisibility($wrapper, mode);
                if ((!$wrapper.find('.iww-tree-view.active').length || isNewWrapper) && tab) {
                    activateTalentView($wrapper, tab, mode);
                }
            } else if ($wrapper.hasClass('iww-mission-tree-ui-wrapper')) {
                if (isNewWrapper || !$wrapper.find('.iww-tree-view.active').length) {
                    initMissionWrapper($wrapper);
                }
            } else {
                var $sidebarButtons = $wrapper.find('.iww-tree-sidebar-button');
                if ($sidebarButtons.length && !$sidebarButtons.filter('.active').length) {
                    $sidebarButtons.first().addClass('active');
                }
                if (!$wrapper.find('.iww-tree-view.active').length) {
                    $wrapper.find('.iww-tree-view').first().addClass('active');
                }
            }

            attachWrapperVisibilityObservers($wrapper);
            setupTooltipNodes($wrapper);
            bindContentAreaInteractions($wrapper);
            refreshTreeLayout($wrapper);
            scheduleTalentContentHeightLock($wrapper);
            $wrapper.data('iwwTreeInitialized', true);
        }

        function initTreeContent($content) {
            var $scope = $content && $content.jquery ? $content : $($content || document.body);
            var $wrappers = $scope.find('.iww-tree-ui-wrapper').addBack('.iww-tree-ui-wrapper');
            if (!$wrappers.length) {
                return;
            }

            cleanupDetachedWrapperObservers();
            $wrappers.each(function() {
                initTreeWrapper($(this));
            });
        }

        function bindGlobalHandlers() {
            var $doc = $(document);
            var $win = $(window);

            $doc.off(EVENT_NS);
            $win.off(EVENT_NS);

            $doc.on('mousemove' + EVENT_NS, function(e) {
                updateActiveDrag(e.clientX, e.clientY, e);
            });

            $doc.on('mouseup' + EVENT_NS, function() {
                endActiveDrag();
            });

            // -- Workshop sidebar tab switching --------------------------
            $doc.on('click' + EVENT_NS, '.iww-tree-sidebar-button', function() {
                var $button = $(this);
                var $wrapper = $button.closest('.iww-tree-ui-wrapper');
                var targetCat = sanitizeTabId($button.attr('data-iww-tree-tab'));
                if (!targetCat) {
                    return;
                }

                var $targetView;
                try {
                    $targetView = $wrapper.find('.iww-tree-view').filter(function() {
                        return (this.getAttribute('data-iww-tree-tab') || '') === targetCat;
                    }).first();
                } catch (err) {
                    return;
                }
                if (!$targetView || $targetView.length === 0) {
                    return;
                }

                $wrapper.find('.iww-tree-sidebar-button').removeClass('active');
                $wrapper.find('.iww-tree-view').removeClass('active');
                $button.addClass('active');
                $targetView.addClass('active');
                hideFloatingTooltip();

                refreshTreeLayout($wrapper);
            });

            $doc.on('click' + EVENT_NS, '.iww-mission-tree-selector-card', function() {
                var $card = $(this);
                var $wrapper = $card.closest('.iww-mission-tree-ui-wrapper');
                var tab = sanitizeTabId($card.attr('data-iww-mission-tree-tab'));
                if (!tab) {
                    return;
                }

                activateMissionView($wrapper, tab);
            });

            $doc.on('keydown' + EVENT_NS, '.iww-mission-tree-selector-card, .iww-mission-tree-back-button', function(e) {
                if (e.key !== 'Enter' && e.key !== ' ') {
                    return;
                }

                e.preventDefault();
                $(this).trigger('click');
            });

            $doc.on('mouseenter' + EVENT_NS + ' focusin' + EVENT_NS, '.iww-mission-tree-selector-card', function() {
                var $card = $(this);
                var $wrapper = $card.closest('.iww-mission-tree-ui-wrapper');
                var tab = sanitizeTabId($card.attr('data-iww-mission-tree-tab'));
                var bgImage = String($card.attr('data-iww-mission-tree-bg-image') || '');
                var bgUrl = String($card.attr('data-iww-mission-tree-bg-url') || bgImage);
                setMissionSelectorCard($wrapper, tab);
                $wrapper.attr('data-iww-mission-tree-selector-bg-image', bgImage);
                $wrapper.attr('data-iww-mission-tree-selector-bg-url', bgUrl);
                setMissionBackground($wrapper, bgUrl);
            });

            $doc.on('mouseleave' + EVENT_NS, '.iww-mission-tree-selector-grid', function() {
                restoreMissionBackground($(this).closest('.iww-mission-tree-ui-wrapper'));
            });

            $doc.on('focusout' + EVENT_NS, '.iww-mission-tree-selector-card', function() {
                var $wrapper = $(this).closest('.iww-mission-tree-ui-wrapper');
                window.requestAnimationFrame(function() {
                    if ($wrapper.find('.iww-mission-tree-selector-card:focus').length === 0) {
                        restoreMissionBackground($wrapper);
                    }
                });
            });

            $doc.on('click' + EVENT_NS, '.iww-mission-tree-back-button', function() {
                var $wrapper = $(this).closest('.iww-mission-tree-ui-wrapper');
                $wrapper.removeClass('iww-mission-tree-view-detail');
                hideFloatingTooltip();
                restoreMissionBackground($wrapper);
            });

            // -- Talent tree interactions --------------------------------
            $doc.on('click' + EVENT_NS, '.iww-talent-tree-tab-btn', function() {
                var $btn = $(this);
                var $wrapper = $btn.closest('.iww-talent-tree-ui-wrapper');
                var tab = sanitizeTabId($btn.attr('data-iww-tree-tab'));
                if (!tab) {
                    return;
                }

                var mode = $wrapper.find('.iww-talent-tree-mode-btn.active').attr('data-iww-tree-mode') || 'talents';

                $wrapper.find('.iww-talent-tree-tab-btn').removeClass('active');
                $btn.addClass('active');

                activateTalentView($wrapper, tab, mode);
            });

            $doc.on('click' + EVENT_NS, '.iww-talent-tree-mode-btn', function() {
                var $btn = $(this);
                var $wrapper = $btn.closest('.iww-talent-tree-ui-wrapper');
                var mode = $btn.attr('data-iww-tree-mode');
                if (!mode) {
                    return;
                }

                var tab = sanitizeTabId(
                    $wrapper.find('.iww-talent-tree-tab-btn.active').attr('data-iww-tree-tab')
                );

                $wrapper.find('.iww-talent-tree-mode-btn').removeClass('active');
                $btn.addClass('active');

                setTalentTabGroupVisibility($wrapper, mode);
                activateTalentView($wrapper, tab, mode);
            });

            // -- Navigation/drag suppression -----------------------------
            $doc.on('click' + EVENT_NS, '.iww-talent-tree-ui-wrapper .iww-tree-node a, .iww-talent-tree-ui-wrapper .iww-talent-tree-tab-icon a', function(e) {
                e.preventDefault();
                e.stopPropagation();
            });

            $doc.on('click' + EVENT_NS, '.iww-tree-ui-wrapper:not(.iww-talent-tree-ui-wrapper) .iww-tree-node a.new', function(e) {
                e.preventDefault();
            });

            $doc.on('dragstart' + EVENT_NS, '.iww-tree-ui-wrapper .iww-tree-node a, .iww-tree-ui-wrapper .iww-tree-page-link a, .iww-tree-ui-wrapper .iww-tree-node img, .iww-tree-ui-wrapper .iww-talent-tree-tab-icon a, .iww-tree-ui-wrapper .iww-talent-tree-tab-icon img, .iww-tree-ui-wrapper .iww-tree-tier-icon a, .iww-tree-ui-wrapper .iww-tree-tier-icon img', function(e) {
                e.preventDefault();
            });

            // -- Scroll/resize tooltip repositioning --------------------
            $win.on('load' + EVENT_NS + ' resize' + EVENT_NS + ' orientationchange' + EVENT_NS, function() {
                hideFloatingTooltip();
                cleanupDetachedWrapperObservers();
                refreshAllTreeLayouts();
                $('.iww-tree-content-area').each(function() {
                    this.style.touchAction = window.innerWidth > MOBILE_BP ? 'none' : '';
                });
                $('.iww-talent-tree-ui-wrapper').each(function() {
                    scheduleTalentContentHeightLock($(this));
                });
            });

            $win.on('scroll' + EVENT_NS + ' resize' + EVENT_NS, function() {
                if (activeTooltipNode) {
                    positionFloatingTooltip(activeTooltipNode);
                }
            });
        }

        bindGlobalHandlers();
        initTreeContent($(document.body));

        if (mw.hook && typeof mw.hook === 'function') {
            mw.hook('wikipage.content').add(function($content) {
                initTreeContent($content);
            });
        }

    });
});
