"""DOC/DOCX/ODS/XLSX -> PDF with LibreOffice headless (MPL-2.0, called as a separate program)."""

import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


class ConversionError(RuntimeError):
    pass


def to_pdf(src: Path, soffice: str, timeout_s: int) -> Path:
    out_dir = src.parent
    try:
        # A fresh profile per request: works on a read-only home and with parallel conversions.
        profile = (out_dir / "lo-profile").as_uri()
        subprocess.run(
            [
                soffice,
                "--headless",
                "--norestore",
                f"-env:UserInstallation={profile}",
                "--convert-to",
                "pdf",
                "--outdir",
                str(out_dir),
                str(src),
            ],
            check=True,
            capture_output=True,
            timeout=timeout_s,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        log.warning("LibreOffice conversion failed: %r", e)  # command line and temp paths go to the log, not the client
        raise ConversionError("Konwersja dokumentu do PDF nie powiodła się.") from e
    pdf = out_dir / (src.stem + ".pdf")
    if not pdf.exists():
        raise ConversionError("LibreOffice nie utworzył pliku PDF.")
    return pdf
