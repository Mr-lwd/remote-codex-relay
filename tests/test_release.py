"""Publication boundary regression: local data and credentials never enter the bundle."""

from pathlib import Path
import subprocess

import pytest
from scripts import release


@pytest.fixture
def sources(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "FILES", ("README.md",))
    monkeypatch.setattr(release, "DIRECTORIES", {"server": {".py"}})
    (tmp_path / "README.md").write_text("Public readme")
    (tmp_path / "server").mkdir()
    (tmp_path / "server/app.py").write_text('print("hello")')
    return tmp_path


def test_source_allowlist_excludes_runtime_output_env_and_generated_assets(sources):
    for directory in ("runtime", "output", "dist", ".venv"):
        (sources / directory).mkdir()
        (sources / directory / "private.py").write_text("private")
    (sources / "relay.toml").write_text("private")
    (sources / ".env").write_text("private")
    (sources / "server/access.txt").write_text("private")
    assert [str(path.relative_to(sources)) for path in release.source_files(sources)] == [
        "README.md",
        "server/app.py",
    ]


def test_credential_pattern_is_rejected_without_echoing_secret(sources):
    secret = "sk-" + ("a" * 48)
    (sources / "README.md").write_text(secret)
    with pytest.raises(ValueError) as error:
        release.source_files(sources)
    assert secret not in str(error.value)
    assert "README.md" in str(error.value)


def test_concatenated_credential_is_rejected_without_echoing_secret(sources):
    secret = "sk-" + ("a" * 48)
    (sources / "server/app.py").write_text(f'key = "sk-" + {secret[3:]!r}')
    with pytest.raises(ValueError) as error:
        release.source_files(sources)
    assert secret not in str(error.value)
    assert "server/app.py" in str(error.value)


@pytest.mark.parametrize(
    "content",
    [
        'host = "203.0.113.42"',
        'host = "203.0." + "113.42"',
        'host = "203.0." "113.42"',
        'directory = "/home/example/project"',
        'directory = "/home/" + "example/" + "project"',
        'directory = "/Users/example/project"',
    ],
)
def test_deployment_identifiers_are_rejected_without_echoing_values(sources, monkeypatch, content):
    # Disable example exemptions to exercise rejection with synthetic values only.
    monkeypatch.setattr(release, "EXAMPLE_NETWORKS", ())
    monkeypatch.setattr(release, "EXAMPLE_USERS", set())
    (sources / "server/app.py").write_text(content)
    with pytest.raises(ValueError) as error:
        release.source_files(sources)
    assert "server/app.py" in str(error.value)
    assert "203.0." not in str(error.value) and "example/project" not in str(error.value)


def test_loopback_listening_and_documentation_examples_are_publishable(sources):
    (sources / "README.md").write_text(
        "127.0.0.1 0.0.0.0 192.0.2.42 198.51.100.42 203.0.113.42 "
        "/home/example/project /Users/example/project ~/projects"
    )
    assert len(release.source_files(sources)) == 2


def test_symlink_source_is_rejected(sources, tmp_path):
    (sources / "server/app.py").unlink()
    (sources / "server/app.py").symlink_to(sources / "README.md")
    with pytest.raises(ValueError):
        release.source_files(sources)


def test_real_publish_set_passes_generic_privacy_checks():
    root = Path(__file__).resolve().parent.parent
    assert release.source_files(root)


@pytest.mark.parametrize(
    "relative",
    [
        "server/runtime/private.py",
        "server/backups/private.py",
        "server/.app.relay-backup-example/private.py",
        "server/cache.relay-failed-example/private.py",
        "server/settings.local.py",
        "server/secrets.py",
        "server/credentials.py",
        "server/.hidden/private.py",
    ],
)
def test_nested_private_sources_are_excluded(sources, relative):
    path = sources / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('password = "private"')
    assert path not in release.source_files(sources)


def test_local_private_values_cover_domains_and_concatenated_literals(sources):
    (sources / "server/app.py").write_text('url = "https://private." + "example.invalid"')
    with pytest.raises(ValueError, match="Private identifier") as error:
        release.source_files(sources, (b"private.example.invalid",))
    assert "private.example.invalid" not in str(error.value)


def test_explicit_file_with_symlink_parent_is_rejected(sources, monkeypatch):
    (sources / "linked").symlink_to(sources / "server", target_is_directory=True)
    monkeypatch.setattr(release, "FILES", ("README.md", "linked/app.py"))
    with pytest.raises(ValueError, match="symlink parent"):
        release.source_files(sources)


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def repository(sources):
    git(sources, "init", "-b", "main")
    git(sources, "config", "user.name", "Release Test")
    git(sources, "config", "user.email", "noreply@example.invalid")
    git(sources, "add", ".")
    git(sources, "commit", "-m", "Initial public source")
    return sources


def test_git_checks_staged_content_even_when_worktree_has_been_cleaned(repository):
    path = repository / "README.md"
    path.write_text("sk-" + "a" * 48)
    git(repository, "add", "README.md")
    path.write_text("Clean working tree content")
    assert release.source_files(repository)
    with pytest.raises(ValueError, match="Credential-like"):
        release.check_git(repository)


def test_history_detects_deleted_private_source(repository):
    path = repository / "server/removed.py"
    path.write_text('key = "sk-" + "' + "b" * 48 + '"')
    git(repository, "add", ".")
    git(repository, "commit", "-m", "Old content")
    git(repository, "rm", "server/removed.py")
    git(repository, "commit", "-m", "Remove old content")
    assert release.check_git(repository) == 2
    with pytest.raises(ValueError, match="Credential-like"):
        release.check_git(repository, history=True)


def test_git_rejects_force_added_runtime_data(repository):
    (repository / "runtime").mkdir()
    (repository / "runtime/access.txt").write_text("synthetic")
    git(repository, "add", ".")
    with pytest.raises(ValueError, match="Non-source Git entry"):
        release.check_git(repository)


@pytest.mark.parametrize("metadata", ["commit", "tag"])
def test_git_metadata_checks_private_identifiers(repository, metadata):
    private = b"private.example.invalid"
    if metadata == "commit":
        git(repository, "commit", "--allow-empty", "-m", private.decode())
    else:
        git(repository, "tag", "-a", "v1.0.0", "-m", private.decode())
    with pytest.raises(ValueError, match="Private identifier"):
        release.check_git(repository, history=True, private_values=(private,))


def test_git_history_requires_full_clone(repository):
    (repository / ".git/shallow").write_text(git(repository, "rev-parse", "HEAD"))
    with pytest.raises(ValueError, match="full clone"):
        release.check_git(repository, history=True)


def test_public_git_history_passes(repository):
    assert release.check_git(repository, history=True) == 2


def test_release_versions_agree_and_drift_is_rejected(tmp_path):
    import shutil

    root = Path(__file__).resolve().parent.parent
    (tmp_path / "server").mkdir()
    for name in ("package.json", "package-lock.json", "server/__init__.py", "CHANGELOG.md"):
        shutil.copyfile(root / name, tmp_path / name)
    assert release.release_version(tmp_path) == release.release_version(root)
    (tmp_path / "server/__init__.py").write_text('__version__ = "0.0.0"')
    with pytest.raises(ValueError, match="Version mismatch"):
        release.release_version(tmp_path)
