"""Build a source-only ZIP using a fixed allowlist; never include runtime data."""

import argparse
import ast
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parent.parent
FILES = (
    ".editorconfig",
    ".gitattributes",
    ".gitignore",
    ".node-version",
    ".prettierignore",
    ".prettierrc.json",
    "README.md",
    "LICENSE",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "CHANGELOG.md",
    "index.html",
    "package.json",
    "package-lock.json",
    "pyproject.toml",
    "requirements.in",
    "requirements.txt",
    "requirements-dev.txt",
    "requirements-browser.txt",
    "relay.example.toml",
    "vite.config.js",
    "docs/images/interface-overview.png",
    "docs/images/readme-banner-light.svg",
    "docs/images/readme-banner-dark.svg",
    "docs/images/readme-badges.svg",
)
DIRECTORIES = {
    "server": {".py"},
    "src": {".js", ".jsx", ".css"},
    "tests": {".py"},
    "scripts": {".py", ".sh"},
    "docs": {".md"},
    "deploy": {".example"},
    ".github/workflows": {".yml", ".yaml"},
}
SECRET_PATTERNS = (
    re.compile(rb"\bsk-[A-Za-z0-9_-]{32,}"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{40,}"),
)
IPV4_PATTERN = re.compile(rb"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
HOME_PATH_PATTERN = re.compile(rb"/(?:home|Users)/([A-Za-z0-9_.-]+)(?=[/\s\"']|$)")
# RFC 5737 examples are safe for public documentation and synthetic tests.
EXAMPLE_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")
)
EXAMPLE_USERS = {b"example", b"user", b"USER"}


def content_variants(content, suffix):
    """Inspect Python constant concatenation without executing any source code."""
    yield content
    if suffix != ".py":
        return
    tree = ast.parse(content)
    constants = {}
    for node in reversed(list(ast.walk(tree))):
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
            constants[node] = node.value
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = constants.get(node.left), constants.get(node.right)
            if isinstance(left, (str, bytes)) and type(left) is type(right):
                constants[node] = left + right
    for value in constants.values():
        yield value.encode() if isinstance(value, str) else value


def validate_content(content, relative):
    for variant in content_variants(content, relative.suffix):
        if any(pattern.search(variant) for pattern in SECRET_PATTERNS):
            raise ValueError(f"Credential-like content in {relative}; remove it before publishing")
        for match in IPV4_PATTERN.finditer(variant):
            try:
                address = ipaddress.ip_address(match[0].decode())
            except ValueError:
                continue
            if not (
                address.is_loopback
                or address.is_unspecified
                or any(address in network for network in EXAMPLE_NETWORKS)
            ):
                raise ValueError(f"Non-example IP address in {relative}; use a placeholder")
        for match in HOME_PATH_PATTERN.finditer(variant):
            if match[1] not in EXAMPLE_USERS:
                raise ValueError(f"Personal home path in {relative}; use a portable path")


def source_files(root=ROOT):
    paths = [root / name for name in FILES]
    for name, extensions in DIRECTORIES.items():
        directory = root / name
        if directory.is_symlink():
            raise ValueError(f"Release rejects symlink directory: {name}")
        paths.extend(
            p
            for p in directory.rglob("*")
            if p.suffix in extensions and "__pycache__" not in p.parts
        )
    for path in paths:
        if (
            not path.is_file()
            or path.is_symlink()
            or not path.resolve().is_relative_to(root.resolve())
        ):
            raise ValueError(f"Missing or unsafe release source: {path.relative_to(root)}")
        validate_content(path.read_bytes(), path.relative_to(root))
    return sorted(paths)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "output/releases")
    parser.add_argument("--check", action="store_true", help="Validate and list source files only")
    args = parser.parse_args()
    sources = source_files()
    if args.check:
        for source in sources:
            print(source.relative_to(ROOT))
        return
    version = json.loads((ROOT / "package.json").read_text())["version"]
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.]+)?", version):
        raise ValueError("Invalid release version")
    prefix = f"codex-relay-{version}"
    args.output.mkdir(parents=True, exist_ok=True)
    archive = args.output / f"{prefix}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for source in sources:
            # Stable timestamp and permissions give reproducible archives.
            entry = zipfile.ZipInfo(
                f"{prefix}/{source.relative_to(ROOT)}", date_time=(2026, 1, 1, 0, 0, 0)
            )
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = (0o100755 if source.suffix == ".sh" else 0o100644) << 16
            target.writestr(entry, source.read_bytes())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (args.output / "SHA256SUMS").write_text(f"{digest}  {archive.name}\n")
    print(f"{archive} ({len(sources)} source files)")


if __name__ == "__main__":
    main()
