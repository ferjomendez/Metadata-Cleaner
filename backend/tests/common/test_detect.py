"""Content-based type detection and zip guard (stdlib only)."""

import io
import zipfile

import pytest

from app.detect import detect_file, sanitize_display_name
from app.zipguard import ZipLimits, ZipRejected, check_zip

LIMITS = ZipLimits(max_entries=100, max_uncompressed=10 * 1024 * 1024, max_ratio=100)


def _zip(path, members: dict, compression=zipfile.ZIP_DEFLATED):
    with zipfile.ZipFile(path, "w", compression) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


CT_DOCX = (b'<Types><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
           b'officedocument.wordprocessingml.document.main+xml"/></Types>')


@pytest.mark.parametrize("head,expected", [
    (b"\xff\xd8\xff\xe0" + b"\0" * 20, "jpeg"),
    (b"\x89PNG\r\n\x1a\n" + b"\0" * 20, "png"),
    (b"GIF89a" + b"\0" * 20, "gif"),
    (b"II*\x00" + b"\0" * 20, "tiff"),
    (b"MM\x00*" + b"\0" * 20, "tiff"),
    (b"RIFF\0\0\0\0WEBPVP8 " + b"\0" * 20, "webp"),
    (b"RIFF\0\0\0\0WAVEfmt " + b"\0" * 20, "wav"),
    (b"RIFF\0\0\0\0AVI LIST" + b"\0" * 20, "avi"),
    (b"%PDF-1.7\n" + b"\0" * 20, "pdf"),
    (b"fLaC" + b"\0" * 20, "flac"),
    (b"OggS" + b"\0" * 24 + b"\x01vorbis" + b"\0" * 10, "ogg"),
    (b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00", "mp3"),
    (b"\xff\xfb\x90\x00" + b"\0" * 20, "mp3"),
    (b"\xff\xf1\x50\x80" + b"\0" * 20, "aac"),
    (b"\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00M4A isom", "m4a"),
    (b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isomavc1", "mp4"),
    (b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00qt  ", "mov"),
    (b"\x00\x00\x00\x18ftypmif1\x00\x00\x00\x00mif1heic", "heic"),
    (b"\x1a\x45\xdf\xa3\x01\x00\x00\x00\x00\x00\x00\x1fB\x82\x84webm", "webm"),
    (b"\x1a\x45\xdf\xa3\x01\x00\x00\x00\x00\x00\x00\x1fB\x82\x88matroska", "mkv"),
])
def test_magic_bytes(tmp_path, head, expected):
    p = tmp_path / "f.bin"
    p.write_bytes(head)
    assert detect_file(p, LIMITS).format_id == expected


def test_extension_is_ignored(tmp_path):
    p = tmp_path / "photo.jpg"
    p.write_bytes(b"%PDF-1.4\n%%EOF")
    assert detect_file(p, LIMITS).format_id == "pdf"


@pytest.mark.parametrize("data,code", [
    (b"", "empty"),
    (b"hello world, plain text", "unsupported"),
    (b"<html><script>alert(1)</script>", "unsupported"),
    (b"MZ\x90\x00" + b"\0" * 60, "unsupported"),
    (b"II+\x00" + b"\0" * 20, "unsupported"),
])
def test_rejections(tmp_path, data, code):
    p = tmp_path / "x"
    p.write_bytes(data)
    d = detect_file(p, LIMITS)
    assert not d.ok and d.reject_code == code


def test_encrypted_pdf(tmp_path):
    p = tmp_path / "e.pdf"
    p.write_bytes(b"%PDF-1.6\n1 0 obj<<>>endobj\ntrailer<</Encrypt 5 0 R/Root 1 0 R>>\n%%EOF")
    assert detect_file(p, LIMITS).reject_code == "encrypted"


def test_encrypted_ooxml_ole_container(tmp_path):
    p = tmp_path / "secret.docx"
    p.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 600 + "EncryptedPackage".encode("utf-16-le"))
    assert detect_file(p, LIMITS).reject_code == "encrypted"


def test_legacy_office(tmp_path):
    p = tmp_path / "old.doc"
    p.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 600 + "WordDocument".encode("utf-16-le"))
    assert detect_file(p, LIMITS).reject_code == "legacy_office"


def test_ooxml_and_odf(tmp_path):
    docx = _zip(tmp_path / "a.zip", {"[Content_Types].xml": CT_DOCX, "word/document.xml": b"<w/>"})
    assert detect_file(docx, LIMITS).format_id == "docx"
    odt = tmp_path / "b.odt"
    with zipfile.ZipFile(odt, "w") as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), b"application/vnd.oasis.opendocument.text")
        zf.writestr("content.xml", b"<x/>")
        zf.writestr("META-INF/manifest.xml", b"<manifest/>")
    assert detect_file(odt, LIMITS).format_id == "odt"


