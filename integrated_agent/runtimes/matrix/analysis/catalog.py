"""矩阵配置目录：读写 yaml，供 HTTP 增删改插；bind_snapshot 每次开跑仍读文件。"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any

import yaml

from integrated_agent.runtimes.matrix.models import MAX_COMPOSE_POSTS, MIN_COMPOSE_POSTS

from .snapshots import (
    TWITTER_PLATFORM_KEY,
    AccountCard,
    GuardrailCard,
    PlatformCard,
    SnapshotError,
    TemplateCard,
    parse_account_card,
    parse_guardrail_card,
    resolve_guardrails,
)


KEY_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class CatalogError(ValueError):
    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class PolicyDoc:
    def __init__(self, *, term_list_id: str, disclaimer: str, terms: list[str]) -> None:
        self.term_list_id = term_list_id
        self.disclaimer = disclaimer
        self.terms = terms

    def as_dict(self) -> dict[str, Any]:
        return {
            "term_list_id": self.term_list_id,
            "disclaimer": self.disclaimer,
            "terms": list(self.terms),
        }


class MatrixCatalog:
    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root
        self._lock = threading.Lock()

    def accounts_path(self) -> Path:
        return self.data_root / "accounts.yaml"

    def platforms_path(self) -> Path:
        return self.data_root / "platforms.yaml"

    def policy_path(self) -> Path:
        return self.data_root / "policy_terms.yaml"

    def templates_path(self) -> Path:
        return self.data_root / "templates.yaml"

    def dump_all(self) -> dict[str, Any]:
        accounts_doc = self._accounts_doc()
        return {
            "accounts": [
                parse_account_card(raw).model_dump(mode="json")
                for raw in (accounts_doc.get("accounts") or {}).values()
                if isinstance(raw, dict)
            ],
            "guardrails": [
                parse_guardrail_card(raw, catalog_key=str(key)).model_dump(mode="json")
                for key, raw in (accounts_doc.get("guardrails") or {}).items()
                if isinstance(raw, dict)
            ],
            "platforms": [item.model_dump(mode="json") for item in self.list_platforms()],
            "policy": self.read_policy().as_dict(),
            "templates": [item.model_dump(mode="json") for item in self.list_templates()],
        }

    def create_account(self, card: AccountCard, *, index: int | None = None) -> AccountCard:
        with self._lock:
            doc = self._accounts_doc()
            accounts = dict(doc.get("accounts") or {})
            key = _require_key(card.account_key, field="account_key")
            if key in accounts:
                raise CatalogError(f"account already exists: {key}", status=409)
            parsed = parse_account_card(card.model_dump(mode="json"))
            resolve_guardrails(parsed.guardrail_keys, doc.get("guardrails") or {})
            doc["accounts"] = _insert_mapping(
                accounts, key, parsed.model_dump(mode="json"), index
            )
            self._write_yaml(self.accounts_path(), doc)
            return parsed

    def update_account(self, account_key: str, card: AccountCard) -> AccountCard:
        with self._lock:
            doc = self._accounts_doc()
            accounts = dict(doc.get("accounts") or {})
            key = _require_key(account_key, field="account_key")
            if key not in accounts:
                raise CatalogError(f"unknown account_key: {key}", status=404)
            if card.account_key != key:
                raise CatalogError("account_key cannot be renamed on update")
            parsed = parse_account_card(card.model_dump(mode="json"))
            resolve_guardrails(parsed.guardrail_keys, doc.get("guardrails") or {})
            accounts[key] = parsed.model_dump(mode="json")
            doc["accounts"] = accounts
            self._write_yaml(self.accounts_path(), doc)
            return parsed

    def delete_account(self, account_key: str) -> None:
        with self._lock:
            doc = self._accounts_doc()
            accounts = dict(doc.get("accounts") or {})
            key = _require_key(account_key, field="account_key")
            if key not in accounts:
                raise CatalogError(f"unknown account_key: {key}", status=404)
            if len(accounts) <= 1:
                raise CatalogError("cannot delete the last account", status=409)
            del accounts[key]
            doc["accounts"] = accounts
            self._write_yaml(self.accounts_path(), doc)

    def insert_account_guardrail(
        self,
        account_key: str,
        guardrail_key: str,
        *,
        index: int | None = None,
    ) -> AccountCard:
        with self._lock:
            doc = self._accounts_doc()
            accounts = dict(doc.get("accounts") or {})
            key = _require_key(account_key, field="account_key")
            pack = _require_key(guardrail_key, field="guardrail_key")
            raw = accounts.get(key)
            if not isinstance(raw, dict):
                raise CatalogError(f"unknown account_key: {key}", status=404)
            card = parse_account_card(raw)
            keys = list(card.guardrail_keys)
            if pack in keys:
                raise CatalogError(f"guardrail already attached: {pack}", status=409)
            if index is None or index >= len(keys):
                keys.append(pack)
            else:
                keys.insert(max(0, index), pack)
            updated = card.model_copy(update={"guardrail_keys": keys})
            resolve_guardrails(updated.guardrail_keys, doc.get("guardrails") or {})
            accounts[key] = updated.model_dump(mode="json")
            doc["accounts"] = accounts
            self._write_yaml(self.accounts_path(), doc)
            return updated

    def create_guardrail(
        self, card: GuardrailCard, *, index: int | None = None
    ) -> GuardrailCard:
        with self._lock:
            doc = self._accounts_doc()
            catalog = dict(doc.get("guardrails") or {})
            key = _require_key(card.guardrail_key, field="guardrail_key")
            if key in catalog:
                raise CatalogError(f"guardrail already exists: {key}", status=409)
            parsed = parse_guardrail_card(card.model_dump(mode="json"), catalog_key=key)
            doc["guardrails"] = _insert_mapping(
                catalog, key, parsed.model_dump(mode="json"), index
            )
            self._write_yaml(self.accounts_path(), doc)
            return parsed

    def update_guardrail(self, guardrail_key: str, card: GuardrailCard) -> GuardrailCard:
        with self._lock:
            doc = self._accounts_doc()
            catalog = dict(doc.get("guardrails") or {})
            key = _require_key(guardrail_key, field="guardrail_key")
            if key not in catalog:
                raise CatalogError(f"unknown guardrail_key: {key}", status=404)
            if card.guardrail_key != key:
                raise CatalogError("guardrail_key cannot be renamed on update")
            parsed = parse_guardrail_card(card.model_dump(mode="json"), catalog_key=key)
            catalog[key] = parsed.model_dump(mode="json")
            doc["guardrails"] = catalog
            self._write_yaml(self.accounts_path(), doc)
            return parsed

    def delete_guardrail(self, guardrail_key: str) -> None:
        with self._lock:
            doc = self._accounts_doc()
            catalog = dict(doc.get("guardrails") or {})
            key = _require_key(guardrail_key, field="guardrail_key")
            if key not in catalog:
                raise CatalogError(f"unknown guardrail_key: {key}", status=404)
            if len(catalog) <= 1:
                raise CatalogError("cannot delete the last guardrail", status=409)
            users = [
                str(item.get("account_key") or name)
                for name, item in (doc.get("accounts") or {}).items()
                if isinstance(item, dict) and key in (item.get("guardrail_keys") or [])
            ]
            if users:
                raise CatalogError(
                    f"guardrail is in use by: {', '.join(users)}", status=409
                )
            del catalog[key]
            doc["guardrails"] = catalog
            self._write_yaml(self.accounts_path(), doc)

    def list_platforms(self) -> list[PlatformCard]:
        doc = self._load(self.platforms_path())
        cards: list[PlatformCard] = []
        for key, raw in (doc.get("platforms") or {}).items():
            if not isinstance(raw, dict):
                continue
            cards.append(_parse_platform(raw, catalog_key=str(key)))
        return cards

    def create_platform(
        self, card: PlatformCard, *, index: int | None = None
    ) -> PlatformCard:
        with self._lock:
            doc = self._load(self.platforms_path())
            catalog = dict(doc.get("platforms") or {})
            key = _require_key(card.platform_key, field="platform_key")
            if key in catalog:
                raise CatalogError(f"platform already exists: {key}", status=409)
            parsed = _parse_platform(card.model_dump(mode="json"), catalog_key=key)
            doc["platforms"] = _insert_mapping(
                catalog, key, parsed.model_dump(mode="json"), index
            )
            self._write_yaml(self.platforms_path(), doc)
            return parsed

    def update_platform(self, platform_key: str, card: PlatformCard) -> PlatformCard:
        with self._lock:
            doc = self._load(self.platforms_path())
            catalog = dict(doc.get("platforms") or {})
            key = _require_key(platform_key, field="platform_key")
            if key not in catalog:
                raise CatalogError(f"unknown platform_key: {key}", status=404)
            if card.platform_key != key:
                raise CatalogError("platform_key cannot be renamed on update")
            parsed = _parse_platform(card.model_dump(mode="json"), catalog_key=key)
            catalog[key] = parsed.model_dump(mode="json")
            doc["platforms"] = catalog
            self._write_yaml(self.platforms_path(), doc)
            return parsed

    def delete_platform(self, platform_key: str) -> None:
        with self._lock:
            doc = self._load(self.platforms_path())
            catalog = dict(doc.get("platforms") or {})
            key = _require_key(platform_key, field="platform_key")
            if key not in catalog:
                raise CatalogError(f"unknown platform_key: {key}", status=404)
            if key == TWITTER_PLATFORM_KEY:
                raise CatalogError("cannot delete x-twitter", status=409)
            del catalog[key]
            doc["platforms"] = catalog
            self._write_yaml(self.platforms_path(), doc)

    def read_policy(self) -> PolicyDoc:
        return _parse_policy(self._load(self.policy_path()))

    def update_policy(self, policy: PolicyDoc) -> PolicyDoc:
        with self._lock:
            parsed = _parse_policy(policy.as_dict())
            self._write_yaml(self.policy_path(), parsed.as_dict())
            return parsed

    def insert_term(self, term: str, *, index: int | None = None) -> PolicyDoc:
        with self._lock:
            policy = _parse_policy(self._load(self.policy_path()))
            text = str(term or "").strip()
            if not text:
                raise CatalogError("term is required")
            terms = list(policy.terms)
            if text in terms:
                raise CatalogError(f"term already exists: {text}", status=409)
            if index is None or index >= len(terms):
                terms.append(text)
            else:
                terms.insert(max(0, index), text)
            updated = PolicyDoc(
                term_list_id=policy.term_list_id,
                disclaimer=policy.disclaimer,
                terms=terms,
            )
            self._write_yaml(self.policy_path(), updated.as_dict())
            return updated

    def delete_term(self, term: str) -> PolicyDoc:
        with self._lock:
            policy = _parse_policy(self._load(self.policy_path()))
            text = str(term or "").strip()
            if text not in policy.terms:
                raise CatalogError(f"unknown term: {text}", status=404)
            if len(policy.terms) <= 1:
                raise CatalogError("cannot delete the last policy term", status=409)
            updated = PolicyDoc(
                term_list_id=policy.term_list_id,
                disclaimer=policy.disclaimer,
                terms=[item for item in policy.terms if item != text],
            )
            self._write_yaml(self.policy_path(), updated.as_dict())
            return updated

    def list_templates(self) -> list[TemplateCard]:
        doc = self._load(self.templates_path())
        cards: list[TemplateCard] = []
        for raw in doc.get("templates") or []:
            if isinstance(raw, dict):
                cards.append(_parse_template(raw))
        return cards

    def create_template(
        self, card: TemplateCard, *, index: int | None = None
    ) -> TemplateCard:
        with self._lock:
            doc = self._load(self.templates_path())
            items = [
                _parse_template(raw).model_dump(mode="json")
                for raw in (doc.get("templates") or [])
                if isinstance(raw, dict)
            ]
            parsed = _parse_template(card.model_dump(mode="json"))
            if any(item.get("template_key") == parsed.template_key for item in items):
                raise CatalogError(
                    f"template already exists: {parsed.template_key}", status=409
                )
            payload = parsed.model_dump(mode="json")
            if index is None or index >= len(items):
                items.append(payload)
            else:
                items.insert(max(0, index), payload)
            doc["templates"] = items
            self._write_yaml(self.templates_path(), doc)
            return parsed

    def update_template(self, template_key: str, card: TemplateCard) -> TemplateCard:
        with self._lock:
            doc = self._load(self.templates_path())
            key = _require_key(template_key, field="template_key")
            if card.template_key != key:
                raise CatalogError("template_key cannot be renamed on update")
            parsed = _parse_template(card.model_dump(mode="json"))
            items = list(doc.get("templates") or [])
            found = False
            for offset, raw in enumerate(items):
                if isinstance(raw, dict) and str(raw.get("template_key") or "") == key:
                    items[offset] = parsed.model_dump(mode="json")
                    found = True
                    break
            if not found:
                raise CatalogError(f"unknown template_key: {key}", status=404)
            doc["templates"] = items
            self._write_yaml(self.templates_path(), doc)
            return parsed

    def delete_template(self, template_key: str) -> None:
        with self._lock:
            key = _require_key(template_key, field="template_key")
            accounts_doc = self._accounts_doc()
            users = [
                str(item.get("guardrail_key") or name)
                for name, item in (accounts_doc.get("guardrails") or {}).items()
                if isinstance(item, dict) and key in (item.get("template_keys") or [])
            ]
            if users:
                raise CatalogError(
                    f"template is in use by guardrails: {', '.join(users)}",
                    status=409,
                )
            doc = self._load(self.templates_path())
            items = [
                raw
                for raw in (doc.get("templates") or [])
                if not (isinstance(raw, dict) and str(raw.get("template_key") or "") == key)
            ]
            if len(items) == len(doc.get("templates") or []):
                raise CatalogError(f"unknown template_key: {key}", status=404)
            if not items:
                raise CatalogError("cannot delete the last template", status=409)
            doc["templates"] = items
            self._write_yaml(self.templates_path(), doc)

    def _accounts_doc(self) -> dict[str, Any]:
        doc = self._load(self.accounts_path())
        if not isinstance(doc, dict):
            raise CatalogError("accounts.yaml is invalid")
        return doc

    def _load(self, path: Path) -> dict[str, Any]:
        if not path.is_file():
            raise CatalogError(f"missing snapshot file: {path.name}", status=404)
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        if payload is None:
            return {}
        if not isinstance(payload, dict):
            raise CatalogError(f"invalid yaml: {path.name}")
        return payload

    def _write_yaml(self, path: Path, payload: dict[str, Any]) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            yaml.safe_dump(
                payload,
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
                width=88,
            ),
            encoding="utf-8",
        )
        tmp.replace(path)


def _require_key(value: str, *, field: str) -> str:
    key = str(value or "").strip()
    if not KEY_RE.match(key):
        raise CatalogError(f"invalid {field}")
    return key


def _insert_mapping(
    mapping: dict[str, Any],
    key: str,
    value: dict[str, Any],
    index: int | None,
) -> dict[str, Any]:
    items = list(mapping.items())
    if index is None or index >= len(items):
        items.append((key, value))
    else:
        items.insert(max(0, index), (key, value))
    return dict(items)


def _parse_platform(raw: dict[str, Any], *, catalog_key: str) -> PlatformCard:
    key = str(raw.get("platform_key") or catalog_key)
    if key != catalog_key:
        raise CatalogError(f"platform_key mismatch: {catalog_key}")
    return PlatformCard(
        platform_key=key,
        max_chars=int(raw["max_chars"]),
        max_posts=min(
            max(int(raw.get("max_posts") or MAX_COMPOSE_POSTS), MIN_COMPOSE_POSTS),
            MAX_COMPOSE_POSTS,
        ),
        mention_rules=str(raw.get("mention_rules") or ""),
    )


def _parse_template(raw: dict[str, Any]) -> TemplateCard:
    key = _require_key(str(raw.get("template_key") or ""), field="template_key")
    return TemplateCard(
        template_key=key,
        text=str(raw.get("text") or ""),
        claim_types=[str(item) for item in (raw.get("claim_types") or []) if str(item)],
    )


def _parse_policy(raw: dict[str, Any]) -> PolicyDoc:
    term_list_id = str(raw.get("term_list_id") or "").strip()
    terms = [str(item).strip() for item in (raw.get("terms") or []) if str(item).strip()]
    if not term_list_id or not terms:
        raise CatalogError("policy terms are required")
    return PolicyDoc(
        term_list_id=term_list_id,
        disclaimer=str(raw.get("disclaimer") or ""),
        terms=terms,
    )


def raise_as_catalog(exc: Exception) -> None:
    if isinstance(exc, CatalogError):
        raise exc
    if isinstance(exc, SnapshotError):
        raise CatalogError(str(exc), status=400) from exc
    raise exc
