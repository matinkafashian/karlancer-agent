import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
WORKFLOW_DIR = ROOT / "workflows" / "n8n"


@pytest.mark.parametrize(
    ("filename", "request_name", "endpoint"),
    (
        ("ingest.json", "Ingest channel projects", "/v1/jobs/ingest"),
        ("shadow.json", "Run shadow analysis", "/v1/jobs/shadow"),
        ("reply-watch.json", "Check first employer replies", "/v1/jobs/reply-watch"),
    ),
)
def test_scheduled_workflow_is_private_inactive_and_surfaces_failures(
    filename: str,
    request_name: str,
    endpoint: str,
) -> None:
    workflow = json.loads((WORKFLOW_DIR / filename).read_text(encoding="utf-8"))
    nodes = {node["name"]: node for node in workflow["nodes"]}
    request = nodes[request_name]
    headers = request["parameters"]["headerParameters"]["parameters"]
    serialized = json.dumps(workflow)

    assert len(workflow["id"]) == 16
    assert workflow["active"] is False
    assert any(node["type"] == "n8n-nodes-base.scheduleTrigger" for node in nodes.values())
    assert request["parameters"]["url"] == f"http://worker:8000{endpoint}"
    assert {header["name"] for header in headers} >= {"X-Internal-API-Key"}
    assert "$env.KARLANCER_INTERNAL_API_KEY" in serialized
    assert request["parameters"]["options"]["timeout"] <= 90_000
    assert request["onError"] == "continueErrorOutput"
    assert request_name in workflow["connections"]
    assert len(workflow["connections"][request_name]["main"]) == 2
    assert workflow["connections"][request_name]["main"][1][0]["node"].startswith(
        "Surface "
    )
    assert "replace-with" not in serialized
    assert "/submit" not in serialized


@pytest.mark.parametrize("filename", ("ingest.json", "shadow.json"))
def test_mutating_read_only_jobs_have_per_run_idempotency_keys(filename: str) -> None:
    workflow = json.loads((WORKFLOW_DIR / filename).read_text(encoding="utf-8"))
    request = next(
        node for node in workflow["nodes"] if node["type"] == "n8n-nodes-base.httpRequest"
    )
    headers = request["parameters"]["headerParameters"]["parameters"]
    idempotency = next(header for header in headers if header["name"] == "Idempotency-Key")

    assert "$execution.id" in idempotency["value"]


def test_auto_workflow_checks_due_window_every_minute() -> None:
    workflow = json.loads((WORKFLOW_DIR / "auto.json").read_text(encoding="utf-8"))
    nodes = {node["name"]: node for node in workflow["nodes"]}
    schedule = nodes["Check submission window"]
    request = nodes["Run automatic cycle"]
    headers = request["parameters"]["headerParameters"]["parameters"]

    interval = schedule["parameters"]["rule"]["interval"][0]
    assert interval == {"field": "minutes", "minutesInterval": 1}
    assert workflow["active"] is False
    assert request["parameters"]["url"] == "http://worker:8000/v1/jobs/auto"
    assert request["parameters"]["options"]["timeout"] == 900_000
    assert any(
        header["name"] == "Idempotency-Key"
        and "karlancer-auto-" in header["value"]
        and "$execution.id" in header["value"]
        for header in headers
    )
    assert request["onError"] == "continueErrorOutput"
    error_target = workflow["connections"]["Run automatic cycle"]["main"][1][0]
    assert error_target["node"] == "Report automatic cycle failure"
    assert nodes[error_target["node"]]["parameters"]["url"].endswith(
        "/v1/alerts/operational"
    )


def test_ingestion_has_independent_thirty_minute_schedule() -> None:
    workflow = json.loads((WORKFLOW_DIR / "ingest.json").read_text(encoding="utf-8"))
    schedule = next(node for node in workflow["nodes"]
                    if node["type"] == "n8n-nodes-base.scheduleTrigger")
    assert schedule["parameters"]["rule"]["interval"] == [
        {"field": "minutes", "minutesInterval": 30}
    ]
    assert schedule["name"] in workflow["connections"]
