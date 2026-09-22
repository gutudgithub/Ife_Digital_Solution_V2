from django.core.checks import Tags, run_checks
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Fail unless all automated and human-attested Stage 12 pilot gates pass."

    def handle(self, *args: object, **options: object) -> None:
        del args, options
        messages = run_checks(tags=[Tags.security], include_deployment_checks=True)
        failures = [message for message in messages if message.is_serious()]
        for message in messages:
            self.stdout.write(str(message))
        if failures:
            raise CommandError(f"Stage 12 readiness failed with {len(failures)} blocking check(s).")
        self.stdout.write(self.style.SUCCESS("Stage 12 readiness checks passed."))
