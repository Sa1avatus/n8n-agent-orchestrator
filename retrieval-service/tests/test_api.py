from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from ahawr_retrieval.api import create_app
from ahawr_retrieval.config import Settings

from .conftest import make_service


def client_for(settings: Settings) -> TestClient:
    return TestClient(create_app(settings, service_factory=make_service))


def test_index_retrieve_invalidate_roundtrip(settings: Settings, workspace: Path) -> None:
    with client_for(settings) as client:
        assert client.get("/health").json()["status"] == "ok"
        index = client.post("/index", json={"corpus_id": "ws", "root": str(workspace)})
        assert index.status_code == 200, index.text
        assert index.json()["code_generation"] == 1

        retrieve = client.post(
            "/retrieve",
            json={
                "profile": "worker",
                "corpora": ["ws"],
                "task": {
                    "title": "Fix totals",
                    "objective": "compute_total counts tax twice",
                    "acceptance_criteria": "tests pass\nno double counting",
                },
            },
        )
        assert retrieve.status_code == 200, retrieve.text
        body = retrieve.json()
        chunk = body["chunks"][0]
        for key in (
            "source_type",
            "path",
            "document",
            "chunk_id",
            "symbol",
            "content_hash",
            "snapshot_id",
            "version",
            "scores",
            "rank",
        ):
            assert key in chunk
        assert chunk["scores"]["final"] > 0

        invalidate = client.post(
            "/invalidate",
            json={
                "corpus_id": "ws",
                "paths": ["app/parser.py"],
                "reason": "test",
            },
        )
        assert invalidate.status_code == 200
        assert invalidate.json()["invalidated_chunks"] > 0
        again = client.post(
            "/retrieve",
            json={
                "corpora": ["ws"],
                "query": "compute_total tax",
                "cache": "bypass",
            },
        ).json()
        assert all(c["path"] != "app/parser.py" for c in again["chunks"])

        corpora = client.get("/corpora").json()
        assert corpora[0]["corpus_id"] == "ws"
        assert corpora[0]["chunks"]["code"]["invalidated"] > 0


def test_validation_and_errors(settings: Settings) -> None:
    with client_for(settings) as client:
        assert client.post("/retrieve", json={"corpora": ["ws"]}).status_code == 422
        assert client.post("/retrieve", json={"corpora": ["../x"], "query": "a"}).status_code == 422
        missing = client.post("/retrieve", json={"corpora": ["absent"], "query": "a"})
        assert missing.status_code == 404
        assert client.post("/invalidate", json={"corpus_id": "ws"}).status_code == 422
        outside = client.post("/index", json={"corpus_id": "ws", "root": "/"})
        assert outside.status_code == 400
        assert client.get("/corpora/absent").status_code == 404


def test_bearer_auth_when_api_key_set(settings: Settings) -> None:
    secured = replace(settings, api_key="s3cret")
    with client_for(secured) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/corpora").status_code == 401
        ok = client.get("/corpora", headers={"Authorization": "Bearer s3cret"})
        assert ok.status_code == 200


def test_workspace_indexing_disabled_without_allowed_roots(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "d")
    with client_for(settings) as client:
        response = client.post("/index", json={"corpus_id": "ws", "root": str(tmp_path)})
        assert response.status_code == 400
        assert "RETRIEVAL_ALLOWED_ROOTS" in response.json()["detail"]
        docs = client.post(
            "/index",
            json={
                "corpus_id": "kb",
                "documents": [{"path": "a.md", "content": "# A\n\nText."}],
            },
        )
        assert docs.status_code == 200


