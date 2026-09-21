from django.test import SimpleTestCase, override_settings

from apps.documents.checks import check_document_production_configuration


class DocumentDeploymentCheckTests(SimpleTestCase):
    @override_settings(
        STORAGES={
            "documents": {
                "BACKEND": "storages.backends.s3.S3Storage",
                "OPTIONS": {"bucket_name": ""},
            }
        },
        DOCUMENT_SCANNER_BACKEND="apps.documents.scanning.ClamAVDocumentScanner",
        DOCUMENT_CONFIRMED_RETENTION_POLICY_APPROVED=True,
    )
    def test_s3_storage_requires_private_bucket_name(self) -> None:
        messages = check_document_production_configuration(None)

        self.assertEqual([message.id for message in messages], ["documents.E002"])

    @override_settings(
        STORAGES={
            "documents": {
                "BACKEND": "storages.backends.s3.S3Storage",
                "OPTIONS": {"bucket_name": "private-documents"},
            }
        },
        DOCUMENT_SCANNER_BACKEND="apps.documents.scanning.ClamAVDocumentScanner",
        DOCUMENT_CONFIRMED_RETENTION_POLICY_APPROVED=True,
    )
    def test_complete_production_configuration_passes_document_checks(self) -> None:
        self.assertEqual(check_document_production_configuration(None), [])
