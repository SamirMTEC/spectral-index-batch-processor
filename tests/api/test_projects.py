"""Authorization and validation coverage for the project and dataset APIs."""

from uuid import UUID

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

FIRST_USER = "10000000-0000-0000-0000-000000000001"
SECOND_USER = "20000000-0000-0000-0000-000000000002"


def request_as(app: FastAPI, user_id: str) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Dev-User-Id": user_id},
    )


async def test_project_crud_and_archive(domain_app: FastAPI) -> None:
    async with request_as(domain_app, FIRST_USER) as client:
        invalid = await client.post("/api/v1/projects", json={"name": ""})
        assert invalid.status_code == 422

        created = await client.post(
            "/api/v1/projects", json={"name": "Field trial", "description": "2026 campaign"}
        )
        assert created.status_code == 201
        project = created.json()
        assert UUID(project["id"])
        assert project["status"] == "active"
        assert project["created_at"]
        assert project["updated_at"]

        updated = await client.patch(
            f"/api/v1/projects/{project['id']}", json={"name": "Updated field trial"}
        )
        assert updated.status_code == 200
        assert updated.json()["name"] == "Updated field trial"

        archived = await client.post(f"/api/v1/projects/{project['id']}/archive")
        assert archived.status_code == 200
        assert archived.json()["status"] == "archived"
        assert archived.json()["archived_at"]

        readonly = await client.patch(f"/api/v1/projects/{project['id']}", json={"name": "Nope"})
        assert readonly.status_code == 409


async def test_project_and_dataset_are_isolated_by_owner(domain_app: FastAPI) -> None:
    async with request_as(domain_app, FIRST_USER) as first:
        created_project = await first.post("/api/v1/projects", json={"name": "Owner one"})
        project_id = created_project.json()["id"]
        created_dataset = await first.post(
            f"/api/v1/projects/{project_id}/datasets",
            json={
                "name": "Canopy capture",
                "crop": "maize",
                "radiometry_mode": "reflectance",
            },
        )
        assert created_dataset.status_code == 201
        dataset_id = created_dataset.json()["id"]

    async with request_as(domain_app, SECOND_USER) as second:
        assert (await second.get("/api/v1/projects")).json() == []
        assert (await second.get(f"/api/v1/projects/{project_id}")).status_code == 404
        assert (
            await second.patch(f"/api/v1/projects/{project_id}", json={"name": "Stolen"})
        ).status_code == 404
        assert (await second.post(f"/api/v1/projects/{project_id}/archive")).status_code == 404
        assert (await second.get(f"/api/v1/projects/{project_id}/datasets")).status_code == 404
        assert (await second.get(f"/api/v1/datasets/{dataset_id}")).status_code == 404
        assert (await second.delete(f"/api/v1/datasets/{dataset_id}")).status_code == 404


async def test_dataset_lifecycle_validation_and_deletion(domain_app: FastAPI) -> None:
    async with request_as(domain_app, FIRST_USER) as client:
        project = (await client.post("/api/v1/projects", json={"name": "Dataset project"})).json()
        invalid = await client.post(
            f"/api/v1/projects/{project['id']}/datasets",
            json={"name": "Capture", "radiometry_mode": "not-a-mode"},
        )
        assert invalid.status_code == 422

        created = await client.post(
            f"/api/v1/projects/{project['id']}/datasets",
            json={
                "name": "Capture",
                "description": "Leaf measurements",
                "crop": "soybean",
                "experiment": "E-01",
                "location": "Bahia",
                "sensor": "MicaSense",
                "radiometry_mode": "digital_number",
                "notes": "Raw import pending",
            },
        )
        assert created.status_code == 201
        dataset = created.json()
        assert dataset["state"] == "draft"
        assert dataset["radiometry_mode"] == "digital_number"

        opened = await client.get(f"/api/v1/datasets/{dataset['id']}")
        assert opened.status_code == 200
        assert opened.json()["location"] == "Bahia"

        deleted = await client.delete(f"/api/v1/datasets/{dataset['id']}")
        assert deleted.status_code == 204
        assert (await client.get(f"/api/v1/datasets/{dataset['id']}")).status_code == 404