def test_bootstrap_corpora_are_indexed_on_first_request(
    settings: Settings, workspace: Path
) -> None:
    configured = replace(settings, bootstrap_corpora={"ahawr-workspace": str(workspace)})
    with client_for(configured) as client:
        response = client.post(
            "/retrieve", json={"corpora": ["ahawr-workspace"], "query": "compute_total"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["chunks"][0]["symbol"] == "compute_total"
        assert (
            client.post("/retrieve", json={"corpora": ["not-declared"], "query": "x"}).status_code
            == 404
        )


def test_settings_parse_bootstrap_pairs() -> None:
    parsed = Settings.from_env({"RETRIEVAL_BOOTSTRAP_CORPORA": "ws=/workspace, docs=/docs,bad"})
    assert parsed.bootstrap_corpora == {"ws": "/workspace", "docs": "/docs"}


def test_corpus_roots_from_host_paths_are_indexed_on_first_use(
    settings: Settings, workspace: Path
) -> None:
    host = "D:\\OpenAIProjects\\" + workspace.name
    configured = replace(settings, path_map={"D:\\OpenAIProjects": str(workspace.parent)})
    with client_for(configured) as client:
        body = {
            "corpora": ["d-openaiprojects-app"],
            "corpus_roots": {"d-openaiprojects-app": host},
            "query": "compute_total",
        }
        ok = client.post("/retrieve", json=body)
        assert ok.status_code == 200, ok.text
        assert ok.json()["chunks"][0]["symbol"] == "compute_total"
        # outside RETRIEVAL_ALLOWED_ROOTS is refused
        bad = {"corpora": ["etc"], "corpus_roots": {"etc": "/etc"}, "query": "x"}
        assert client.post("/retrieve", json=bad).status_code in (400, 404)


def test_map_host_path() -> None:
    from ahawr_retrieval.config import map_host_path

    table = {"D:\\": "/host/d", "D:\\n8n\\workspace": "/workspace"}
    assert map_host_path("D:\\OpenAIProjects\\foo", table) == "/host/d/OpenAIProjects/foo"
    assert map_host_path("d:/n8n/workspace/app", table) == "/workspace/app"
    assert map_host_path("D:\\", table) == "/host/d"
    assert map_host_path("/already/linux", table) == "/already/linux"
    assert Settings.from_env({"RETRIEVAL_PATH_MAP": "D:\\=/host/d"}).path_map == {"D:\\": "/host/d"}


def test_corpus_roots_first_use_indexes_a_new_root_and_finds_new_files_on_next_query(
    settings: Settings, tmp_path: Path
) -> None:
    root = tmp_path / "mission"
    root.mkdir()
    (root / "app").mkdir()
    (root / "app" / "parser.py").write_text(
        "def compute_total(lines):\n    return sum(float(l.split()[-1]) for l in lines)\n",
        encoding="utf-8",
    )
    with client_for(settings) as client:
        first = client.post(
            "/retrieve",
            json={
                "corpora": ["m-work"],
                "corpus_roots": {"m-work": str(root)},
                "query": "compute_total",
            },
        )
        assert first.status_code == 200, first.text
        assert first.json()["chunks"][0]["symbol"] == "compute_total"
        # a newly written file appears in the index by the next (synced) request
        (root / "notes").mkdir()
        (root / "notes" / "meeting.md").write_text(
            "# Meeting notes\n\nWe fixed the invoice totals.\n",
            encoding="utf-8",
        )
        again = client.post(
            "/retrieve",
            json={
                "corpora": ["m-work"],
                "query": "invoice totals meeting",
                "cache": "bypass",
            },
        )
        assert again.status_code == 200, again.text
        paths = {c["path"] for c in again.json()["chunks"]}
        assert "notes/meeting.md" in paths
        # and the corpus is no longer stale
        assert not again.json()["snapshots"]["m-work"]["stale"]


def test_corpus_root_deletion_marks_corpus_stale_and_serves_no_fragments(
    settings: Settings, tmp_path: Path
) -> None:
    root = tmp_path / "gone"
    root.mkdir()
    (root / "app").mkdir()
    (root / "app" / "parser.py").write_text(
        "def compute_total(lines):\n    return sum(float(l.split()[-1]) for l in lines)\n",
        encoding="utf-8",
    )
    with client_for(settings) as client:
        first = client.post(
            "/retrieve",
            json={
                "corpora": ["gone-work"],
                "corpus_roots": {"gone-work": str(root)},
                "query": "compute_total",
            },
        )
        assert first.status_code == 200, first.text
        assert first.json()["chunks"][0]["symbol"] == "compute_total"
        # the root disappears; the next request marks the corpus stale and serves nothing
        (root / "app" / "parser.py").unlink()
        (root / "app").rmdir()
        root.rmdir()
        again = client.post(
            "/retrieve",
            json={
                "corpora": ["gone-work"],
                "query": "compute_total",
                "freshness_mode": "sync",
                "cache": "bypass",
            },
        )
        assert again.status_code == 200, again.text
        body = again.json()
        assert body["chunks"] == []
        assert body["snapshots"]["gone-work"]["stale"] is True
        assert "stale_corpus:gone-work:root no longer exists; no fragments served" in body["notes"]
        # an out-of-allowed-root is rejected with a clear message, not a crash
        bad = client.post(
            "/retrieve",
            json={"corpora": ["etc"], "corpus_roots": {"etc": "/etc"}, "query": "x"},
        )
        assert bad.status_code == 400
        assert "RETRIEVAL_ALLOWED_ROOTS" in bad.json()["detail"]
