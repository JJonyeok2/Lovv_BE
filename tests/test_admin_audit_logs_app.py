import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from admin.app import handle_request
from admin.proposals_repository import InMemoryAdminProposalRepository
from admin.monthly_destinations_repository import InMemoryMonthlyDestinationRepository
from admin.publish_jobs_repository import InMemoryPublishJobRepository
from admin.operations_repository import InMemoryAdminOperationsRepository
from admin.audit_logs_repository import InMemoryAuditLogRepository


MONTHLY = "/api/v1/admin/monthly-destinations"
AUDIT = "/api/v1/admin/audit-logs"
NOTICES = "/api/v1/admin/notices"


def make_event(method, path, body=None, authorizer_context=None, query=None):
    event = {
        "rawPath": path,
        "headers": {"content-type": "application/json"},
        "queryStringParameters": query,
        "requestContext": {"http": {"method": method}},
    }
    if authorizer_context is not None:
        event["requestContext"]["authorizer"] = {"lambda": authorizer_context}
    if body is not None:
        event["body"] = json.dumps(body)
    return event


def admin_context(user_id="admin-1"):
    return {"userId": user_id, "roles": "R-ADMIN", "sessionId": "sess-1"}


def data_provider_context(user_id="provider-1"):
    return {"userId": user_id, "roles": "R-DATA-PROVIDER", "organization_ids": "org-1"}


class AuditLogApiTests(unittest.TestCase):
    def setUp(self):
        self.proposals = InMemoryAdminProposalRepository()
        self.monthly = InMemoryMonthlyDestinationRepository()
        self.jobs = InMemoryPublishJobRepository()
        self.operations = InMemoryAdminOperationsRepository()
        self.audit = InMemoryAuditLogRepository()

    def _call(self, method, path, body=None, context=None, query=None):
        return handle_request(
            make_event(method, path, body=body, authorizer_context=context, query=query),
            proposal_repository=self.proposals,
            monthly_repository=self.monthly,
            publish_jobs_repository=self.jobs,
            operations_repository=self.operations,
            audit_repository=self.audit,
        )

    def _seed_approved(self):
        provider = {"userId": "provider-1", "roles": ["R-DATA-PROVIDER"], "organizationIds": ["org-1"]}
        admin = {"userId": "admin-1", "roles": ["R-ADMIN"]}
        created = self.proposals.create(provider, {"contentType": "festival", "regionId": "KR-42-150", "cityName": "강릉", "title": "강릉 커피축제"})
        self.proposals.transition(created["proposalId"], "in_review", admin)
        return created["proposalId"]

    def test_proposal_approval_is_audited(self):
        proposal_id = self._seed_approved()
        self._call("POST", f"/api/v1/admin/data-proposals/{proposal_id}/approve", body={}, context=admin_context())
        actions = [entry["action"] for entry in self.audit.entries]
        self.assertIn("data_proposal.approve", actions)
        entry = next(e for e in self.audit.entries if e["action"] == "data_proposal.approve")
        self.assertEqual(entry["resourceType"], "data_proposal")
        self.assertEqual(entry["resourceId"], proposal_id)
        self.assertEqual(entry["result"], "succeeded")
        self.assertEqual(entry["actorUserId"], "admin-1")

    def test_promote_is_audited(self):
        proposal_id = self._seed_approved()
        self._call("POST", f"/api/v1/admin/data-proposals/{proposal_id}/approve", body={}, context=admin_context())
        approved = self.proposals.get_visible(proposal_id, {"userId": "admin-1", "roles": ["R-ADMIN"]})
        self._call("POST", MONTHLY, body={
            "sourceProposalId": approved["proposalId"],
            "curationMonth": "2026-10",
            "themeCodes": ["coffee"],
            "regionId": "KR-42-150",
        }, context=admin_context())
        entry = next((e for e in self.audit.entries if e["action"] == "monthly_destination.promote"), None)
        self.assertIsNotNone(entry)
        self.assertEqual(entry["resourceType"], "monthly_destination")
        self.assertEqual(entry["metadata"].get("sourceProposalId"), approved["proposalId"])

    def test_publish_is_audited_with_reflection_count(self):
        proposal_id = self._seed_approved()
        self._call("POST", f"/api/v1/admin/data-proposals/{proposal_id}/approve", body={}, context=admin_context())
        approved = self.proposals.get_visible(proposal_id, {"userId": "admin-1", "roles": ["R-ADMIN"]})
        destination = self.monthly.create({"userId": "admin-1", "roles": ["R-ADMIN"]}, {
            "sourceProposalId": approved["proposalId"],
            "curationMonth": "2026-10",
            "themeCodes": ["coffee"],
            "regionId": "KR-42-150",
        })
        self._call("POST", f"{MONTHLY}/{destination['id']}/publish", body={}, context=admin_context())
        entry = next(e for e in self.audit.entries if e["action"] == "monthly_destination.publish")
        self.assertEqual(entry["metadata"].get("reflectionJobCount"), 4)

    def test_notice_creation_is_audited(self):
        self._call("POST", NOTICES, body={"title": "공지", "body": "내용", "audience": "admin", "severity": "info"}, context=admin_context())
        self.assertIn("notice.create", [e["action"] for e in self.audit.entries])

    def test_audit_log_list_is_admin_only(self):
        denied = self._call("GET", AUDIT, context=data_provider_context())
        self.assertEqual(denied["statusCode"], 403)

    def test_audit_log_list_returns_recorded_entries(self):
        proposal_id = self._seed_approved()
        self._call("POST", f"/api/v1/admin/data-proposals/{proposal_id}/approve", body={}, context=admin_context())
        response = self._call("GET", AUDIT, context=admin_context())
        self.assertEqual(response["statusCode"], 200)
        items = json.loads(response["body"])["items"]
        self.assertTrue(any(item["action"] == "data_proposal.approve" for item in items))

    def test_audit_log_list_filters_by_action(self):
        proposal_id = self._seed_approved()
        self._call("POST", f"/api/v1/admin/data-proposals/{proposal_id}/approve", body={}, context=admin_context())
        self._call("POST", NOTICES, body={"title": "공지", "body": "내용", "audience": "admin", "severity": "info"}, context=admin_context())
        response = self._call("GET", AUDIT, context=admin_context(), query={"action": "notice.create"})
        items = json.loads(response["body"])["items"]
        self.assertTrue(items)
        self.assertTrue(all(item["action"] == "notice.create" for item in items))


if __name__ == "__main__":
    unittest.main()
