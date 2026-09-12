"""Build a source-only ZIP using a fixed allowlist; never include runtime data."""

import argparse
import ast
import fnmatch
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import subprocess
import sys
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
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/dependabot.yml",
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
    ".github/ISSUE_TEMPLATE": {".yml", ".yaml"},
}
# Match .gitignore's private/generated exclusions at every directory depth.
EXCLUDED_PARTS = (
    "node_modules",
    ".venv*",
    "venv",
    "dist",
    "build",
    "runtime",
    "output",
    "tmp",
    "temp",
    "backups",
    "__pycache__",
    ".*",
    "*.local.*",
    "*.relay-backup-*",
    "*.relay-failed-*",
    "credentials.*",
    "secrets.*",
    "*.bak",
    "*.orig",
    "*.rej",
    "*.swp",
    "*.swo",
    "*~",
)
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


def validate_content(content, relative, private_values=()):
    for variant in content_variants(content, relative.suffix):
        if any(value in variant for value in private_values):
            raise ValueError(f"Private identifier in {relative}; remove it before publishing")
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


def allowed_source(relative):
    relative = Path(relative)
    if relative.as_posix() in FILES:
        return True
    for directory, extensions in DIRECTORIES.items():
        if relative.is_relative_to(directory) and relative.suffix in extensions:
            parts = relative.relative_to(directory).parts
            return not any(
                fnmatch.fnmatch(part, pattern) for part in parts for pattern in EXCLUDED_PARTS
            )
    return False


def source_files(root=ROOT, private_values=()):
    paths = [root / name for name in FILES]
    for name, extensions in DIRECTORIES.items():
        directory = root / name
        if directory.is_symlink():
            raise ValueError(f"Release rejects symlink directory: {name}")
        paths.extend(
            p
            for p in directory.rglob("*")
            if p.suffix in extensions and allowed_source(p.relative_to(root))
        )
    for path in paths:
        if (
            not path.is_file()
            or path.is_symlink()
            or not path.resolve().is_relative_to(root.resolve())
        ):
            raise ValueError(f"Missing or unsafe release source: {path.relative_to(root)}")
        if any((root / parent).is_symlink() for parent in path.relative_to(root).parents):
            raise ValueError(f"Release rejects symlink parent: {path.relative_to(root)}")
        validate_content(path.read_bytes(), path.relative_to(root), private_values)
    return sorted(paths)


def git_output(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True)
    if result.returncode:
        raise ValueError("Git inspection failed; use a complete local checkout")
    return result.stdout


def check_git(root=ROOT, *, history=False, private_values=()):
    """Inspect the actual index and reachable history, without printing contents."""
    checked = set()

    def entry(mode, oid, name):
        path = Path(name.decode("utf-8"))
        if mode not in (b"100644", b"100755") or not allowed_source(path):
            raise ValueError(f"Non-source Git entry: {path}; remove it before publishing")
        key = (oid, path.suffix)
        if key not in checked:
            validate_content(
                git_output(root, "cat-file", "blob", oid.decode()), path, private_values
            )
            checked.add(key)

    for record in git_output(root, "ls-files", "--stage", "-z").split(b"\0"):
        if not record:
            continue
        metadata, name = record.split(b"\t", 1)
        mode, oid, stage = metadata.split()
        if stage != b"0":
            raise ValueError("Resolve Git conflicts before publishing")
        entry(mode, oid, name)
    if history:
        if git_output(root, "rev-parse", "--is-shallow-repository").strip() == b"true":
            raise ValueError("History check requires a full clone; run git fetch --unshallow")
        for commit in git_output(root, "rev-list", "--all").splitlines():
            ident = commit.decode()
            validate_content(
                git_output(root, "cat-file", "commit", ident),
                Path(f"commit-{ident[:12]}"),
                private_values,
            )
            for record in git_output(root, "ls-tree", "-r", "-z", ident).split(b"\0"):
                if record:
                    metadata, name = record.split(b"\t", 1)
                    mode, _, oid = metadata.split()
                    entry(mode, oid, name)
        # Annotated tag messages and public branch/tag names are metadata too.
        refs = git_output(root, "for-each-ref", "--format=%(refname) %(objecttype) %(objectname)")
        validate_content(refs, Path("git-refs"), private_values)
        for ref in refs.splitlines():
            _, kind, oid = ref.rsplit(b" ", 2)
            if kind == b"tag":
                validate_content(
                    git_output(root, "cat-file", "tag", oid.decode()),
                    Path("git-tag"),
                    private_values,
                )
    return len(checked)


def release_version(root=ROOT):
    version = json.loads((root / "package.json").read_text())["version"]
    lock = json.loads((root / "package-lock.json").read_text())
    tree = ast.parse((root / "server/__init__.py").read_text())
    backend = next(
        (
            ast.literal_eval(node.value)
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "__version__"
                for target in node.targets
            )
        ),
        None,
    )
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.]+)?", version):
        raise ValueError("Invalid release version")
    if any(
        value != version for value in (lock["version"], lock["packages"][""]["version"], backend)
    ):
        raise ValueError("Version mismatch: package.json, package-lock.json and server/__init__.py")
    if f"\n## {version}\n" not in (root / "CHANGELOG.md").read_text():
        raise ValueError("Current version is missing from CHANGELOG.md")
    return version


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "output/releases")
    parser.add_argument("--check", action="store_true", help="Validate and list source files only")
    parser.add_argument(
        "--git-check", action="store_true", help="Also inspect staged/tracked files"
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help="Also inspect all reachable Git history (full clone required)",
    )
    parser.add_argument(
        "--private-values-file",
        type=Path,
        help="Local file of private identifiers, one per line; never prints matched values",
    )
    args = parser.parse_args()
    private_values = (
        tuple(
            line.strip()
            for line in args.private_values_file.read_bytes().splitlines()
            if line.strip()
        )
        if args.private_values_file
        else ()
    )
    sources = source_files(private_values=private_values)
    version = release_version()
    if args.git_check or args.history:
        count = check_git(history=args.history, private_values=private_values)
        print(f"Git privacy check passed ({count} unique source blobs)")
    if args.check:
        for source in sources:
            print(source.relative_to(ROOT))
        return
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
    try:
        main()
    except (ValueError, OSError, SyntaxError) as error:
        # In particular, never print the source line from ast.parse's traceback.
        print(f"Release check failed: {error}", file=sys.stderr)
        raise SystemExit(1) from None
