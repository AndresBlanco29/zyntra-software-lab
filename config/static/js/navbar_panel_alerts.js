(function () {
    'use strict';

    var POLL_INTERVAL_MS = 25000;

    function getCookie(name) {
        var match = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
        return match ? decodeURIComponent(match[1]) : '';
    }

    function escapeHtml(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    /**
     * Keep the menu inside its dropdown parent. Moving it to <body> during open
     * breaks Bootstrap's show/hide on desktop and mobile.
     * Popper `strategy: 'fixed'` is enough to escape the navbar's overflow clip.
     */
    function initAlertDropdown(toggle) {
        if (!toggle || !window.bootstrap || !bootstrap.Dropdown) {
            return null;
        }

        var dropdown = bootstrap.Dropdown.getOrCreateInstance(toggle, {
            autoClose: 'outside',
            popperConfig: function (defaultConfig) {
                return Object.assign({}, defaultConfig, {
                    strategy: 'fixed',
                });
            },
        });

        var reposition = function () {
            if (toggle.classList.contains('show')) {
                try {
                    dropdown.update();
                } catch (error) {
                    // Ignore.
                }
            }
        };
        window.addEventListener('resize', reposition);
        window.addEventListener('orientationchange', reposition);
        return dropdown;
    }

    function postMarkSeen(url, csrfToken) {
        if (!url) {
            return Promise.resolve(null);
        }
        return fetch(url, {
            method: 'POST',
            headers: {
                'X-CSRFToken': csrfToken || getCookie('csrftoken'),
                'X-Requested-With': 'XMLHttpRequest',
            },
            credentials: 'same-origin',
        }).then(function (response) {
            if (!response.ok) {
                throw new Error('mark seen failed');
            }
            return response.json();
        });
    }

    function fetchFeed(url) {
        if (!url) {
            return Promise.resolve(null);
        }
        return fetch(url, {
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
            credentials: 'same-origin',
            cache: 'no-store',
        }).then(function (response) {
            if (!response.ok) {
                throw new Error('feed failed');
            }
            return response.json();
        });
    }

    function setBadge(badgeId, count) {
        var badge = document.getElementById(badgeId);
        var value = Math.max(0, parseInt(count, 10) || 0);
        if (value <= 0) {
            if (badge) {
                badge.remove();
            }
            return;
        }
        if (!badge) {
            var toggle = document.getElementById(badgeId.replace('Badge', 'Toggle'));
            if (!toggle) {
                return;
            }
            badge = document.createElement('span');
            badge.className = 'navbar-urgent-alerts-badge';
            badge.id = badgeId;
            toggle.appendChild(badge);
        }
        badge.textContent = String(value);
    }

    function initOrdersAlerts() {
        var container = document.getElementById('navbarUrgentAlerts');
        var toggle = document.getElementById('navbarUrgentAlertsToggle');
        if (!container || !toggle) {
            return;
        }

        initAlertDropdown(toggle);

        var markSeenUrl = container.dataset.markSeenUrl;
        var feedUrl = container.dataset.feedUrl;
        var csrfToken = container.dataset.csrf || '';
        var markSeenRequest = null;

        function clearUnreadState() {
            container.querySelectorAll('.navbar-urgent-alerts-item--unread').forEach(function (item) {
                item.classList.remove('navbar-urgent-alerts-item--unread');
            });
            document.querySelectorAll('#navbarUrgentAlertsRecent .navbar-urgent-alerts-recent-item--unread').forEach(function (item) {
                item.classList.remove('navbar-urgent-alerts-recent-item--unread');
            });
            setBadge('navbarUrgentAlertsBadge', 0);
        }

        function renderOrdersFeed(data) {
            if (!data || !data.success) {
                return;
            }
            setBadge('navbarUrgentAlertsBadge', data.total_count);

            var summaryHost = document.getElementById('navbarUrgentAlertsSummary');
            if (summaryHost) {
                if (data.summary_items && data.summary_items.length) {
                    summaryHost.innerHTML = '<div class="navbar-urgent-alerts-summary">' + data.summary_items.map(function (item) {
                        var count = item.unread_count || item.count || 0;
                        var unreadClass = item.unread_count ? ' navbar-urgent-alerts-item--unread' : '';
                        return (
                            '<a href="' + escapeHtml(item.url || '#') + '" class="navbar-urgent-alerts-item navbar-urgent-alerts-item--' +
                            escapeHtml(item.priority || 'medium') + unreadClass + '">' +
                            '<span class="navbar-urgent-alerts-item-copy">' +
                            '<span class="navbar-urgent-alerts-item-label">' + escapeHtml(item.label || '') + '</span>' +
                            '<span class="navbar-urgent-alerts-item-detail">' + escapeHtml(item.detail || '') + '</span>' +
                            '</span>' +
                            '<span class="navbar-urgent-alerts-item-count">' + escapeHtml(count) + '</span>' +
                            '</a>'
                        );
                    }).join('') + '</div>';
                } else {
                    summaryHost.innerHTML = '<div class="navbar-urgent-alerts-empty">No open orders right now.</div>';
                }
            }

            var recentWrap = document.getElementById('navbarUrgentAlertsRecentWrap');
            if (recentWrap) {
                if (data.recent_items && data.recent_items.length) {
                    recentWrap.innerHTML =
                        '<div class="navbar-urgent-alerts-recent-header">Latest open orders</div>' +
                        '<div class="navbar-urgent-alerts-recent" id="navbarUrgentAlertsRecent">' +
                        data.recent_items.map(function (item) {
                            var unreadClass = item.is_unread ? ' navbar-urgent-alerts-recent-item--unread' : '';
                            return (
                                '<a href="' + escapeHtml(item.url || '#') + '" class="navbar-urgent-alerts-recent-item' + unreadClass + '">' +
                                '<span class="navbar-urgent-alerts-recent-title">' + escapeHtml(item.title || '') + '</span>' +
                                '<span class="navbar-urgent-alerts-recent-message">' + escapeHtml(item.message || '') + '</span>' +
                                '</a>'
                            );
                        }).join('') +
                        '</div>';
                } else {
                    recentWrap.innerHTML = '';
                }
            }

            var ordersLink = document.getElementById('navbarUrgentAlertsOrdersLink');
            if (ordersLink && data.orders_url) {
                ordersLink.href = data.orders_url;
            }
        }

        toggle.addEventListener('show.bs.dropdown', function () {
            if (!markSeenUrl || markSeenRequest) {
                return;
            }
            markSeenRequest = postMarkSeen(markSeenUrl, csrfToken)
                .then(function () {
                    clearUnreadState();
                })
                .catch(function () {})
                .finally(function () {
                    markSeenRequest = null;
                });
        });

        if (feedUrl) {
            window.setInterval(function () {
                if (document.hidden) {
                    return;
                }
                fetchFeed(feedUrl).then(renderOrdersFeed).catch(function () {});
            }, POLL_INTERVAL_MS);
        }
    }

    function initCustomerRequestAlerts() {
        var container = document.getElementById('navbarCustomerRequestAlerts');
        var toggle = document.getElementById('navbarCustomerRequestAlertsToggle');
        if (!container || !toggle) {
            return;
        }

        initAlertDropdown(toggle);

        var markSeenUrl = container.dataset.markSeenUrl;
        var feedUrl = container.dataset.feedUrl;
        var csrfToken = container.dataset.csrf || '';
        var canManage = container.dataset.canManage === 'true';
        var labelView = container.dataset.labelView || 'View request';
        var labelApprove = container.dataset.labelApprove || 'Approve';
        var labelReject = container.dataset.labelReject || 'Reject';
        var labelEmpty = container.dataset.labelEmpty || 'No pending customer requests right now.';
        var labelLatest = container.dataset.labelLatest || 'Latest registration requests';
        var markSeenRequest = null;

        function clearUnreadHighlights() {
            document.querySelectorAll('#navbarCustomerRequestAlertsBody .navbar-urgent-alerts-recent-item--unread').forEach(function (item) {
                item.classList.remove('navbar-urgent-alerts-recent-item--unread');
            });
        }

        function renderCustomerFeed(data) {
            if (!data || !data.success) {
                return;
            }
            setBadge('navbarCustomerRequestAlertsBadge', data.pending_count);

            var body = document.getElementById('navbarCustomerRequestAlertsBody');
            if (!body) {
                return;
            }

            if (!data.items || !data.items.length) {
                body.innerHTML = '<div class="navbar-urgent-alerts-empty">' + escapeHtml(labelEmpty) + '</div>';
                return;
            }

            body.innerHTML =
                '<div class="navbar-urgent-alerts-recent-header">' + escapeHtml(labelLatest) + '</div>' +
                '<div class="navbar-urgent-alerts-recent navbar-customer-alerts-list">' +
                data.items.map(function (item) {
                    var unreadClass = item.is_unread ? ' navbar-urgent-alerts-recent-item--unread' : '';
                    var actions =
                        '<div class="navbar-customer-alert-actions">' +
                        '<a href="' + escapeHtml(item.url || '#') + '" class="navbar-customer-alert-action">' + escapeHtml(labelView) + '</a>';
                    if (canManage) {
                        actions +=
                            '<a href="' + escapeHtml(item.approve_url || item.url || '#') + '" class="navbar-customer-alert-action navbar-customer-alert-action--approve">' +
                            escapeHtml(labelApprove) + '</a>' +
                            '<a href="' + escapeHtml(item.reject_url || item.url || '#') + '" class="navbar-customer-alert-action navbar-customer-alert-action--reject">' +
                            escapeHtml(labelReject) + '</a>';
                    }
                    actions += '</div>';
                    return (
                        '<div class="navbar-customer-alert-card' + unreadClass + '">' +
                        '<a href="' + escapeHtml(item.url || '#') + '" class="navbar-customer-alert-main">' +
                        '<span class="navbar-urgent-alerts-recent-title">' + escapeHtml(item.customer_name || '') + '</span>' +
                        '<span class="navbar-customer-alert-company">' + escapeHtml(item.company || '') + '</span>' +
                        '<span class="navbar-urgent-alerts-recent-message">' + escapeHtml(item.email || '') + '</span>' +
                        (item.registered_at
                            ? '<span class="navbar-customer-alert-time">' + escapeHtml(item.registered_at) + '</span>'
                            : '') +
                        '</a>' +
                        actions +
                        '</div>'
                    );
                }).join('') +
                '</div>';
        }

        toggle.addEventListener('show.bs.dropdown', function () {
            if (!markSeenUrl || markSeenRequest) {
                return;
            }
            markSeenRequest = postMarkSeen(markSeenUrl, csrfToken)
                .then(function () {
                    clearUnreadHighlights();
                })
                .catch(function () {})
                .finally(function () {
                    markSeenRequest = null;
                });
        });

        if (feedUrl) {
            window.setInterval(function () {
                if (document.hidden) {
                    return;
                }
                fetchFeed(feedUrl).then(renderCustomerFeed).catch(function () {});
            }, POLL_INTERVAL_MS);
        }
    }

    document.addEventListener('DOMContentLoaded', function () {
        initOrdersAlerts();
        initCustomerRequestAlerts();
    });
})();
