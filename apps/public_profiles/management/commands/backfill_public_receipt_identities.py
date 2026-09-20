from django.core.management.base import BaseCommand
from django.db import transaction

from apps.public_profiles.models import (
    PublicReturnReceiptIdentity,
    PublicSaleReceiptIdentity,
)
from apps.public_profiles.services import (
    get_or_create_return_receipt_identity,
    get_or_create_sale_receipt_identity,
)
from apps.sales.models import InternalReceipt, InternalReturnReceipt


class Command(BaseCommand):
    help = "Create immutable public identities for existing internal receipts."

    @transaction.atomic
    def handle(self, *args: object, **options: object) -> None:
        sale_count = 0
        return_count = 0
        sale_receipts = InternalReceipt.objects.exclude(
            id__in=PublicSaleReceiptIdentity.objects.values("receipt_id")
        ).select_related("business")
        for sale_receipt in sale_receipts.iterator():
            get_or_create_sale_receipt_identity(sale_receipt)
            sale_count += 1
        return_receipts = InternalReturnReceipt.objects.exclude(
            id__in=PublicReturnReceiptIdentity.objects.values("receipt_id")
        ).select_related("business")
        for return_receipt in return_receipts.iterator():
            get_or_create_return_receipt_identity(return_receipt)
            return_count += 1
        self.stdout.write(
            self.style.SUCCESS(
                f"Created {sale_count} sale and {return_count} return receipt identities."
            )
        )
