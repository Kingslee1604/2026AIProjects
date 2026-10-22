"""Upload validation by content, not by name (security baseline S5).

The declared extension must agree with what the bytes actually are; zip containers (DOCX)
are checked for decompression bombs before anything opens them.
"""

import zipfile
from dataclasses import dataclass
from pathlib import Path

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MARKDOWN = "text/markdown"
TEXT = "text/plain"

EXTENSIONS = {".pdf": PDF, ".docx": DOCX, ".md": MARKDOWN, ".markdown": MARKDOWN, ".txt": TEXT}

MAX_ZIP_ENTRIES = 2_000
MAX_ZIP_UNCOMPRESSED = 200 * 1024 * 1024
MAX_ZIP_RATIO = 100  # uncompressed / compressed, per entry


class UploadRejectedError(ValueError):
    """The file is not acceptable; the message is safe to show to the user."""


@dataclass(frozen=True, slots=True)
class ValidatedUpload:
    filename: str
    media_type: str


def safe_filename(name: str) -> str:
    base = Path(name.replace("\\", "/")).name.strip()
    cleaned = "".join(ch for ch in base if ch.isprintable() and ch not in '<>:"|?*')
    if not cleaned or cleaned in {".", ".."}:
        raise UploadRejectedError("The file needs a name.")
    return cleaned[:255]


def check_zip_container(path: Path, *, required: set[str], kind: str) -> set[str]:
    """Reject decompression bombs and files missing the parts their format requires, before
    any parser opens them (Office files are zip archives). Returns the part names."""
    invalid = f"The file is not a valid {kind}."
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ZIP_ENTRIES:
                raise UploadRejectedError(f"The {kind} has too many internal parts.")
            total = 0
            for entry in entries:
                total += entry.file_size
                compressed = max(entry.compress_size, 1)
                if entry.file_size / compressed > MAX_ZIP_RATIO and entry.file_size > 1_000_000:
                    raise UploadRejectedError(f"The {kind} looks like a decompression bomb.")
            if total > MAX_ZIP_UNCOMPRESSED:
                raise UploadRejectedError(f"The {kind} expands to more than 200 MB.")
            names = {entry.filename for entry in entries}
            if not required <= names:
                raise UploadRejectedError(invalid)
            return names
    except zipfile.BadZipFile as error:
        raise UploadRejectedError(invalid) from error


def _check_docx(path: Path) -> None:
    check_zip_container(
        path, required={"word/document.xml", "[Content_Types].xml"}, kind="Word (.docx) document"
    )


def _check_text(path: Path) -> None:
    data = path.read_bytes()
    if b"\x00" in data:
        raise UploadRejectedError("The text file contains binary data.")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise UploadRejectedError("Text files must be UTF-8 encoded.") from error


def validate_upload(path: Path, filename: str, size: int) -> ValidatedUpload:
    name = safe_filename(filename)
    if size == 0:
        raise UploadRejectedError("The file is empty.")
    extension = Path(name).suffix.lower()
    media_type = EXTENSIONS.get(extension)
    if media_type is None:
        allowed = ", ".join(sorted(EXTENSIONS))
        raise UploadRejectedError(f"Unsupported file type. Allowed: {allowed}.")
    with path.open("rb") as handle:
        head = handle.read(8)
    if media_type == PDF:
        if not head.startswith(b"%PDF-"):
            raise UploadRejectedError("The file has a .pdf name but is not a PDF.")
    elif media_type == DOCX:
        if not head.startswith(b"PK\x03\x04"):
            raise UploadRejectedError("The file has a .docx name but is not a Word document.")
        _check_docx(path)
    else:
        _check_text(path)
    return ValidatedUpload(name, media_type)
