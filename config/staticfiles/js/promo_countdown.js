(function (global) {
    'use strict';

    function pad2(value) {
        return String(Math.max(0, value)).padStart(2, '0');
    }

    function buildCountdownSegments(remainingMs) {
        const totalSeconds = Math.max(0, Math.floor(remainingMs / 1000));
        const days = Math.floor(totalSeconds / 86400);
        const hours = Math.floor((totalSeconds % 86400) / 3600);
        const minutes = Math.floor((totalSeconds % 3600) / 60);
        const daysLabel = document.body.dataset.countdownDays || 'days';
        const hoursLabel = document.body.dataset.countdownHours || 'hours';
        const minutesLabel = document.body.dataset.countdownMinutes || 'minutes';
        const segments = [];
        if (days > 0) {
            segments.push({ value: String(days), label: daysLabel, short: 'd' });
        }
        segments.push({ value: pad2(hours), label: hoursLabel, short: 'h' });
        segments.push({ value: pad2(minutes), label: minutesLabel, short: 'm' });
        return segments;
    }

    function renderCountdownHtml(remainingMs) {
        const prefix = document.body.dataset.countdownPrefix || 'Ends in';
        const segments = buildCountdownSegments(remainingMs);
        const segmentsHtml = segments.map(function (segment) {
            return (
                '<span class="promo-countdown__seg" title="' + segment.label + '">' +
                    '<strong>' + segment.value + '</strong>' +
                    '<small>' + segment.short + '</small>' +
                '</span>'
            );
        }).join('<span class="promo-countdown__sep" aria-hidden="true">:</span>');
        return (
            '<span class="promo-countdown__prefix">' + prefix + '</span>' +
            '<span class="promo-countdown__clock">' + segmentsHtml + '</span>'
        );
    }

    function parsePromoEndsAt(rawValue) {
        const raw = String(rawValue || '').trim();
        if (!raw) {
            return NaN;
        }
        let endsAt = Date.parse(raw);
        if (!Number.isNaN(endsAt)) {
            return endsAt;
        }
        // Django sometimes serializes naive datetimes as "YYYY-MM-DD HH:MM:SS".
        endsAt = Date.parse(raw.replace(' ', 'T'));
        if (!Number.isNaN(endsAt)) {
            return endsAt;
        }
        return Date.parse(raw.replace(' ', 'T') + 'Z');
    }

    function tickPromoCountdowns() {
        const endedLabel = document.body.dataset.countdownEnded || 'Promotion ended';
        document.querySelectorAll('.js-promo-countdown').forEach(function (el) {
            const endsAt = parsePromoEndsAt(el.dataset.endsAt);
            const textEl = el.querySelector('.promo-countdown__text');
            if (!textEl || Number.isNaN(endsAt)) {
                el.hidden = true;
                return;
            }
            const remaining = endsAt - Date.now();
            if (remaining <= 0) {
                el.classList.add('promo-countdown--ended');
                textEl.textContent = endedLabel;
                return;
            }
            el.classList.remove('promo-countdown--ended');
            el.hidden = false;
            textEl.innerHTML = renderCountdownHtml(remaining);
        });
    }

    function initPromoCountdowns() {
        if (!document.querySelector('.js-promo-countdown')) {
            return;
        }
        tickPromoCountdowns();
        if (global.__promoCountdownTimer) {
            global.clearInterval(global.__promoCountdownTimer);
        }
        global.__promoCountdownTimer = global.setInterval(tickPromoCountdowns, 1000);
    }

    global.initPromoCountdowns = initPromoCountdowns;
}(window));
