from types import SimpleNamespace
from evaluation.langsmith_resources import link_agent_resources


class Client:
    def __init__(self, pages):
        self.pages = pages
        self.tags = {}
        self.created = []

    def request_with_retries(self, method, path, params=None, json=None):
        if path == "/api/v1/agents":
            value = self.pages[int((params or {}).get("cursor", 0))]
        elif method == "POST":
            self.created.append(json)
            self.tags[(json["resource_type"], json["resource_id"])] = [{"values": [{"id": json["tag_value_id"]}]}]
            value = {}
        else:
            value = self.tags.get((params["resource_type"], params["resource_id"]), [])
        return SimpleNamespace(json=lambda: value)


def agent(name="Different display name"):
    return {"id": "research", "name": name, "tag_value_id": "tag-123",
            "environments": [{"tracer_session_name": "research-assistant"}]}


def test_links_dataset_and_experiment_by_environment_and_is_idempotent():
    client = Client([{"items": [agent()], "next_cursor": None}])
    result = link_agent_resources(client, "research-assistant", "dataset-1", "experiment-1")
    assert result["agent_id"] == "research" and result["status"] == "linked"
    assert {(r["resource_type"], r["resource_id"]) for r in client.created} == {
        ("dataset", "dataset-1"), ("experiment", "experiment-1")}
    link_agent_resources(client, "research-assistant", "dataset-1", "experiment-1")
    assert len(client.created) == 2


def test_agent_on_later_page_is_found():
    client = Client([{"items": [], "next_cursor": "1"}, {"items": [agent()], "next_cursor": None}])
    assert link_agent_resources(client, "research-assistant", "d", "e")["status"] == "linked"


def test_project_without_agent_does_not_link_other_agents_resources():
    client = Client([{"items": [agent()], "next_cursor": None}])
    assert link_agent_resources(client, "another-project", "d", "e")["status"] == "no_agent"
    assert client.created == []
