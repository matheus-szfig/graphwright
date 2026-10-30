"""Delete nodes and incident edges in one operation, including incomplete drafts."""
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from builder_api.main import create_app

GRAPH = '''workflow: delete_demo
portal: test
start: start
nodes:
  start:
    recognize: {always: true}
    transitions: [{to: middle}]
    on_fail: middle
    actions: [{jump: {to: middle}}]
  middle:
    recognize: {always: true}
    transitions: [{to: done}]
  done: {terminal: success}
'''


@pytest.fixture
def client(tmp_path: Path):
    (tmp_path / 'delete_demo.yaml').write_text(GRAPH, encoding='utf-8')
    with TestClient(create_app(graphs_dir=tmp_path, headed=False)) as client:
        yield client


def test_delete_connected_node_and_incident_edges_as_one_draft(client):
    response = client.post('/graphs/delete_demo/delete-elements', json={
        'nodes': ['middle'], 'edges': [{'source': 'start', 'target': 'middle'},
                                     {'source': 'middle', 'target': 'done'}],
    })
    assert response.status_code == 200, response.text
    result = response.json()
    assert 'middle' not in result['workflow']['nodes']
    start = yaml.safe_load(result['yaml'])['nodes']['start']
    assert start['transitions'] == [] and start['actions'] == []
    assert 'on_fail' not in start
    assert result['lint']  # incomplete topology is saved for repair
    assert client.get('/graphs/delete_demo').json()['yaml'] == result['yaml']


def test_delete_multiple_nodes_and_last_success(client):
    response = client.post('/graphs/delete_demo/delete-elements', json={'nodes': ['middle', 'done'], 'edges': []})
    assert response.status_code == 200
    assert set(response.json()['workflow']['nodes']) == {'start'}
    assert response.json()['lint']


def test_rejected_selection_does_not_partially_delete(client):
    before = client.get('/graphs/delete_demo').json()['yaml']
    response = client.post('/graphs/delete_demo/delete-elements', json={
        'nodes': ['middle', 'start'], 'edges': [{'source': 'start', 'target': 'middle'}],
    })
    assert response.status_code == 400
    assert 'another node as start' in response.text
    assert client.get('/graphs/delete_demo').json()['yaml'] == before
    response = client.post('/graphs/delete_demo/delete-elements', json={
        'nodes': ['middle'], 'edges': [{'source': 'missing', 'target': 'done'}],
    })
    assert response.status_code == 404
    assert client.get('/graphs/delete_demo').json()['yaml'] == before


def test_single_node_endpoint_uses_same_cleanup(client):
    response = client.delete('/graphs/delete_demo/nodes/middle')
    assert response.status_code == 200
    assert 'on_fail' not in yaml.safe_load(response.json()['yaml'])['nodes']['start']


def test_edge_only_selection_keeps_nodes(client):
    response = client.post('/graphs/delete_demo/delete-elements', json={
        'nodes': [], 'edges': [{'source': 'middle', 'target': 'done'}],
    })
    assert response.status_code == 200
    assert len(response.json()['workflow']['nodes']) == 3
    assert yaml.safe_load(response.json()['yaml'])['nodes']['middle']['transitions'] == []
