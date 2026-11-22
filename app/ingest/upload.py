"""Upload checks required by COI: format, file name, size, and content matching the extension.

Content is recognised by magic bytes, and Office/OpenDocument zips by the parts inside.
"""

import io
import zipfile

FORBIDDEN = set(";:></\\|'*%$")
MAX_NAME = 250
KINDS = {  # extension -> kind
    "pdf": "pdf",
    "png": "image",
    "jpg": "image",
    "jpeg": "image",
    "doc": "office",
    "docx": "office",
    "xlsx": "office",
    "ods": "office",
}


class InputError(ValueError):
    """Rejected upload; `status` is the HTTP code the API answers with."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def check_name(name: str) -> str:
    if not name or len(name) > MAX_NAME:
        raise InputError(400, f"Nazwa pliku musi mieć od 1 do {MAX_NAME} znaków.")
    if FORBIDDEN & set(name):
        raise InputError(400, "Nazwa pliku zawiera niedozwolone znaki: ; : > < / \\ | ' * % $")
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in KINDS:
        raise InputError(415, f"Nieobsługiwany format .{ext}; dozwolone: {', '.join(sorted(KINDS))}.")
    return ext


def _zip_parts(data: bytes) -> set[str]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = set(z.namelist())
            if "mimetype" in names:
                with z.open("mimetype") as part:  # a few bytes are enough; never inflate the whole entry
                    names.add("mimetype:" + part.read(128).decode("ascii", "ignore").strip())
            return names
    except zipfile.BadZipFile, OSError, NotImplementedError:  # broken, encrypted or exotic compression
        return set()


def content_matches(ext: str, data: bytes) -> bool:
    head = data[:8]
    if ext == "pdf":
        return data[:1024].lstrip().startswith(b"%PDF")
    if ext == "png":
        return head == b"\x89PNG\r\n\x1a\n"
    if ext in ("jpg", "jpeg"):
        return head[:3] == b"\xff\xd8\xff"
    ole2 = head == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # OLE2 compound file (Word 97-2003)
    parts = _zip_parts(data) if head[:4] == b"PK\x03\x04" else set()
    if ext in ("doc", "docx"):
        # COI lists one format "DOC (.doc .docx)", and its sample forms include Word 2007 files named .DOC.
        # So either Word format passes under either extension; nothing else does.
        return ole2 or "word/document.xml" in parts
    if ext == "xlsx":
        return "xl/workbook.xml" in parts
    if ext == "ods":
        return "mimetype:application/vnd.oasis.opendocument.spreadsheet" in parts
    return False


def validate(name: str, data: bytes, max_bytes: int) -> str:
    """Return the file kind (pdf | image | office) or raise InputError."""
    ext = check_name(name)
    if not data:
        raise InputError(400, "Pusty plik.")
    if len(data) > max_bytes:
        raise InputError(413, f"Plik większy niż {max_bytes // (1024 * 1024)} MB.")
    if not content_matches(ext, data):
        raise InputError(415, f"Zawartość pliku nie odpowiada rozszerzeniu .{ext}.")
    return KINDS[ext]
