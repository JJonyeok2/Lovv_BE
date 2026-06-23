import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from admin.proposals_repository import RdsDataAdminProposalRepository


class FakeSqlClient:
    def __init__(self, fetch_one_rows=None, fetch_all_rows=None):
        self.fetch_one_rows = list(fetch_one_rows or [])
        self.fetch_all_rows = list(fetch_all_rows or [])
        self.executed = []
        self.fetch_one_calls = []
        self.fetch_all_calls = []

    def execute(self, sql, parameters=None, include_result_metadata=True):
        self.executed.append(
            {
                "sql": " ".join(sql.split()),
                "parameters": parameters or {},
                "include_result_metadata": include_result_metadata,
            }
        )
        return {"numberOfRecordsUpdated": 1}

    def fetch_one(self, sql, parameters=None):
        self.fetch_one_calls.append({"sql": " ".join(sql.split()), "parameters": parameters or {}})
        return self.fetch_one_rows.pop(0) if self.fetch_one_rows else None

    def fetch_all(self, sql, parameters=None):
        self.fetch_all_calls.append({"sql": " ".join(sql.split()), "parameters": parameters or {}})
        return self.fetch_all_rows.pop(0) if self.fetch_all_rows else []


def principal(**overrides):
    data = {
        "userId": "provider-1",
        "roles": ["R-DATA-PROVIDER"],
        "organizationIds": ["org-gangneung"],
        "regionIds": ["KR-42-150"],
    }
    data.update(overrides)
    return data


def payload(**overrides):
    data = {
        "contentType": "festival",
        "regionId": "KR-42-150",
        "cityId": "gangneung",
        "cityName": "강릉",
        "title": "강릉 커피축제",
        "payload": {"festivalName": "강릉 커피축제"},
    }
    data.update(overrides)
    return data


class AdminProposalRepositoryTest(unittest.TestCase):
    def test_defaults_to_admin_console_table_names(self):
        repository = RdsDataAdminProposalRepository(rds_client=FakeSqlClient())

        self.assertEqual(repository.proposals_table, "admin_data_proposals")
        self.assertEqual(repository.history_table, "admin_data_proposal_history")

    def test_create_writes_proposal_and_submitted_history(self):
        client = FakeSqlClient()
        repository = RdsDataAdminProposalRepository(rds_client=client)

        proposal = repository.create(principal(), payload(), "2026-06-23T09:00:00Z")

        sql_statements = [call["sql"] for call in client.executed]
        insert_params = client.executed[0]["parameters"]
        history_params = client.executed[1]["parameters"]

        self.assertIn("INSERT INTO admin_data_proposals", sql_statements[0])
        self.assertIn("INSERT INTO admin_data_proposal_history", sql_statements[1])
        self.assertEqual(proposal["createdBy"], "provider-1")
        self.assertEqual(proposal["organizationId"], "org-gangneung")
        self.assertEqual(proposal["status"], "submitted")
        self.assertEqual(insert_params["created_by"], "provider-1")
        self.assertEqual(insert_params["organization_id"], "org-gangneung")
        self.assertEqual(insert_params["payload_json"], "{\"festivalName\":\"강릉 커피축제\"}")
        self.assertEqual(history_params["action"], "submitted")
        self.assertEqual(history_params["actor_user_id"], "provider-1")

    def test_list_for_provider_scopes_by_creator_or_organization(self):
        client = FakeSqlClient(fetch_all_rows=[[]])
        repository = RdsDataAdminProposalRepository(rds_client=client)

        repository.list_for_provider("provider-1", organization_ids=["org-1", "org-2"], limit=10)

        call = client.fetch_all_calls[0]
        self.assertIn("created_by = :user_id", call["sql"])
        self.assertIn("organization_id IN (:organization_id_0, :organization_id_1)", call["sql"])
        self.assertEqual(call["parameters"]["user_id"], "provider-1")
        self.assertEqual(call["parameters"]["organization_id_0"], "org-1")
        self.assertEqual(call["parameters"]["organization_id_1"], "org-2")
        self.assertEqual(call["parameters"]["limit"], 10)

    def test_list_for_regions_scopes_by_assigned_regions(self):
        client = FakeSqlClient(fetch_all_rows=[[]])
        repository = RdsDataAdminProposalRepository(rds_client=client)

        repository.list_for_regions(["KR-42-150", "KR-47-170"], limit=10)

        call = client.fetch_all_calls[0]
        self.assertIn("region_id IN (:region_id_0, :region_id_1)", call["sql"])
        self.assertEqual(call["parameters"]["region_id_0"], "KR-42-150")
        self.assertEqual(call["parameters"]["region_id_1"], "KR-47-170")
        self.assertEqual(call["parameters"]["limit"], 10)

    def test_list_for_regions_without_regions_does_not_query(self):
        client = FakeSqlClient()
        repository = RdsDataAdminProposalRepository(rds_client=client)

        proposals = repository.list_for_regions([], limit=10)

        self.assertEqual(proposals, [])
        self.assertEqual(client.fetch_all_calls, [])

    def test_get_visible_hides_other_provider_proposal(self):
        client = FakeSqlClient(
            fetch_one_rows=[
                {
                    "id": "proposal-1",
                    "proposal_code": "PROP-000001",
                    "content_type": "festival",
                    "region_id": "KR-42-150",
                    "title": "다른 기관 제안",
                    "payload_json": "{}",
                    "service_boundary_json": "{}",
                    "gateway_city_json": "{}",
                    "status": "submitted",
                    "created_by": "provider-2",
                    "organization_id": "org-other",
                    "submitted_at": "2026-06-23T09:00:00Z",
                    "created_at": "2026-06-23T09:00:00Z",
                    "updated_at": "2026-06-23T09:00:00Z",
                }
            ]
        )
        repository = RdsDataAdminProposalRepository(rds_client=client)

        proposal = repository.get_visible("proposal-1", principal(userId="provider-1", organizationIds=["org-gangneung"]))

        self.assertIsNone(proposal)


if __name__ == "__main__":
    unittest.main()
