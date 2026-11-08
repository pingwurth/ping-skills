"""corpus.py: 证据分层(D12)、生成物排除(D4)、兄弟裁剪(D13)、匹配原语。"""

from __future__ import annotations

from pwiki_lib import corpus


def test_path_match_substring_and_glob():
    assert corpus.path_match("src/client/app.tsx", "/client/")
    assert corpus.path_match("src/widget.tsx", "*.tsx")
    assert corpus.path_match("src/a/b_test.go", "_test.go")
    assert not corpus.path_match("src/app.py", "*.tsx")


def test_d4_generated_excluded():
    assert corpus.is_generated("rpc/user_pb2.py")
    assert corpus.is_generated("api/model.generated.ts")
    assert not corpus.is_generated("api/model.ts")


def test_d12_weak_evidence_tiers():
    assert corpus.is_weak_evidence("docs/README.md")
    assert corpus.is_weak_evidence("pnpm-lock.yaml")
    assert corpus.is_weak_evidence("CHANGELOG.md")
    assert not corpus.is_weak_evidence("src/core/engine.py")
    # never-cite 比 weak 更严: CLAUDE.md 永不回填
    assert corpus.is_never_cite("CLAUDE.md")
    assert not corpus.is_never_cite("docs/design/adr-001.md")


def test_d13_trim_sibling_overlap():
    pages = [
        {"id": "a", "path": "A.md", "source_files": ["x.py", "y.py", "z.py"]},
        {"id": "b", "path": "B.md", "source_files": ["x.py", "y.py", "z.py", "w.py"]},
    ]
    corpus.trim_sibling_overlap(pages, notes := [])
    assert pages[1]["source_files"][0] in ("x.py", "w.py")
    assert len(pages[1]["source_files"]) < 4
    assert any(n["code"] == "sibling/overlap" for n in notes)
    # 下限: 不裁到 2 以下
    pages2 = [
        {"id": "a", "path": "A.md", "source_files": ["x.py", "y.py"]},
        {"id": "b", "path": "B.md", "source_files": ["x.py", "y.py"]},
    ]
    corpus.trim_sibling_overlap(pages2)
    assert len(pages2[1]["source_files"]) >= 2


def test_d13_primary_first():
    page = {"id": "engine-core", "source_files": ["misc.py", "engine/core.py"]}
    corpus.primary_first(page)
    assert page["source_files"][0] == "engine/core.py"


def test_corpus_collect_filters_and_grep_backfill(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "auth.py").write_text("from rbac import check\n")
    (repo / "gen").mkdir()
    (repo / "gen" / "user_pb2.py").write_text("generated\n")
    (repo / "docs").mkdir()
    (repo / "docs" / "auth.md").write_text("# auth\n")
    files = [
        {"path": "src/auth.py", "lang": "python", "lines": 10, "bytes": 30},
        {"path": "gen/user_pb2.py", "lang": "python", "lines": 5, "bytes": 10},
        {"path": "docs/auth.md", "lang": "markdown", "lines": 3, "bytes": 10},
    ]
    c = corpus.Corpus(str(repo), files, {"src": {}}, set())
    ev = c.collect({"patterns": ["auth"], "min": 2})
    paths = [f["path"] for f in ev]
    assert "src/auth.py" in paths
    assert "gen/user_pb2.py" not in paths       # D4
    # grep 回填: 模式没命中但正文含关键词
    ev2 = c.collect({"patterns": ["/nonexistent-dir/"], "grep": ["rbac"], "min": 2})
    assert any(f["path"] == "src/auth.py" for f in ev2)


def test_module_for_longest_prefix():
    files = []
    c = corpus.Corpus(".", files, {"services": {}, "services/user": {}}, set())
    assert c.module_for("services/user/api.py") == "services/user"
    assert c.module_for("services/other.py") == "services"
    assert c.module_for("README.md") is None
