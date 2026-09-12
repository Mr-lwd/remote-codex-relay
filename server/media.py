"""Validated image and document storage. Clients address opaque IDs, never server paths."""

import base64
import hashlib
import io
import re
import sqlite3
import time
import uuid
import warnings
from pathlib import Path
from urllib.parse import unquote, urlsplit

from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError
from server.files import DOCUMENT_TYPES, validate_document

MAX_BYTES = 50 * 1024 * 1024
IMAGE_MAX_BYTES = 20 * 1024 * 1024
MAX_IMAGES = 6  # Compatibility name: the limit covers images and documents together.
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
Image.MAX_IMAGE_PIXELS = 40_000_000
FORMATS = {
    "PNG": ("image/png", ".png"),
    "JPEG": ("image/jpeg", ".jpg"),
    "WEBP": ("image/webp", ".webp"),
    "GIF": ("image/gif", ".gif"),
}
LINK = re.compile(r'(!?\[[^\]\n]*\]\()(?P<url><[^>\n]+>|[^\s)]+)(?P<tail>\s+"[^"\n]*")?\)')


class MediaStore:
    def __init__(self, root):
        self.root = Path(root) / "media"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.import_cache = {}
        with self.db() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS images(id TEXT PRIMARY KEY, name TEXT, mime TEXT, ext TEXT, size INTEGER, width INTEGER, height INTEGER, digest TEXT, thread_id TEXT, source TEXT, created REAL)"
            )
            c.execute(
                "CREATE TABLE IF NOT EXISTS references_cache(thread_id TEXT, reference TEXT, image_id TEXT, PRIMARY KEY(thread_id,reference))"
            )
            c.execute("CREATE INDEX IF NOT EXISTS images_thread_digest ON images(thread_id,digest)")

    def db(self):
        c = sqlite3.connect(self.root / "index.sqlite", timeout=10)
        c.row_factory = sqlite3.Row
        return c

    def save(self, data, name="image.png", tid=None, source="upload"):
        if len(data) > MAX_BYTES:
            raise HTTPException(413, "每个文件不得超过 50 MB")
        ext = Path(name).suffix.lower()
        preview = None
        width = height = None
        if ext in DOCUMENT_TYPES:
            mime, ext = validate_document(data, name)
        else:
            if len(data) > IMAGE_MAX_BYTES:
                raise HTTPException(413, "每张图片不得超过 20 MB")
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("error", Image.DecompressionBombWarning)
                    with Image.open(io.BytesIO(data)) as im:
                        fmt, (width, height) = im.format, im.size
                        if fmt not in FORMATS:
                            raise ValueError("format")
                        im.verify()
                    with Image.open(io.BytesIO(data)) as im:
                        im.load()
                        thumb = ImageOps.exif_transpose(im).convert("RGB")
                        thumb.thumbnail((640, 640))
                        preview = io.BytesIO()
                        thumb.save(preview, "JPEG", quality=85)
            except (
                OSError,
                ValueError,
                UnidentifiedImageError,
                Image.DecompressionBombError,
                Image.DecompressionBombWarning,
            ) as e:
                raise HTTPException(
                    400, "请选择有效的 PNG、JPEG、WebP 或 GIF 图片（不超过 4000 万像素）"
                ) from e
            mime, ext = FORMATS[fmt]
        # Never use a client name as a path or HTTP header verbatim.
        name = re.sub(r"[\x00-\x1f\x7f/\\]", "_", Path(name).name)[:120] or "image" + ext
        if Path(name).suffix.lower() not in IMAGE_EXTENSIONS | DOCUMENT_TYPES.keys():
            name += ext
        digest = hashlib.sha256(data).hexdigest()
        if source != "upload":
            with self.db() as c:
                row = c.execute(
                    "SELECT * FROM images WHERE thread_id IS ? AND digest=? AND ext=? AND name=? LIMIT 1",
                    (tid, digest, ext, name),
                ).fetchone()
                if row:
                    return self.public(row)
        ident = uuid.uuid4().hex
        contents = [(ext, data)]
        if preview is not None:
            contents.append((".thumb.jpg", preview.getvalue()))
        for suffix, content in contents:
            target = self.root / (ident + suffix)
            with target.open("xb") as f:
                f.write(content)
            target.chmod(0o600)
        with self.db() as c:
            c.execute(
                "INSERT INTO images VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    ident,
                    name,
                    mime,
                    ext,
                    len(data),
                    width,
                    height,
                    digest,
                    tid,
                    source,
                    time.time(),
                ),
            )
        return self.public(self.get(ident))

    def get(self, ident):
        if not re.fullmatch("[0-9a-f]{32}", ident):
            raise HTTPException(404, "附件不存在")
        with self.db() as c:
            row = c.execute("SELECT * FROM images WHERE id=?", (ident,)).fetchone()
        if not row:
            raise HTTPException(404, "附件不存在")
        return row

    def public(self, row):
        image = row["mime"].startswith("image/")
        return {
            **{k: row[k] for k in ("id", "name", "mime", "size", "width", "height")},
            "kind": "image" if image else "file",
            "url": f"/api/media/{row['id']}",
            **({"thumbnailUrl": f"/api/media/{row['id']}?thumbnail=1"} if image else {}),
            "downloadUrl": f"/api/media/{row['id']}?download=1",
        }

    def path(self, row):
        return self.root / (row["id"] + row["ext"])

    def validate(self, ids, tid=None):
        if len(ids) > MAX_IMAGES or len(set(ids)) != len(ids):
            raise HTTPException(400, "每条消息最多附加 6 个不同文件")
        for ident in ids:
            row = self.get(ident)
            if row["source"] != "upload" or (
                row["thread_id"] is not None and row["thread_id"] != tid
            ):
                raise HTTPException(400, "附件不属于当前会话，请重新上传")
        return ids

    def bind(self, ids, tid):
        self.validate(ids, tid)
        with self.db() as c:
            for ident in ids:
                c.execute("UPDATE images SET thread_id=? WHERE id=?", (tid, ident))

    def inputs(self, ids):
        inputs = []
        for ident in ids:
            row = self.get(ident)
            path = str(self.path(row))
            if row["mime"].startswith("image/"):
                inputs.append({"type": "localImage", "path": path})
            else:
                # App-server has no generic binary input. Give the agent an explicit,
                # persisted local file reference it can read with its existing tools.
                name = row["name"].replace("[", "_").replace("]", "_")
                inputs.append(
                    {
                        "type": "text",
                        "text": f"用户上传的参考文件：[{name}](<{path}>)（{row['mime']}，{row['size']} 字节）。请按用户需求读取此文件。",
                        "text_elements": [],
                    }
                )
        return inputs

    def default_prompt(self, ids):
        return (
            "请分析这些图片。"
            if ids and all(self.get(i)["mime"].startswith("image/") for i in ids)
            else "请查看并分析这些附件。"
        )

    def import_reference(self, value, row):
        if not isinstance(value, str) or not value:
            return None
        reference_hash = hashlib.sha256(value.encode()).hexdigest()
        try:
            if re.fullmatch(r"/api/media/[0-9a-f]{32}(?:\?(?:download|thumbnail)=1)?", value):
                asset = self.get(value.rsplit("/", 1)[-1].split("?")[0])
                return self.public(asset) if asset["thread_id"] == row["id"] else None
            if value.startswith("data:image/"):
                key = (row["id"], hashlib.sha256(value.encode()).hexdigest())
                if key in self.import_cache:
                    return self.import_cache[key]
                if len(value) > IMAGE_MAX_BYTES * 4 // 3 + 256:
                    return None
                header, encoded = value.split(",", 1)
                if ";base64" not in header:
                    return None
                result = self.save(
                    base64.b64decode(encoded, validate=True), "image.png", row["id"], "returned"
                )
            else:
                value = unquote(value.removeprefix("sandbox:").removeprefix("file://"))
                if urlsplit(value).scheme or value.startswith("//"):
                    return None
                path = Path(value)
                if not path.is_absolute():
                    path = Path(row.get("cwd") or ".") / path
                path = path.resolve()
                cwd = Path(row.get("cwd") or "/nonexistent").resolve()
                # Restrict imports to explicit references inside this thread's workspace or our uploads.
                if not (path.is_relative_to(cwd) or path.is_relative_to(self.root.resolve())):
                    return None
                if path.is_relative_to(self.root.resolve()):
                    # Local references to uploads must obey the same thread ownership
                    # rules as /api/media links; don't re-import another thread's file.
                    try:
                        asset = self.get(path.stem)
                    except HTTPException:
                        return None
                    return (
                        self.public(asset)
                        if asset["thread_id"] == row["id"] and self.path(asset).resolve() == path
                        else None
                    )
                if path.suffix.lower() not in IMAGE_EXTENSIONS | DOCUMENT_TYPES.keys():
                    return None
                # Broad workspaces such as a home directory also contain private config.
                # Never turn credentials or hidden configuration directories into downloads.
                if (
                    any(part.startswith(".") for part in path.parts)
                    or path.name.lower()
                    in {"access.txt", "credentials.json", "auth.json", "secrets.json"}
                    or path.name.endswith(".env")
                ):
                    return None
                try:
                    stat = path.stat()
                except FileNotFoundError:
                    with self.db() as c:
                        cached = c.execute(
                            "SELECT image_id FROM references_cache WHERE thread_id=? AND reference=?",
                            (row["id"], reference_hash),
                        ).fetchone()
                    return self.public(self.get(cached["image_id"])) if cached else None
                if stat.st_size > MAX_BYTES or not path.is_file():
                    return None
                key = (row["id"], str(path), stat.st_mtime_ns, stat.st_size)
                if key in self.import_cache:
                    return self.import_cache[key]
                result = self.save(path.read_bytes(), path.name, row["id"], "returned")
            if len(self.import_cache) > 2000:
                self.import_cache.clear()
            self.import_cache[key] = result
            with self.db() as c:
                c.execute(
                    "INSERT OR REPLACE INTO references_cache VALUES(?,?,?)",
                    (row["id"], reference_hash, result["id"]),
                )
            return result
        except (OSError, ValueError, HTTPException):
            return None

    def decorate(self, message, row):
        images = list(message.get("images", []))
        files = list(message.get("files", []))

        def append(asset):
            target = images if asset["kind"] == "image" else files
            if asset["id"] not in {i["id"] for i in target}:
                target.append(asset)

        def link(match):
            asset = self.import_reference(match["url"].strip("<>"), row)
            if not asset:
                return match.group(0)
            append(asset)
            # Images get their own accessible preview/download cards below the text.
            label = match[1].lstrip("!")
            return label + asset["url"] + (match["tail"] or "") + ")"

        text = LINK.sub(link, message.get("text", ""))
        for ref in message.get("imageRefs", []):
            asset = self.import_reference(ref, row)
            if asset:
                append(asset)
        return {
            k: v
            for k, v in {**message, "text": text, "images": images, "files": files}.items()
            if k != "imageRefs"
        }


def image_references(content):
    """Only parse explicit attachment blocks; never scan arbitrary tool text or private reasoning."""
    refs = []
    if isinstance(content, list):
        for item in content:
            refs.extend(image_references(item))
    elif isinstance(content, dict):
        typ = content.get("type", "").lower().replace("_", "")
        if typ in (
            "image",
            "inputimage",
            "outputimage",
            "localimage",
            "file",
            "inputfile",
            "outputfile",
            "localfile",
        ):
            url = (
                content.get("image_url")
                or content.get("url")
                or content.get("path")
                or content.get("file_path")
            )
            if isinstance(url, dict):
                url = url.get("url")
            if (
                not url
                and content.get("data")
                and typ in ("image", "inputimage", "outputimage", "localimage")
            ):
                url = "data:" + content.get("mimeType", "image/png") + ";base64," + content["data"]
            if url:
                refs.append(url)
        for key in ("content", "output", "contentItems", "result"):
            refs.extend(image_references(content.get(key)))
    return refs
