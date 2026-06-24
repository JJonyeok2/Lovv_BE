import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from admin.app import handle_request
from admin.operations_repository import InMemoryAdminOperationsRepository
from admin.proposals_repository import InMemoryAdminProposalRepository


NOTICES = "/api/v1/admin/notices"
POLICIES = "/api/v1/admin/recommendation-policies"


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
    return {"userId": user_id, "roles": "R-ADMIN"}


def local_operator_context(user_id="operator-1"):
    return {"userId": user_id, "roles": "R-LOCAL-OPERATOR", "region_ids": "KR-42-150"}


class AdminOperationsApiTests(unittest.TestCase):
    def setUp(self):
        self.proposals = InMemoryAdminProposalRepository()
        self.operations = InMemoryAdminOperationsRepository()

    def _call(self, method, path, body=None, context=None, query=None):
        return handle_request(
            make_event(method, path, body=body, authorizer_context=context, query=query),
            proposal_repository=self.proposals,
            operations_repository=self.operations,
        )

    def test_admin_creates_and_publishes_notice(self):
        created = self._call(
            "POST",
            NOTICES,
            body={
                "title": "Service maintenance",
                "body": "Recommendation cache will be refreshed.",
                "audience": "admin",
                "severity": "warning",
            },
            context=admin_context(),
        )
        self.assertEqual(created["statusCode"], 201)
        notice = json.loads(created["body"])["notice"]
        self.assertEqual(notice["status"], "draft")
        self.assertEqual(notice["createdBy"], "admin-1")

        published = self._call("POST", f"{NOTICES}/{notice['id']}/publish", body={}, context=admin_context())
        self.assertEqual(published["statusCode"], 200)
        self.assertEqual(json.loads(published["body"])["notice"]["status"], "published")

        listed = self._call("GET", NOTICES, context=admin_context(), query={"status": "published"})
        items = json.loads(listed["body"])["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Service maintenance")

    def test_admin_creates_and_activates_recommendation_policy(self):
        created = self._call(
            "POST",
            POLICIES,
            body={
                "policyKey": "small_city_balance",
                "title": "Small city exposure balance",
                "description": "Prefer under-exposed small cities when quality is comparable.",
                "rules": {"underExposedBoost": 0.15, "maxSameRegionShare": 0.35},
                "priority": 80,
            },
            context=admin_context(),
        )
        self.assertEqual(created["statusCode"], 201)
        policy = json.loads(created["body"])["policy"]
        self.assertEqual(policy["status"], "draft")
        self.assertEqual(policy["rules"]["underExposedBoost"], 0.15)

        activated = self._call("POST", f"{POLICIES}/{policy['id']}/activate", body={}, context=admin_context())
        self.assertEqual(activated["statusCode"], 200)
        self.assertEqual(json.loads(activated["body"])["policy"]["status"], "active")

        listed = self._call("GET", POLICIES, context=admin_context(), query={"status": "active"})
        items = json.loads(listed["body"])["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["policyKey"], "small_city_balance")

    def test_non_admin_cannot_manage_operations(self):
        response = self._call(
            "POST",
            NOTICES,
            body={"title": "Blocked", "body": "Nope"},
            context=local_operator_context(),
        )
        self.assertEqual(response["statusCode"], 403)

        list_response = self._call("GET", POLICIES, context=local_operator_context())
        self.assertEqual(list_response["statusCode"], 403)

    def test_rejects_client_owned_operation_fields(self):
        notice_response = self._call(
            "POST",
            NOTICES,
            body={"title": "Bad", "body": "Bad", "createdBy": "user-1"},
            context=admin_context(),
        )
        self.assertEqual(notice_response["statusCode"], 400)
        self.assertEqual(json.loads(notice_response["body"])["error"]["code"], "INVALID_NOTICE_PAYLOAD")

        policy_response = self._call(
            "POST",
            POLICIES,
            body={"policyKey": "bad", "title": "Bad", "activatedBy": "user-1"},
            context=admin_context(),
        )
        self.assertEqual(policy_response["statusCode"], 400)
        self.assertEqual(json.loads(policy_response["body"])["error"]["code"], "INVALID_POLICY_PAYLOAD")


if __name__ == "__main__":
    unittest.main()