def test_encrypted_odf(tmp_path):
    odt = tmp_path / "b.odt"
    with zipfile.ZipFile(odt, "w") as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), b"application/vnd.oasis.opendocument.text")
        zf.writestr("META-INF/manifest.xml", b"<manifest:encryption-data/>")
    assert detect_file(odt, LIMITS).reject_code == "encrypted"


def test_macro_office_rejected(tmp_path):
    p = _zip(tmp_path / "m.docm", {"[Content_Types].xml": b"application/vnd.ms-word.document.macroEnabled.main+xml"})
    assert detect_file(p, LIMITS).reject_code == "macro_office"


def test_plain_zip_unsupported(tmp_path):
    p = _zip(tmp_path / "x.zip", {"a.txt": b"hi"})
    assert detect_file(p, LIMITS).reject_code == "unsupported"


def test_corrupt_zip(tmp_path):
    p = tmp_path / "c.docx"
    p.write_bytes(b"PK\x03\x04garbage")
    assert detect_file(p, LIMITS).reject_code == "corrupt"


def test_zip_bomb_ratio(tmp_path):
    p = _zip(tmp_path / "bomb.docx", {"[Content_Types].xml": CT_DOCX, "big.xml": b"\0" * (5 * 1024 * 1024)})
    with pytest.raises(ZipRejected) as e:
        check_zip(p, LIMITS)
    assert e.value.code == "archive_bomb"
    assert detect_file(p, LIMITS).reject_code == "archive_bomb"


def test_zip_bomb_total_size(tmp_path):
    lim = ZipLimits(max_entries=100, max_uncompressed=1000, max_ratio=1000)
    p = _zip(tmp_path / "t.zip", {"a": b"x" * 600, "b": b"y" * 600}, zipfile.ZIP_STORED)
    with pytest.raises(ZipRejected):
        check_zip(p, lim)


def test_zip_too_many_entries(tmp_path):
    p = _zip(tmp_path / "n.zip", {f"f{i}": b"" for i in range(150)})
    with pytest.raises(ZipRejected) as e:
        check_zip(p, LIMITS)
    assert e.value.code == "archive_bomb"


@pytest.mark.parametrize("name", ["../evil.xml", "/abs.xml", "a/../../b.xml", "c:\\x.xml", "a\\b.xml"])
def test_zip_slip_names(tmp_path, name):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(zipfile.ZipInfo(name), b"x")
    p = tmp_path / "s.zip"
    p.write_bytes(buf.getvalue())
    with pytest.raises(ZipRejected) as e:
        check_zip(p, LIMITS)
    assert e.value.code == "archive_unsafe"


@pytest.mark.parametrize("raw,expected", [
    ("../../etc/passwd", "passwd"),
    ("C:\\Users\\usuario\\foto.jpg", "foto.jpg"),
    ("a\x00b\nc.pdf", "abc.pdf"),
    ("<script>.png", "_script_.png"),
    ("", "archivo"),
    ("...", "archivo"),
    ("informe ñandú (final).docx", "informe ñandú (final).docx"),
])
def test_sanitize_display_name(raw, expected):
    assert sanitize_display_name(raw) == expected


def test_sanitize_long_name_keeps_extension():
    n = sanitize_display_name("a" * 500 + ".pdf")
    assert n.endswith(".pdf") and len(n) <= 180
