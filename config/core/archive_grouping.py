"""Group completed/archived records by day, month, or year for archive UIs."""

from __future__ import annotations

from collections import OrderedDict
from datetime import date, datetime
from typing import Any, Callable, Iterable, Optional

from django.utils import timezone
from django.utils.translation import gettext as _


def _as_date(value) -> Optional[date]:
	if value is None:
		return None
	if isinstance(value, datetime):
		if timezone.is_aware(value):
			value = timezone.localtime(value)
		return value.date()
	if isinstance(value, date):
		return value
	return None


def archive_period_key(record_date: date, *, today: Optional[date] = None) -> tuple[str, str, str]:
	"""
	Return (kind, key, label) for a record date.

	- day: same calendar month as today
	- month: earlier months in the same year
	- year: previous years
	"""
	today = today or timezone.localdate()
	if record_date.year == today.year and record_date.month == today.month:
		return (
			'day',
			record_date.isoformat(),
			record_date.strftime('%b %d, %Y'),
		)
	if record_date.year == today.year:
		return (
			'month',
			f'{record_date.year:04d}-{record_date.month:02d}',
			record_date.strftime('%B %Y'),
		)
	return (
		'year',
		f'{record_date.year:04d}',
		str(record_date.year),
	)


def group_records_for_archive(
	records: Iterable[Any],
	*,
	date_getter: Callable[[Any], Any],
	today: Optional[date] = None,
) -> list[dict]:
	"""
	Group records newest-first into archive buckets.

	Each group: {kind, key, label, count, items}
	"""
	today = today or timezone.localdate()
	buckets: OrderedDict[str, dict] = OrderedDict()

	dated_rows = []
	for record in records:
		record_date = _as_date(date_getter(record))
		if record_date is None:
			continue
		dated_rows.append((record_date, record))

	dated_rows.sort(key=lambda row: row[0], reverse=True)

	for record_date, record in dated_rows:
		kind, key, label = archive_period_key(record_date, today=today)
		group = buckets.get(key)
		if group is None:
			group = {
				'kind': kind,
				'key': key,
				'label': label,
				'count': 0,
				'items': [],
			}
			buckets[key] = group
		group['items'].append(record)
		group['count'] += 1

	return list(buckets.values())


def archive_kind_heading(kind: str) -> str:
	if kind == 'day':
		return str(_('This month (by day)'))
	if kind == 'month':
		return str(_('Earlier this year (by month)'))
	return str(_('Previous years'))
