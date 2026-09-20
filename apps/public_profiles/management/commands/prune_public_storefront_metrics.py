from datetime import date

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.public_profiles.models import PublicStorefrontDailyMetric


class Command(BaseCommand):
    help = "Delete aggregate storefront metric buckets older than the retention period."

    def handle(self, *args: object, **options: object) -> None:
        cutoff = subtract_months(
            timezone.localdate(),
            settings.PUBLIC_METRIC_RETENTION_MONTHS,
        )
        deleted, _details = PublicStorefrontDailyMetric.objects.filter(
            local_date__lt=cutoff
        ).delete()
        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} expired metric rows."))


def subtract_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 - months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = value.day
    while day > 28:
        try:
            return value.replace(year=year, month=month, day=day)
        except ValueError:
            day -= 1
    return value.replace(year=year, month=month, day=day)
