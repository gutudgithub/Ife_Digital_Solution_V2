from django.core.management.base import BaseCommand

from apps.documents.services import purge_eligible_document_files


class Command(BaseCommand):
    help = "Purge eligible cancelled private-document bytes while retaining metadata."

    def handle(self, *args: object, **options: object) -> None:
        del args, options
        count = purge_eligible_document_files()
        self.stdout.write(self.style.SUCCESS(f"Purged {count} document file(s)."))
