"""PDF page -> image, serialized by one lock.

pdfplumber renders through pdfium, which is not thread-safe: two concurrent renders crash the whole process.
"""

import threading

_PDFIUM = threading.Lock()  # ponytail: one lock per process; a render takes well under a second per page


def page_image(page, resolution: int):
    """A PIL image of a pdfplumber page."""
    with _PDFIUM:
        return page.to_image(resolution=resolution).original.copy()
