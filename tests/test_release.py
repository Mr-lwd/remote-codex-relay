"""Publication boundary regression: local data and credentials never enter the bundle."""

from pathlib import Path

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
