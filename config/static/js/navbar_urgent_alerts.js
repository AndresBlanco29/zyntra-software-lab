document.addEventListener('DOMContentLoaded', function () {
    const container = document.getElementById('navbarUrgentAlerts');
    const toggle = document.getElementById('navbarUrgentAlertsToggle');

    if (!container || !toggle) {
        return;
    }

    const markSeenUrl = container.dataset.markSeenUrl;
    const csrfToken = container.dataset.csrf || '';
    let markSeenRequest = null;

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
