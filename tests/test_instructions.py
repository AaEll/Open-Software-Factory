"""Loading a project's own instruction files as baseline context."""

from pathlib import Path

from osf.instructions import DEFAULT_BUDGET, load


def test_no_project_means_no_instructions():
    assert load(None).render() == ""


def test_a_project_without_an_instruction_file_contributes_nothing(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path / "home"))
    assert load(tmp_path).render() == ""


def test_agents_md_is_read(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path / "home"))
    (tmp_path / "AGENTS.md").write_text("Use tabs. We are monsters.\n", encoding="utf-8")

    rendered = load(tmp_path).render()
    assert "Use tabs" in rendered
    assert "outrank your general habits" in rendered  # the framing that makes it authoritative


def test_agents_md_wins_over_claude_md(tmp_path: Path, monkeypatch):
    """Both files in one repo would otherwise state the conventions twice."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path / "home"))
    (tmp_path / "AGENTS.md").write_text("from agents\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("from claude\n", encoding="utf-8")

    rendered = load(tmp_path).render()
    assert "from agents" in rendered
    assert "from claude" not in rendered


def test_the_global_file_comes_before_the_project(tmp_path: Path, monkeypatch):
    config = tmp_path / "cfg"
    (config / "osf").mkdir(parents=True)
    (config / "osf" / "AGENTS.md").write_text("global rule\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path / "home"))

    project = tmp_path / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text("project rule\n", encoding="utf-8")

    rendered = load(project).render()
    assert rendered.index("global rule") < rendered.index("project rule")  # specific wins last


def test_the_budget_drops_broad_files_before_truncating_the_specific_one(tmp_path, monkeypatch):
    """Half a house rule is worse than none, so the project's own file is the last to suffer."""
    config = tmp_path / "cfg"
    (config / "osf").mkdir(parents=True)
    (config / "osf" / "AGENTS.md").write_text("g" * DEFAULT_BUDGET, encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path / "home"))

    project = tmp_path / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text("the project rule\n", encoding="utf-8")

    instructions = load(project)
    assert [path.name for path, _text in instructions.files] == ["AGENTS.md"]
    assert "the project rule" in instructions.render()
    assert "gggg" not in instructions.render()


def test_an_oversized_project_file_is_truncated_not_dropped(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path / "home"))
    (tmp_path / "AGENTS.md").write_text("x" * (DEFAULT_BUDGET + 500), encoding="utf-8")

    instructions = load(tmp_path)
    assert instructions.truncated  # reported, not silent
    assert "truncated" in instructions.render()
