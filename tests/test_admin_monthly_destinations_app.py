import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from admin.app import handle_request
from admin.proposals_repository import InMemoryAdminProposalRepository
from admin.monthly_destinations_repository import InMemoryMonthlyDestinationRepository
from admin.publish_jobs_repository import InMemoryPublishJobRepository


COLLECTION = "/api/v1/admin/monthly-destinations"


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


def provider_context(user_id="provider-1", organization_ids=None):
    return {
        "userId": user_id,
        "roles": "R-DATA-PROVIDER",
        "organization_ids": ",".join(organization_ids or ["org-gangneung"]),
    }


def local_operator_context(user_id="operator-1", region_ids=None):
    return {
        "userId": user_id,
        "roles": "R-LOCAL-OPERATOR",
        "region_ids": ",".join(region_ids or ["KR-42-150"]),
    }


def proposal_payload(**overrides):
    payload = {
        "contentType": "festival",
        "regionId": "KR-42-150",
        "cityId": "gangneung",
        "cityName": "강릉",
        "title": "강릉 커피축제 공식 정보 갱신",
        "officialSourceName": "강릉시청",
        "officialSourceUrl": "https://www.gn.go.kr/",
    }
    payload.update(overrides)
    return payload


def seed_approved_proposal(proposals, **overrides):
    provider = {"userId": "provider-1", "roles": ["R-DATA-PROVIDER"], "organizationIds": ["org-gangneung"]}
    admin = {"userId": "admin-1", "roles": ["R-ADMIN"]}
    created = proposals.create(provider, proposal_payload(**overrides))
    proposals.transition(created["proposalId"], "in_review", admin)
    approved = proposals.transition(created["proposalId"], "approved", admin)
    return approved


def promote_body(source_proposal_id, **overrides):
    body = {
        "sourceProposalId": source_proposal_id,
        "curationMonth": "2026-10",
        "themeCodes": ["coffee", "festival"],
    }
    body.update(overrides)
    return body


