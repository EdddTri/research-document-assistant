"""Associate evaluation resources with the AI agent owning the application project."""


def link_agent_resources(client, project_name, dataset_id, experiment_id):
    # LANGSMITH_PROJECT routes traces; it does not attach datasets to an AI agent.
    # Resolve the owner through environment bindings instead of guessing by name.
    agents = []
    cursor = None
    while True:
        params = {"page_size": 100}
        if cursor:
            params["cursor"] = cursor
        page = client.request_with_retries("GET", "/api/v1/agents", params=params).json()
        agents.extend(agent for agent in page["items"] if any(
            environment["tracer_session_name"] == project_name for environment in agent["environments"]))
        cursor = page.get("next_cursor")
        if not cursor:
            break
    if not agents:
        return {"status": "no_agent", "project": project_name}
    if len(agents) != 1 or not agents[0].get("tag_value_id"):
        raise ValueError("The application project must belong to one AI agent with a resource tag")
    agent = agents[0]
    tag_id = agent["tag_value_id"]
    for resource_type, resource_id in (("dataset", dataset_id), ("experiment", experiment_id)):
        resource = {"resource_type": resource_type, "resource_id": str(resource_id)}
        existing = client.request_with_retries("GET", "/workspaces/current/tags/resource", params=resource).json()
        if not any(value["id"] == tag_id for key in existing for value in key.get("values", [])):
            client.request_with_retries("POST", "/workspaces/current/taggings",
                                        json={**resource, "tag_value_id": tag_id})
        confirmed = client.request_with_retries("GET", "/workspaces/current/tags/resource", params=resource).json()
        if not any(value["id"] == tag_id for key in confirmed for value in key.get("values", [])):
            raise RuntimeError(f"LangSmith did not confirm the {resource_type} association")
    return {"status": "linked", "agent_id": agent["id"], "agent_name": agent["name"], "tag_value_id": tag_id}
