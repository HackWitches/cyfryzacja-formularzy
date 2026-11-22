"""PDF page -> image. pdfplumber renders through pdfium, which is not thread-safe: two requests rendering at once
(two scans, or page images for the model) crash the whole process. One lock serializes rendering only."""
import threading

_PDFIUM = threading.Lock()  # ponytail: one lock per process; a render takes well under a second per page


def page_image(page, resolution: int):
    """A PIL image of a pdfplumber page."""
    with _PDFIUM:
        return page.to_image(resolution=resolution).original.copy()
