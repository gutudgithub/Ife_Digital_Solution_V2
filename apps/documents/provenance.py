from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from apps.documents.models import (
    CapturedDocument,
    DocumentTranscription,
    TranscriptionStatus,
)
from apps.expenses.models import OperatingExpense
from apps.purchasing.models import Purchase


def purchase_has_confirmed_source(purchase: Purchase) -> bool:
    return DocumentTranscription.objects.filter(
        business=purchase.business,
        target_purchase=purchase,
        status=TranscriptionStatus.CONFIRMED,
    ).exists()


def expense_has_confirmed_source(expense: OperatingExpense) -> bool:
    return DocumentTranscription.objects.filter(
        business=expense.business,
        target_expense=expense,
        status=TranscriptionStatus.CONFIRMED,
    ).exists()


def purchase_confirmed_source_document(purchase: Purchase) -> CapturedDocument | None:
    transcription = (
        DocumentTranscription.objects.filter(
            business=purchase.business,
            target_purchase=purchase,
            status=TranscriptionStatus.CONFIRMED,
        )
        .select_related("document")
        .first()
    )
    return transcription.document if transcription is not None else None


def expense_confirmed_source_document(expense: OperatingExpense) -> CapturedDocument | None:
    transcription = (
        DocumentTranscription.objects.filter(
            business=expense.business,
            target_expense=expense,
            status=TranscriptionStatus.CONFIRMED,
        )
        .select_related("document")
        .first()
    )
    return transcription.document if transcription is not None else None


def require_purchase_not_confirmed_source(purchase: Purchase) -> None:
    if purchase_has_confirmed_source(purchase):
        raise ValidationError(
            _(
                "This purchase matches owner-confirmed document evidence. "
                "Cancel it and start a replacement transcription to correct it."
            )
        )


def require_expense_not_confirmed_source(expense: OperatingExpense) -> None:
    if expense_has_confirmed_source(expense):
        raise ValidationError(
            _(
                "This expense matches owner-confirmed document evidence. "
                "Cancel it and start a replacement transcription to correct it."
            )
        )


def validate_document_expense_posting(
    *,
    expense: OperatingExpense,
    method: str,
    telebirr_reference: str,
) -> None:
    transcription = DocumentTranscription.objects.filter(
        business=expense.business,
        target_expense=expense,
        status=TranscriptionStatus.CONFIRMED,
    ).first()
    if transcription is None:
        return
    if transcription.expense_payment_method != method:
        raise ValidationError(
            _("The payment method must match the owner-confirmed document transcription.")
        )
    if transcription.expense_telebirr_reference.strip() != telebirr_reference.strip():
        raise ValidationError(
            _("The Telebirr reference must match the owner-confirmed document transcription.")
        )
