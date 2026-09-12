"""Common document formats. Never execute or extract uploaded containers."""

import io
import zipfile
from pathlib import Path
from fastapi import HTTPException

DOCUMENT_TYPES = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".odp": "application/vnd.oasis.opendocument.presentation",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".json": "application/json",
    ".rtf": "application/rtf",
    ".zip": "application/zip",
}
TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".tsv", ".json"}
OFFICE_MEMBERS = {
    ".docx": "word/document.xml",
    ".pptx": "ppt/presentation.xml",
    ".xlsx": "xl/workbook.xml",
    ".odt": "content.xml",
    ".ods": "content.xml",
    ".odp": "content.xml",
}


def validate_document(data, name):
    ext = Path(name).suffix.lower()
    if ext not in DOCUMENT_TYPES:
        raise HTTPException(
            400, "不支持此文件类型，请选择图片、PDF、Word、PPT、Excel、文本或 ZIP 文件"
        )
    try:
        if ext == ".pdf":
            if not data[:1024].lstrip().startswith(b"%PDF-"):
                raise ValueError()
        elif ext in (".doc", ".ppt", ".xls"):
            if not data.startswith(bytes.fromhex("d0cf11e0a1b11ae1")):
                raise ValueError()
        elif ext == ".rtf":
            if not data.lstrip().startswith(b"{\\rtf"):
                raise ValueError()
        elif ext in OFFICE_MEMBERS or ext == ".zip":
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                entries = archive.infolist()
                if (
                    len(entries) > 10000
                    or sum(entry.file_size for entry in entries) > 250 * 1024 * 1024
                ):
                    raise ValueError()
                if ext in OFFICE_MEMBERS and OFFICE_MEMBERS[ext] not in archive.namelist():
                    raise ValueError()
        elif ext in TEXT_EXTENSIONS:
            # Accept UTF-8, UTF-16 and common legacy text encodings, but reject binary files.
            if b"\0" in data and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
                raise ValueError()
    except (ValueError, zipfile.BadZipFile, OSError) as e:
        raise HTTPException(400, "文件内容与扩展名不符，或文件容器过大，请检查后重试") from e
    return DOCUMENT_TYPES[ext], ext
