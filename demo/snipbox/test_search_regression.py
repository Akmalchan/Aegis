"""Regression test for GET /snippets/search (AEGIS finding: aegis.sql-string-concat).

A quote in the query must not reach the SQL text. On the vulnerable build this
request raises sqlite3.OperationalError (unterminated string) and the API answers 500;
on the parameterized build it answers 200 with the matching snippet.
"""

from fastapi.testclient import TestClient

from snipbox.api import create_app


def test_search_handles_quote_in_query(tmp_path):
    client = TestClient(create_app(tmp_path / "test.db"), raise_server_exceptions=False)
    created = client.post(
        "/snippets",
        json={"title": "O'Reilly notes", "content": "chapter 3", "tags": []},
    )
    assert created.status_code == 201, created.text

    resp = client.get("/snippets/search", params={"q": "O'"})
    assert resp.status_code == 200, resp.text
    assert [s["title"] for s in resp.json()] == ["O'Reilly notes"]


def test_search_quote_cannot_widen_results(tmp_path):
    client = TestClient(create_app(tmp_path / "test.db"), raise_server_exceptions=False)
    client.post("/snippets", json={"title": "public.py", "content": "x", "tags": []})
    client.post("/snippets", json={"title": "secret.py", "content": "y", "tags": []})

    # classic tautology; must match nothing, not everything
    resp = client.get("/snippets/search", params={"q": "zzz' OR 'a%'='a"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == []
