"""Persist view defaults without losing drafts or changing execution targets."""
import pytest
import yaml
from fastapi.testclient import TestClient
from graphwright.engine.schema import PageFilter, Workflow

from builder_api.graphs import target_from_element
from builder_api.main import create_app


@pytest.fixture
def client(tmp_path):
    (tmp_path / "demo.yaml").write_text("""workflow: demo
portal: test
start: done
nodes:
  done: {terminal: success}
""", encoding="utf-8")
    with TestClient(create_app(graphs_dir=tmp_path, headed=False)) as client:
        yield client


def test_filter_roundtrip_stale_save_and_schema(client):
    base = "/graphs/demo"
    defaults = PageFilter().model_dump()
    value = PageFilter(categories=["text", "tables"], frame=1, query="Case", visibility="all").model_dump()
    response = client.put(base + "/page-filter", json={"value": value, "expected": defaults})
    assert response.status_code == 200
    view = response.json()
    assert view["workflow"]["page_filter"] == value and not view["lint"]
    assert Workflow.from_yaml_text(view["yaml"]).page_filter == PageFilter(**value)
    assert client.put(base + "/page-filter", json={"value": defaults, "expected": defaults}).status_code == 409
    assert client.put(base + "/page-filter", json={"value": {"categories": ["typo"]}, "expected": value}).status_code == 422
    assert "page_filter" in client.get("/schema").json()["workflow"]["properties"]


def test_filters_preserve_invalid_node_drafts(client):
    base = "/graphs/demo"
    document = yaml.safe_load(client.get(base).json()["yaml"])
    document["nodes"]["done"] = {"actions": [{"fill": {"target": "missing"}}]}
    client.put(base, json={"yaml": yaml.safe_dump(document)})
    response = client.put(base + "/page-filter", json={"value": {"categories": []}, "expected": {}})
    assert response.status_code == 200 and response.json()["lint"]
    assert yaml.safe_load(response.json()["yaml"])["nodes"] == document["nodes"]


def test_new_target_keeps_frame_and_structural_fallback():
    _, target = target_from_element({"id": "f2e1", "frame": 2, "role": "generic", "name": "",
                                      "css_path": "#host >>> div:nth-of-type(2)"})
    assert target.frame == 2
    assert target.strategies[0].css == "#host >>> div:nth-of-type(2)"
