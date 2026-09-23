import socket
import struct
from dataclasses import dataclass
from enum import StrEnum
from typing import BinaryIO, Protocol, cast

from django.conf import settings
from django.utils.module_loading import import_string


class ScanVerdict(StrEnum):
    CLEAN = "clean"
    INFECTED = "infected"
    ERROR = "error"


@dataclass(frozen=True)
class ScanResult:
    verdict: ScanVerdict
    engine: str
    signature: str = ""
    detail: str = ""


class DocumentScanner(Protocol):
    def scan(self, source: BinaryIO) -> ScanResult: ...


class DevelopmentDocumentScanner:
    engine_name = "development-deterministic-scanner"

    def scan(self, source: BinaryIO) -> ScanResult:
        source.seek(0)
        content = source.read()
        source.seek(0)
        if b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE" in content:
            return ScanResult(
                verdict=ScanVerdict.INFECTED,
                engine=self.engine_name,
                signature="EICAR-Test-Signature",
            )
        return ScanResult(verdict=ScanVerdict.CLEAN, engine=self.engine_name)


class ClamAVDocumentScanner:
    engine_name = "clamav-instream"

    def scan(self, source: BinaryIO) -> ScanResult:
        try:
            with socket.create_connection(
                (
                    settings.DOCUMENT_SCANNER_HOST,
                    settings.DOCUMENT_SCANNER_PORT,
                ),
                timeout=settings.DOCUMENT_SCANNER_TIMEOUT_SECONDS,
            ) as connection:
                connection.settimeout(settings.DOCUMENT_SCANNER_TIMEOUT_SECONDS)
                connection.sendall(b"zINSTREAM\0")
                source.seek(0)
                while chunk := source.read(64 * 1024):
                    connection.sendall(struct.pack("!I", len(chunk)))
                    connection.sendall(chunk)
                connection.sendall(struct.pack("!I", 0))
                response = connection.recv(4096).decode("utf-8", errors="replace")
                source.seek(0)
        except (OSError, TimeoutError) as error:
            return ScanResult(
                verdict=ScanVerdict.ERROR,
                engine=self.engine_name,
                detail=error.__class__.__name__,
            )
        if response.endswith("OK\0"):
            return ScanResult(verdict=ScanVerdict.CLEAN, engine=self.engine_name)
        if "FOUND" in response:
            signature = response.rsplit(" FOUND", maxsplit=1)[0].split(": ", maxsplit=1)[-1]
            return ScanResult(
                verdict=ScanVerdict.INFECTED,
                engine=self.engine_name,
                signature=signature[:180],
            )
        return ScanResult(
            verdict=ScanVerdict.ERROR,
            engine=self.engine_name,
            detail="Unexpected scanner response.",
        )


def configured_scanner() -> DocumentScanner:
    scanner_class = cast(type[DocumentScanner], import_string(settings.DOCUMENT_SCANNER_BACKEND))
    return scanner_class()
