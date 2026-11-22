"""DOC/DOCX/ODS/XLSX -> PDF with LibreOffice headless (MPL-2.0, called as a separate program)."""
import subprocess
from pathlib import Path


class ConversionError(RuntimeError):
    pass


def to_pdf(src: Path, soffice: str, timeout_s: int) -> Path:
    out_dir = src.parent
    try:
        # A fresh profile per request: works on a read-only home and with parallel conversions.
        profile = (out_dir / "lo-profile").as_uri()
        subprocess.run([soffice, "--headless", "--norestore", f"-env:UserInstallation={profile}",
                        "--convert-to", "pdf", "--outdir", str(out_dir), str(src)],
                       check=True, capture_output=True, timeout=timeout_s)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        raise ConversionError(f"Konwersja do PDF nie powiodła się: {e}") from e
    pdf = out_dir / (src.stem + ".pdf")
    if not pdf.exists():
        raise ConversionError("LibreOffice nie utworzył pliku PDF.")
    return pdf
