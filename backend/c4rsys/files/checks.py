"""The check of an uploaded file before anyone may use it (ТЗ § 24.4.4).

* The actual type is told by the content (the signature, the inner parts of a package), not by
  the name or the browser's claim, and must be one of the formats allowed for the purpose
  (§ 10.4.2, § 18.1.3; `services.*_type`).
* The content is then inspected for what makes an allowed format dangerous. PROPOSAL (the ТЗ
  names no list): macros, embedded objects, encrypted parts and «zip bombs» in DOCX/XLSX;
  scripts, launch actions and embedded files in PDF.
* The check is synchronous and fast. TODO(spec): an antivirus engine is not part of the MVP
  architecture yet; when it comes, it plugs in here and a file stays «checking» until it answers.

A blocked file is not attached to anything; the attempt is recorded with its reason, without the
content (`services.record_blocked`).
"""

import io
import re
import zipfile

from c4rsys.files import services

# PROPOSAL: technical limits of an Office package unpacked for the check.
MAX_UNPACKED_BYTES = 50 * 1024 * 1024
MAX_RATIO = 100
MAX_ENTRIES = 2000

OFFICE_DANGEROUS = (
    re.compile(r"(^|/)vbaProject\.bin$", re.IGNORECASE),  # macros
    re.compile(r"(^|/)vbaData\.xml$", re.IGNORECASE),
    re.compile(r"(^|/)activeX/", re.IGNORECASE),  # ActiveX controls
    re.compile(r"(^|/)embeddings/.*\.(bin|exe|dll|bat|cmd|com|js|vbs|ps1|scr)$", re.IGNORECASE),
)
# Name tokens of active content inside a PDF (§ 24.4.4: never run in the interface).
PDF_DANGEROUS = (b"/JavaScript", b"/JS", b"/Launch", b"/EmbeddedFile", b"/RichMedia", b"/XFA")


def _office(content: bytes) -> str:
    try:
        package = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        return "package_invalid"
    with package:
        entries = package.infolist()
        if len(entries) > MAX_ENTRIES:
            return "package_too_complex"
        total = 0
        for entry in entries:
            if entry.flag_bits & 0x1:
                return "encrypted"
            total += entry.file_size
            if any(p.search(entry.filename) for p in OFFICE_DANGEROUS):
                return "active_content"
        if total > MAX_UNPACKED_BYTES or total > MAX_RATIO * max(len(content), 1):
            return "package_too_large"
        names = {entry.filename for entry in entries}
        types = package.read("[Content_Types].xml") if "[Content_Types].xml" in names else b""
        if b"macroEnabled" in types or b"vbaProject" in types:
            return "active_content"
    return ""


def _pdf(content: bytes) -> str:
    for token in PDF_DANGEROUS:
        # A name ends at a delimiter; «/JS» must not match «/JSON».
        if re.search(re.escape(token) + rb"(?![A-Za-z0-9])", content):
            return "active_content"
    if re.search(rb"/Encrypt(?![A-Za-z0-9])", content):
        return "encrypted"
    return ""


def inspect(content_type: str, content: bytes) -> str:
    """The reason to block the file, or an empty string when it passes."""
    if content_type in (services.DOCX, services.XLSX):
        return _office(content)
    if content_type == "application/pdf":
        return _pdf(content)
    return ""
