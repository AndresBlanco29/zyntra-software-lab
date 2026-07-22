from datetime import date, datetime

from django.test import SimpleTestCase

from config.core.archive_grouping import archive_period_key, group_records_for_archive


class ArchiveGroupingTests(SimpleTestCase):
	def test_groups_current_month_by_day_earlier_months_by_month_and_prior_years(self):
		today = date(2026, 7, 22)
		records = [
			{'id': 1, 'when': datetime(2026, 7, 22, 10, 0)},
			{'id': 2, 'when': datetime(2026, 7, 1, 9, 0)},
			{'id': 3, 'when': datetime(2026, 5, 10, 12, 0)},
			{'id': 4, 'when': datetime(2025, 12, 1, 8, 0)},
		]
		groups = group_records_for_archive(
			records,
			date_getter=lambda row: row['when'],
			today=today,
		)
		kinds = [group['kind'] for group in groups]
		self.assertEqual(kinds.count('day'), 2)
		self.assertEqual(kinds.count('month'), 1)
		self.assertEqual(kinds.count('year'), 1)
		self.assertEqual(groups[0]['key'], '2026-07-22')
		self.assertEqual(archive_period_key(date(2026, 5, 10), today=today)[0], 'month')
		self.assertEqual(archive_period_key(date(2024, 1, 1), today=today)[0], 'year')
