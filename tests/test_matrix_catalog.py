from __future__ import annotations

import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from integrated_agent.config import PROJECT_ROOT
from integrated_agent.transports.http import create_matrix_api
from tests.fakes import ScriptedMatrixModel, install_scripted_ask
from integrated_agent.bootstrap.matrix_service import build_matrix_service


def _app(tmp_path: Path, monkeypatch):
    data_root = tmp_path / "matrix"
    shutil.copytree(PROJECT_ROOT / "data" / "matrix", data_root)
    install_scripted_ask(monkeypatch, ScriptedMatrixModel())
    service = build_matrix_service(
        PROJECT_ROOT,
        identity_root=tmp_path / "identity",
    )
    return create_matrix_api(service, data_root=data_root), data_root


def test_catalog_dump_and_account_crud(tmp_path: Path, monkeypatch) -> None:
    app, _data_root = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        dumped = client.get("/api/catalog")
        assert dumped.status_code == 200
        assert len(dumped.json()["accounts"]) == 8
        assert len(dumped.json()["interactions"]) == 4
        policy = dumped.json()["policy"]
        assert isinstance(policy, list)
        assert len(policy) == 5
        assert {item["term_list_id"] for item in policy} == {
            "baseline",
            "medical",
            "finance",
            "civic-hard",
            "support-hard",
        }

        created = client.post(
            "/api/catalog/accounts",
            json={
                "account_key": "growth-demo",
                "display_name": "增长试用",
                "voice_summary": "短、可核验、拉关注。",
                "guardrail_keys": ["default"],
                "term_list_keys": ["baseline"],
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
                "term_list_keys": ["baseline"],
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

        terms = client.post(
            "/api/catalog/accounts/growth-demo/term-lists",
            json={"term_list_id": "finance", "index": 1},
        )
        assert terms.status_code == 200
        assert terms.json()["term_list_keys"] == ["baseline", "finance"]

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
            "/api/catalog/policy/baseline/terms",
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


def test_catalog_interaction_crud(tmp_path: Path, monkeypatch) -> None:
    app, _data_root = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        created = client.post(
            "/api/catalog/interactions",
            json={
                "interaction_key": "quiet-desk",
                "display_name": "少回试用",
                "voice_summary": "短、不推销。",
                "skip_guidance": "攻击默认 skip。",
                "guardrail_keys": ["default"],
                "term_list_keys": ["baseline"],
            },
        )
        assert created.status_code == 201
        listed = client.get("/api/interactions").json()["interactions"]
        assert any(item["interaction_key"] == "quiet-desk" for item in listed)

        updated = client.put(
            "/api/catalog/interactions/quiet-desk",
            json={
                "interaction_key": "quiet-desk",
                "display_name": "少回试用改名",
                "voice_summary": "短、不推销。",
                "one_liner": "改过的一句话",
                "skip_guidance": "攻击默认 skip。",
                "guardrail_keys": ["default"],
                "term_list_keys": ["baseline"],
            },
        )
        assert updated.status_code == 200
        assert updated.json()["one_liner"] == "改过的一句话"

        attached = client.post(
            "/api/catalog/interactions/quiet-desk/guardrails",
            json={"guardrail_key": "support", "index": 1},
        )
        assert attached.status_code == 200
        assert attached.json()["guardrail_keys"] == ["default", "support"]

        terms = client.post(
            "/api/catalog/interactions/quiet-desk/term-lists",
            json={"term_list_id": "support-hard", "index": 1},
        )
        assert terms.status_code == 200
        assert terms.json()["term_list_keys"] == ["baseline", "support-hard"]

        deleted = client.delete("/api/catalog/interactions/quiet-desk")
        assert deleted.status_code == 204
        keys = [
            item["interaction_key"]
            for item in client.get("/api/interactions").json()["interactions"]
        ]
        assert "quiet-desk" not in keys


def test_catalog_cannot_delete_twitter_platform(tmp_path: Path, monkeypatch) -> None:
    app, _data_root = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        rejected = client.delete("/api/catalog/platforms/x-twitter")
        assert rejected.status_code == 409


def test_catalog_term_list_crud_and_in_use_delete(tmp_path: Path, monkeypatch) -> None:
    app, _data_root = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        created = client.post(
            "/api/catalog/policy",
            json={
                "term_list_id": "temp-pack",
                "display_name": "临时清单",
                "summary": "测试用",
                "disclaimer": "演示",
                "terms": ["临时词"],
            },
        )
        assert created.status_code == 201

        updated = client.put(
            "/api/catalog/policy/temp-pack",
            json={
                "term_list_id": "temp-pack",
                "display_name": "临时清单改名",
                "summary": "测试用",
                "disclaimer": "演示",
                "terms": ["临时词", "另一词"],
            },
        )
        assert updated.status_code == 200
        assert updated.json()["display_name"] == "临时清单改名"
        assert "另一词" in updated.json()["terms"]

        removed = client.delete("/api/catalog/policy/temp-pack")
        assert removed.status_code == 204

        blocked = client.delete("/api/catalog/policy/baseline")
        assert blocked.status_code == 409


def test_matrix_page_includes_catalog_editor(tmp_path: Path, monkeypatch) -> None:
    data_root = tmp_path / "matrix"
    shutil.copytree(PROJECT_ROOT / "data" / "matrix", data_root)
    install_scripted_ask(monkeypatch, ScriptedMatrixModel())
    app = create_matrix_api(
        build_matrix_service(PROJECT_ROOT, identity_root=tmp_path / "identity"),
        static_root=PROJECT_ROOT / "static",
        data_root=data_root,
    )
    with TestClient(app) as client:
        page = client.get("/matrix").text
        assert 'id="catalogEditor"' in page
        assert 'id="catalogTabs"' in page
        assert 'data-kind="interactions"' in page
        assert 'data-kind="policy">硬禁词' in page
        assert 'id="catalogKindHint"' in page
        assert ">功能模块<" in page
        assert 'id="scenePrev"' in page
        assert 'id="sceneNext"' in page
        assert 'class="catalog-grid"' in page
        assert "/static/matrix-catalog.js?v=kb-ui-44" in page
        js = (PROJECT_ROOT / "static" / "matrix-catalog.js").read_text(encoding="utf-8")
        kind_fn = js.split("function setCatalogKind", 1)[1].split("async function loadCatalog", 1)[0]
        assert "renderCatalogList();" in kind_fn
