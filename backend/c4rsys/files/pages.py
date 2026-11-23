"""Several photos kept as one document (customer's decision of 23.11.2026): a paper of a vehicle
has two sides, a document of a supplier and a receipt may take several pages. The photos — JPEG,
as the interface sends them — become the pages of one PDF, so whatever reads, keeps or shows the
document deals with one file as before.

A photo is put into the PDF as it is: nothing is decoded here, only the size and the colours are
taken from its header."""

from c4rsys.platform import errors

# The largest number of photos in one document by the purpose of the file (customer's decision of
# 23.11.2026): a paper of a vehicle — 5, a document of a supplier — 10, a receipt of an expense — 3.
LIMITS: dict[str, int] = {"vehicle_paper": 5, "warehouse_document": 10, "expense_receipt": 3}
# The markers that open the frame of a JPEG: the size of the picture follows them.
FRAMES = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
COLOURS = {1: "/DeviceGray", 3: "/DeviceRGB"}
# A page is drawn at 150 points per inch of its dots.
SCALE = 72 / 150


def _not_a_photo() -> errors.ApiError:
    return errors.validation_error("file", code="PAGES_NOT_PHOTOS")


def _frame(photo: bytes) -> tuple[int, int, str]:
    """The width, the height and the colour space of a JPEG, by its header."""
    if not photo.startswith(b"\xff\xd8"):
        raise _not_a_photo()
    at = 2
    while at + 9 < len(photo):
        if photo[at] != 0xFF:
            raise _not_a_photo()
        marker = photo[at + 1]
        if marker == 0xFF:
            at += 1
            continue
        if marker in (0x01, *range(0xD0, 0xD8)):
            at += 2
            continue
        length = int.from_bytes(photo[at + 2 : at + 4], "big")
        if marker in FRAMES:
            height = int.from_bytes(photo[at + 5 : at + 7], "big")
            width = int.from_bytes(photo[at + 7 : at + 9], "big")
            colours = COLOURS.get(photo[at + 9])
            if photo[at + 4] != 8 or not width or not height or colours is None:
                raise _not_a_photo()
            return width, height, colours
        if length < 2:
            raise _not_a_photo()
        at += 2 + length
    raise _not_a_photo()


def pdf(photos: list[bytes]) -> bytes:
    """One PDF whose pages are the photos, in their order."""
    objects: list[bytes] = [b"", b""]  # the catalog and the list of pages are written last
    kids = []
    for photo in photos:
        width, height, colours = _frame(photo)
        w, h = round(width * SCALE, 2), round(height * SCALE, 2)
        number = len(objects) + 1
        kids.append(f"{number} 0 R")
        draw = f"q {w} 0 0 {h} 0 0 cm /P Do Q".encode()
        objects += [
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {w} {h}] "
                f"/Resources << /XObject << /P {number + 1} 0 R >> >> "
                f"/Contents {number + 2} 0 R >>"
            ).encode(),
            (
                f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
                f"/ColorSpace {colours} /BitsPerComponent 8 /Filter /DCTDecode "
                f"/Length {len(photo)} >>\nstream\n"
            ).encode()
            + photo
            + b"\nendstream",
            f"<< /Length {len(draw)} >>\nstream\n".encode() + draw + b"\nendstream",
        ]
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>".encode()
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    start = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n"
    ).encode()
    return bytes(out)
