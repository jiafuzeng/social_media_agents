from __future__ import annotations

import shutil

from fastapi.testclient import TestClient

from integrated_agent.config import PROJECT_ROOT
from integrated_agent.transports.http import create_matrix_api
from tests.fakes import install_scripted_ask
from integrated_agent.runtimes.matrix.analysis.scripted import ScriptedMatrixModel
from integrated_agent.bootstrap.matrix_service import build_matrix_service


def _app(tmp_path: Path, monkeypatch):
    data_root = tmp_path / "matrix"
    shutil.copytree(PROJECT_ROOT / "data" / "matrix", data_root)
    install_scripted_ask(monkeypatch, ScriptedMatrixModel())
    service = build_matrix_service(PROJECT_ROOT)
    return create_matrix_api(service, data_root=data_root), data_root


def test_catalog_dump_and_account_crud(tmp_path: Path, monkeypatch) -> None:
    app, _data_root = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        dumped = client.get("/api/catalog")
        assert dumped.status_code == 200
        assert len(dumped.json()["accounts"]) == 10
        assert dumped.json()["policy"]["terms"]

        created = client.post(
            "/api/catalog/accounts",
            json={
                "account_key": "growth-demo",
                "display_name": "增长试用",
                "voice_summary": "短、可核验、拉关注。",
                "guardrail_keys": ["default"],
            },
        )
        assert created.status_code == 201
        listed = client.get("/api/accounts").json()["accounts"]
        assert any(item["account_key"] == "growth-demo" for item in listed)

        updated = client.put(
            "/api/catalog/accounts/growth-demo",
            json={
                "account_key": "growth-demo",
                "display_name": "增长试用改名",
                "voice_summary": "短、可核验、拉关注。",
                "one_liner": "改过的一句话",
                "guardrail_keys": ["default"],
            },
        )
        assert updated.status_code == 200
        assert updated.json()["one_liner"] == "改过的一句话"

        attached = client.post(
            "/api/catalog/accounts/growth-demo/guardrails",
            json={"guardrail_key": "maker", "index": 1},
        )
        assert attached.status_code == 200
        assert attached.json()["guardrail_keys"] == ["default", "maker"]

        deleted = client.delete("/api/catalog/accounts/growth-demo")
        assert deleted.status_code == 204
        keys = [item["account_key"] for item in client.get("/api/accounts").json()["accounts"]]
        assert "growth-demo" not in keys


def test_catalog_insert_term_and_reject_last_account_delete(
    tmp_path: Path, monkeypatch
) -> None:
    app, _data_root = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        inserted = client.post(
            "/api/catalog/policy/terms",
            json={"term": "必赚", "index": 0},
        )
        assert inserted.status_code == 200
        assert inserted.json()["terms"][0] == "必赚"

        for key in [
            item["account_key"]
            for item in client.get("/api/accounts").json()["accounts"]
            if item["account_key"] != "default"
        ]:
            client.delete(f"/api/catalog/accounts/{key}")
        last = client.delete("/api/catalog/accounts/default")
        assert last.status_code == 409


def test_catalog_cannot_delete_twitter_platform(tmp_path: Path, monkeypatch) -> None:
    app, _data_root = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        rejected = client.delete("/api/catalog/platforms/x-twitter")
        assert rejected.status_code == 409


def test_matrix_page_includes_catalog_editor(tmp_path: Path, monkeypatch) -> None:
    data_root = tmp_path / "matrix"
    shutil.copytree(PROJECT_ROOT / "data" / "matrix", data_root)
    install_scripted_ask(monkeypatch, ScriptedMatrixModel())
    app = create_matrix_api(
        build_matrix_service(PROJECT_ROOT),
        static_root=PROJECT_ROOT / "static",
        data_root=data_root,
    )
    with TestClient(app) as client:
        page = client.get("/matrix").text
        assert 'id="catalogEditor"' in page
        assert 'id="catalogTabs"' in page
        assert 'class="catalog-grid"' in page
        assert "/static/matrix-catalog.js" in page