class MonthlyDestinationApiTests(unittest.TestCase):
    def setUp(self):
        self.proposals = InMemoryAdminProposalRepository()
        self.monthly = InMemoryMonthlyDestinationRepository()
        self.jobs = InMemoryPublishJobRepository()

    def _call(self, method, path, body=None, context=None, query=None):
        return handle_request(
            make_event(method, path, body=body, authorizer_context=context, query=query),
            proposal_repository=self.proposals,
            monthly_repository=self.monthly,
            publish_jobs_repository=self.jobs,
        )

    def test_promote_creates_candidate_from_approved_proposal(self):
        approved = seed_approved_proposal(self.proposals)
        response = self._call("POST", COLLECTION, body=promote_body(approved["proposalId"]), context=admin_context())
        self.assertEqual(response["statusCode"], 201)
        destination = json.loads(response["body"])["destination"]
        self.assertEqual(destination["status"], "candidate")
        self.assertEqual(destination["curationMonth"], "2026-10")
        self.assertEqual(destination["themeCodes"], ["coffee", "festival"])
        # city/region copied from the approved proposal
        self.assertEqual(destination["regionId"], "KR-42-150")
        self.assertEqual(destination["cityName"], "강릉")
        self.assertEqual(destination["sourceProposalId"], approved["proposalId"])

    def test_promote_rejects_non_admin(self):
        approved = seed_approved_proposal(self.proposals)
        response = self._call("POST", COLLECTION, body=promote_body(approved["proposalId"]), context=provider_context())
        self.assertEqual(response["statusCode"], 403)
        self.assertEqual(json.loads(response["body"])["error"]["code"], "ADMIN_ACCESS_REQUIRED")

    def test_promote_requires_approved_proposal(self):
        provider = {"userId": "provider-1", "roles": ["R-DATA-PROVIDER"], "organizationIds": ["org-gangneung"]}
        submitted = self.proposals.create(provider, proposal_payload())
        response = self._call("POST", COLLECTION, body=promote_body(submitted["proposalId"]), context=admin_context())
        self.assertEqual(response["statusCode"], 409)
        self.assertEqual(json.loads(response["body"])["error"]["code"], "PROPOSAL_NOT_APPROVED")

    def test_promote_unknown_proposal_is_404(self):
        response = self._call("POST", COLLECTION, body=promote_body("missing"), context=admin_context())
        self.assertEqual(response["statusCode"], 404)
        self.assertEqual(json.loads(response["body"])["error"]["code"], "PROPOSAL_NOT_FOUND")

    def test_promote_rejects_authority_fields(self):
        approved = seed_approved_proposal(self.proposals)
        body = promote_body(approved["proposalId"], status="published")
        response = self._call("POST", COLLECTION, body=body, context=admin_context())
        self.assertEqual(response["statusCode"], 400)
        self.assertEqual(json.loads(response["body"])["error"]["code"], "INVALID_MONTHLY_PAYLOAD")

    def test_promote_validates_curation_month(self):
        approved = seed_approved_proposal(self.proposals)
        body = promote_body(approved["proposalId"], curationMonth="2026/10")
        response = self._call("POST", COLLECTION, body=body, context=admin_context())
        self.assertEqual(response["statusCode"], 400)

    def _seed_candidate(self):
        approved = seed_approved_proposal(self.proposals)
        response = self._call("POST", COLLECTION, body=promote_body(approved["proposalId"]), context=admin_context())
        return json.loads(response["body"])["destination"]

    def test_publish_then_hide_flow(self):
        candidate = self._seed_candidate()
        published = self._call("POST", f"{COLLECTION}/{candidate['id']}/publish", body={"reason": "운영 게시"}, context=admin_context())
        self.assertEqual(published["statusCode"], 200)
        published_body = json.loads(published["body"])["destination"]
        self.assertEqual(published_body["status"], "published")
        self.assertEqual(published_body["publishedBy"], "admin-1")

        hidden = self._call("POST", f"{COLLECTION}/{candidate['id']}/hide", body={"reason": "정보 오류"}, context=admin_context())
        self.assertEqual(hidden["statusCode"], 200)
        hidden_body = json.loads(hidden["body"])["destination"]
        self.assertEqual(hidden_body["status"], "hidden")
        self.assertEqual(hidden_body["hiddenReason"], "정보 오류")

    def test_illegal_transition_is_409(self):
        candidate = self._seed_candidate()
        # cannot hide a candidate that was never published
        response = self._call("POST", f"{COLLECTION}/{candidate['id']}/hide", body={}, context=admin_context())
        self.assertEqual(response["statusCode"], 409)
        self.assertEqual(json.loads(response["body"])["error"]["code"], "MONTHLY_TRANSITION_FORBIDDEN")

    def test_transition_requires_admin(self):
        candidate = self._seed_candidate()
        response = self._call("POST", f"{COLLECTION}/{candidate['id']}/publish", body={}, context=local_operator_context())
        self.assertEqual(response["statusCode"], 403)

    def test_transition_unknown_destination_is_404(self):
        response = self._call("POST", f"{COLLECTION}/missing/publish", body={}, context=admin_context())
        self.assertEqual(response["statusCode"], 404)
        self.assertEqual(json.loads(response["body"])["error"]["code"], "MONTHLY_DESTINATION_NOT_FOUND")

    def test_admin_lists_all_local_operator_scoped(self):
        self._seed_candidate()  # KR-42-150
        other = seed_approved_proposal(self.proposals, regionId="KR-11-000", cityName="서울")
        self._call("POST", COLLECTION, body=promote_body(other["proposalId"]), context=admin_context())

        admin_list = self._call("GET", COLLECTION, context=admin_context())
        self.assertEqual(admin_list["statusCode"], 200)
        self.assertEqual(len(json.loads(admin_list["body"])["items"]), 2)

        operator_list = self._call("GET", COLLECTION, context=local_operator_context(region_ids=["KR-42-150"]))
        self.assertEqual(operator_list["statusCode"], 200)
        operator_items = json.loads(operator_list["body"])["items"]
        self.assertEqual(len(operator_items), 1)
        self.assertEqual(operator_items[0]["regionId"], "KR-42-150")

    def test_get_one_and_status_filter(self):
        candidate = self._seed_candidate()
        got = self._call("GET", f"{COLLECTION}/{candidate['id']}", context=admin_context())
        self.assertEqual(got["statusCode"], 200)
        self.assertEqual(json.loads(got["body"])["destination"]["id"], candidate["id"])

        filtered = self._call("GET", COLLECTION, context=admin_context(), query={"status": "published"})
        self.assertEqual(filtered["statusCode"], 200)
        self.assertEqual(len(json.loads(filtered["body"])["items"]), 0)


if __name__ == "__main__":
    unittest.main()
