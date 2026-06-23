import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from admin.proposals_repository import ProposalTransitionError, RdsDataAdminProposalRepository


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


def proposal_row(**overrides):
    data = {
        "id": "proposal-1",
        "proposal_code": "PROP-000001",
        "content_type": "festival",
        "region_id": "KR-42-150",
        "city_id": "gangneung",
        "city_name": "강릉",
        "title": "강릉 커피축제",
        "description": None,
        "official_source_name": "city office",
        "official_source_url": "https://example.com",
        "source_updated_at": "2026-06-20T00:00:00Z",
        "evidence_text": "official notice",
        "payload_json": "{\"festivalName\":\"강릉 커피축제\"}",
        "service_boundary_json": "{}",
        "gateway_city_json": "{}",
        "status": "in_review",
        "created_by": "provider-1",
        "organization_id": "org-gangneung",
        "submitted_at": "2026-06-23T09:00:00Z",
        "reviewed_by": "admin-1",
        "reviewed_at": "2026-06-23T09:10:00Z",
        "review_note": "checking",
        "approved_content_hash": None,
        "created_at": "2026-06-23T09:00:00Z",
        "updated_at": "2026-06-23T09:10:00Z",
        "deleted_at": None,
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

    def test_transition_to_approved_updates_proposal_and_appends_history(self):
        client = FakeSqlClient(fetch_one_rows=[proposal_row(status="in_review")])
        repository = RdsDataAdminProposalRepository(rds_client=client)

        proposal = repository.transition(
            "proposal-1",
            "approved",
            principal(userId="admin-1", roles=["R-ADMIN"]),
            "2026-06-23T09:30:00Z",
            note="approved",
        )

        update_call = client.executed[0]
        history_call = client.executed[1]

        self.assertEqual(proposal["status"], "approved")
        self.assertEqual(proposal["reviewedBy"], "admin-1")
        self.assertEqual(proposal["reviewNote"], "approved")
        self.assertEqual(len(proposal["approvedContentHash"]), 64)
        self.assertIn("UPDATE admin_data_proposals SET status = :status", update_call["sql"])
        self.assertEqual(update_call["parameters"]["from_status"], "in_review")
        self.assertEqual(update_call["parameters"]["status"], "approved")
        self.assertEqual(update_call["parameters"]["reviewed_by"], "admin-1")
        self.assertEqual(update_call["parameters"]["review_note"], "approved")
        self.assertEqual(len(update_call["parameters"]["approved_content_hash"]), 64)
        self.assertIn("INSERT INTO admin_data_proposal_history", history_call["sql"])
        self.assertEqual(history_call["parameters"]["action"], "approved")
        self.assertEqual(history_call["parameters"]["from_status"], "in_review")
        self.assertEqual(history_call["parameters"]["to_status"], "approved")
        self.assertEqual(history_call["parameters"]["note"], "approved")

    def test_transition_rejects_invalid_state(self):
        client = FakeSqlClient(fetch_one_rows=[proposal_row(status="submitted")])
        repository = RdsDataAdminProposalRepository(rds_client=client)

        with self.assertRaises(ProposalTransitionError) as context:
            repository.transition(
                "proposal-1",
                "approved",
                principal(userId="admin-1", roles=["R-ADMIN"]),
                "2026-06-23T09:30:00Z",
            )

        self.assertEqual(context.exception.code, "INVALID_PROPOSAL_STATE")
        self.assertEqual(client.executed, [])

    def test_transition_rejects_self_review(self):
        client = FakeSqlClient(fetch_one_rows=[proposal_row(created_by="admin-1", status="submitted")])
        repository = RdsDataAdminProposalRepository(rds_client=client)

        with self.assertRaises(ProposalTransitionError) as context:
            repository.transition(
                "proposal-1",
                "in_review",
                principal(userId="admin-1", roles=["R-ADMIN"]),
                "2026-06-23T09:30:00Z",
            )

        self.assertEqual(context.exception.code, "SELF_REVIEW_FORBIDDEN")
        self.assertEqual(client.executed, [])

    def test_list_history_visible_queries_history_after_visibility_check(self):
        client = FakeSqlClient(
            fetch_one_rows=[proposal_row(status="approved")],
            fetch_all_rows=[
                [
                    {
                        "id": "history-1",
                        "proposal_id": "proposal-1",
                        "action": "approved",
                        "from_status": "in_review",
                        "to_status": "approved",
                        "actor_user_id": "admin-1",
                        "actor_roles_json": "[\"R-ADMIN\"]",
                        "note": "approved",
                        "metadata_json": "{\"proposalCode\":\"PROP-000001\"}",
                        "created_at": "2026-06-23T09:30:00Z",
                    }
                ]
            ],
        )
        repository = RdsDataAdminProposalRepository(rds_client=client)

        history = repository.list_history_visible("proposal-1", principal(userId="provider-1"), limit=20)

        self.assertEqual(history[0]["action"], "approved")
        self.assertEqual(history[0]["actorRoles"], ["R-ADMIN"])
        self.assertEqual(history[0]["metadata"]["proposalCode"], "PROP-000001")
        self.assertIn("FROM admin_data_proposal_history", client.fetch_all_calls[0]["sql"])
        self.assertEqual(client.fetch_all_calls[0]["parameters"]["proposal_id"], "proposal-1")
        self.assertEqual(client.fetch_all_calls[0]["parameters"]["limit"], 20)

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
