from __future__ import annotations

import pytest

from kiln_mcp.auth.store import InMemoryOAuthStore


@pytest.fixture
def store() -> InMemoryOAuthStore:
    return InMemoryOAuthStore()


def test_store_and_retrieve_client(store: InMemoryOAuthStore) -> None:
    store.save_client("client-1", {"client_id": "client-1", "name": "test"})
    assert store.get_client("client-1") == {"client_id": "client-1", "name": "test"}


def test_get_missing_client_returns_none(store: InMemoryOAuthStore) -> None:
    assert store.get_client("missing") is None


def test_delete_client(store: InMemoryOAuthStore) -> None:
    store.save_client("c1", {"client_id": "c1"})
    store.delete_client("c1")
    assert store.get_client("c1") is None


def test_store_and_retrieve_auth_code(store: InMemoryOAuthStore) -> None:
    data = {"code": "abc", "client_id": "c1", "user_id": "u1"}
    store.save_auth_code("abc", data, ttl=60)
    assert store.get_auth_code("abc") == data


def test_auth_code_expires(store: InMemoryOAuthStore) -> None:
    data = {"code": "abc"}
    store.save_auth_code("abc", data, ttl=-1)  # already expired
    assert store.get_auth_code("abc") is None


def test_delete_auth_code(store: InMemoryOAuthStore) -> None:
    store.save_auth_code("abc", {"code": "abc"}, ttl=60)
    store.delete_auth_code("abc")
    assert store.get_auth_code("abc") is None


def test_store_and_retrieve_access_token(store: InMemoryOAuthStore) -> None:
    data = {"token": "tok", "user_id": "u1", "client_id": "c1", "scopes": ["kiln:tools"]}
    store.save_access_token("tok", data, ttl=3600)
    assert store.get_access_token("tok") == data


def test_access_token_expires(store: InMemoryOAuthStore) -> None:
    store.save_access_token("tok", {"token": "tok"}, ttl=-1)
    assert store.get_access_token("tok") is None


def test_delete_access_token(store: InMemoryOAuthStore) -> None:
    store.save_access_token("tok", {"token": "tok"}, ttl=3600)
    store.delete_access_token("tok")
    assert store.get_access_token("tok") is None


def test_store_and_retrieve_refresh_token(store: InMemoryOAuthStore) -> None:
    data = {"token": "ref", "user_id": "u1"}
    store.save_refresh_token("ref", data, ttl=86400)
    assert store.get_refresh_token("ref") == data


def test_refresh_token_expires(store: InMemoryOAuthStore) -> None:
    store.save_refresh_token("ref", {"token": "ref"}, ttl=-1)
    assert store.get_refresh_token("ref") is None


def test_delete_refresh_token(store: InMemoryOAuthStore) -> None:
    store.save_refresh_token("ref", {"token": "ref"}, ttl=86400)
    store.delete_refresh_token("ref")
    assert store.get_refresh_token("ref") is None


def test_cleanup_removes_expired_entries(store: InMemoryOAuthStore) -> None:
    store.save_auth_code("exp", {"code": "exp"}, ttl=-1)
    store.save_access_token("exp", {"token": "exp"}, ttl=-1)
    store.save_refresh_token("exp", {"token": "exp"}, ttl=-1)
    store.save_auth_code("live", {"code": "live"}, ttl=3600)
    store.cleanup()
    assert store.get_auth_code("exp") is None
    assert store.get_access_token("exp") is None
    assert store.get_refresh_token("exp") is None
    assert store.get_auth_code("live") == {"code": "live"}
