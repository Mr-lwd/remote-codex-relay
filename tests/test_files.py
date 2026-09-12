"""Document authorization, round trip, return-path containment and native inputs."""

import io
import zipfile
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from server import app as adapter
from server.media import MediaStore, MAX_BYTES
from test_media import png


def archive(member):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr(member, "<document/>")
    return out.getvalue()


SAMPLES = {
    "report.pdf": b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF",
    "report.docx": archive("word/document.xml"),
    "slides.pptx": archive("ppt/presentation.xml"),
    "data.xlsx": archive("xl/workbook.xml"),
    "legacy.doc": bytes.fromhex("d0cf11e0a1b11ae1") + b"legacy office",
    "legacy.ppt": bytes.fromhex("d0cf11e0a1b11ae1") + b"legacy office",
    "legacy.xls": bytes.fromhex("d0cf11e0a1b11ae1") + b"legacy office",
    "notes.txt": "中文附件内容".encode(),
    "notes.md": b"# Title",
    "table.csv": b"name,value\na,1",
    "table.tsv": b"name\tvalue\na\t1",
    "data.json": b'{"ok":true}',
    "notes.rtf": b"{\\rtf1 sample}",
    "archive.zip": archive("test.txt"),
    "report.odt": archive("content.xml"),
    "sheet.ods": archive("content.xml"),
    "slides.odp": archive("content.xml"),
}


@pytest.mark.parametrize("name,raw", SAMPLES.items())
def test_document_roundtrip(client, store, name, raw):
    response = client.post("/api/media", params={"name": name, "thread": "t"}, content=raw)
    assert response.status_code == 200, response.text
    asset = response.json()
    assert asset["kind"] == "file" and "thumbnailUrl" not in asset
    assert asset["width"] is None and asset["name"] == name
    for url in [asset["url"], asset["downloadUrl"]]:
        download = client.get(url)
        assert download.content == raw
        assert download.headers["content-disposition"].startswith("attachment;")
        assert download.headers["x-content-type-options"] == "nosniff"
    assert client.get(asset["url"] + "?thumbnail=1").status_code == 404
    assert TestClient(adapter.app).get(asset["url"]).status_code == 401
    assert store.path(store.get(asset["id"])).stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "name,raw",
    [
        ("bad.pdf", b"not a PDF"),
        ("bad.pptx", archive("word/document.xml")),
        ("bad.doc", b"fake"),
        ("bad.xlsx", b"PK broken zip"),
        ("bad.exe", b"MZ executable"),
        ("bad.svg", b"<svg/>"),
        ("bad.html", b"<script>alert(1)</script>"),
        ("bad.txt", b"\0binary"),
    ],
)
def test_document_type_checks(client, name, raw):
    assert client.post("/api/media", params={"name": name}, content=raw).status_code == 400


def test_oversized_archive_is_not_extracted(client):
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("large.txt", b"x" * (251 * 1024 * 1024))
    assert client.post("/api/media?name=large.zip", content=raw.getvalue()).status_code == 400


def test_file_and_image_limits_and_empty_text(client):
    assert (
        client.post("/api/media?name=large.txt", content=b"x" * (MAX_BYTES + 1)).status_code == 413
    )
    assert (
        client.post("/api/media?name=large.png", content=b"x" * (21 * 1024 * 1024)).status_code
        == 413
    )
    assert client.post("/api/media?name=empty.txt", content=b"").status_code == 200


def test_mixed_native_inputs_and_reference_ownership(store, tmp_path):
    image = store.save(png(), tid="t")
    document = store.save(SAMPLES["report.pdf"], "报告.pdf", tid="t")
    inputs = store.inputs([document["id"], image["id"]])
    assert (
        inputs[0]["type"] == "text"
        and str(store.path(store.get(document["id"]))) in inputs[0]["text"]
    )
    assert "报告.pdf" in inputs[0]["text"]
    assert inputs[1]["type"] == "localImage"
    assert store.default_prompt([document["id"], image["id"]]) == "请查看并分析这些附件。"
    for reference in [
        document["url"],
        document["downloadUrl"],
        str(store.path(store.get(document["id"]))),
    ]:
        assert (
            store.import_reference(reference, {"id": "t", "cwd": str(tmp_path)})["id"]
            == document["id"]
        )
        assert store.import_reference(reference, {"id": "other", "cwd": str(tmp_path)}) is None
    with pytest.raises(HTTPException):
        store.validate([document["id"]], "other")
    with pytest.raises(HTTPException):
        store.validate([document["id"]] * 7, "t")


def test_returned_files_persist_and_private_paths_are_not_imported(store, tmp_path):
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    row = {"id": "t", "cwd": str(cwd)}
    path = cwd / "报价 单.pdf"
    path.write_bytes(SAMPLES["report.pdf"])
    message = {"text": f"[下载 PDF](<{path}>)"}
    decorated = store.decorate(message, row)
    asset = decorated["files"][0]
    assert not decorated["images"] and asset["name"] == "报价 单.pdf"
    assert asset["url"] in decorated["text"]
    path.unlink()
    reopened = MediaStore(tmp_path / "data")
    assert reopened.decorate(message, row)["files"][0]["id"] == asset["id"]
    assert reopened.path(reopened.get(asset["id"])).read_bytes() == SAMPLES["report.pdf"]
    outside = tmp_path / "private.txt"
    outside.write_text("private")
    (cwd / "escape.txt").symlink_to(outside)
    (cwd / ".private").mkdir()
    (cwd / ".private" / "report.txt").write_text("private")
    (cwd / "access.txt").write_text("private")
    for reference in [
        str(outside),
        "../private.txt",
        "escape.txt",
        ".private/report.txt",
        "access.txt",
    ]:
        assert reopened.import_reference(reference, row) is None


def test_document_only_submission_and_attachment_notes(client, store, monkeypatch):
    calls = []
    monkeypatch.setattr(adapter, "launch", lambda *a, **kw: calls.append((a, kw)) or "j")
    monkeypatch.setattr(adapter, "note", lambda *a: None)
    monkeypatch.setattr(adapter, "snapshot", lambda *a: {"status": "idle", "managed": False})
    monkeypatch.setattr(adapter, "writer_alive", lambda *a: False)
    asset = store.save(SAMPLES["report.pdf"], "report.pdf", tid="t")
    response = client.post("/api/threads/t/messages", json={"attachments": [asset["id"]]})
    assert response.status_code == 200, response.text
    assert calls[0][0][0] == "请查看并分析这些附件。"
    assert calls[0][0][-1]["attachments"] == [asset["id"]]
    links = adapter.attachment_links([asset["id"]])
    assert "[report.pdf]" in links and "![" not in links


def test_explicit_file_blocks_become_cards(store, tmp_path):
    from server.media import image_references

    path = tmp_path / "report.txt"
    path.write_text("returned")
    refs = image_references({"content": [{"type": "file", "path": str(path)}]})
    message = store.decorate({"text": "", "imageRefs": refs}, {"id": "t", "cwd": str(tmp_path)})
    assert message["files"][0]["name"] == "report.txt" and not message["images"]


def test_returned_file_keeps_deliverable_name_even_if_bytes_match_upload(store, tmp_path):
    original = store.save(b"contents", "input.txt", tid="t")
    path = tmp_path / "deliverable.txt"
    path.write_bytes(b"contents")
    result = store.import_reference(str(path), {"id": "t", "cwd": str(tmp_path)})
    assert result["name"] == "deliverable.txt" and result["id"] != original["id"]
