document.addEventListener('DOMContentLoaded', function () {
    const container = document.getElementById('navbarUrgentAlerts');
    const toggle = document.getElementById('navbarUrgentAlertsToggle');

    if (!container || !toggle) {
        return;
    }

    const markSeenUrl = container.dataset.markSeenUrl;
    const csrfToken = container.dataset.csrf || '';
    let markSeenRequest = null;

    // iPad/Safari fix: the toggle lives inside `.navbar-custom`, which uses
    // `overflow-x: clip` and is a fixed stacking context. With Bootstrap's default
    // (absolute strategy + clippingParents boundary) Popper positions the menu inside
    // that clipping context, so on Safari it gets clipped/hidden (portrait: it looks
    // like nothing opens; landscape: it renders behind the page content). Forcing a
    // fixed positioning strategy makes the menu escape the clipping ancestor and, with
    // the high z-index set in CSS, sit above everything in every orientation.
    let dropdown = null;
    if (window.bootstrap && bootstrap.Dropdown) {
        dropdown = bootstrap.Dropdown.getOrCreateInstance(toggle, {
            popperConfig: function (defaultConfig) {
                return Object.assign({}, defaultConfig, { strategy: 'fixed' });
            },
        });

        // Recalculate the position if the device is rotated (or the viewport
        // resizes) while the panel is open, so it never drifts off-screen.
        const reposition = function () {
            if (dropdown && toggle.classList.contains('show')) {
                try {
                    dropdown.update();
                } catch (error) {
                    // Popper instance not ready yet; ignore.
                }
            }
        };
        window.addEventListener('resize', reposition);
        window.addEventListener('orientationchange', reposition);
    }

    function getCookie(name) {
        const match = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
        return match ? decodeURIComponent(match[1]) : '';
    }

    function clearUnreadState() {
        const badge = document.getElementById('navbarUrgentAlertsBadge');
        if (badge) {
            badge.remove();
        }

        container.querySelectorAll('.navbar-urgent-alerts-item--unread').forEach(function (item) {
            item.classList.remove('navbar-urgent-alerts-item--unread');
        });

        container.querySelectorAll('.navbar-urgent-alerts-recent-item--unread').forEach(function (item) {
            item.classList.remove('navbar-urgent-alerts-recent-item--unread');
        });
    }

    toggle.addEventListener('show.bs.dropdown', function () {
        if (!markSeenUrl) {
            return;
        }

        if (markSeenRequest) {
            return;
        }

        markSeenRequest = fetch(markSeenUrl, {
            method: 'POST',
            headers: {
                'X-CSRFToken': csrfToken || getCookie('csrftoken'),
                'X-Requested-With': 'XMLHttpRequest',
            },
            credentials: 'same-origin',
        })
            .then(function (response) {
                if (!response.ok) {
                    throw new Error('mark seen failed');
                }
                return response.json();
            })
            .then(function () {
                clearUnreadState();
            })
            .catch(function () {
                // Keep unread styling if the request fails.
            })
            .finally(function () {
                markSeenRequest = null;
            });
    });
});
