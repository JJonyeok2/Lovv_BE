import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from admin.app import handle_request
from admin.proposals_repository import InMemoryAdminProposalRepository


def make_event(method, path, body=None, authorizer_context=None, path_parameters=None, query=None):
    event = {
        "rawPath": path,
        "headers": {"content-type": "application/json"},
        "pathParameters": path_parameters or {},
        "queryStringParameters": query,
        "requestContext": {"http": {"method": method}},
    }
    if authorizer_context is not None:
        event["requestContext"]["authorizer"] = {"lambda": authorizer_context}
    if body is not None:
        event["body"] = json.dumps(body)
    return event


def proposal_payload(**overrides):
    payload = {
        "contentType": "festival",
        "regionId": "KR-42-150",
        "cityId": "gangneung",
        "cityName": "강릉",
        "title": "강릉 커피축제 공식 정보 갱신",
        "description": "2026년 커피축제 일정과 공식 링크를 갱신합니다.",
        "officialSourceName": "강릉시청",
        "officialSourceUrl": "https://www.gn.go.kr/",
        "sourceUpdatedAt": "2026-06-20T00:00:00Z",
        "evidenceText": "공식 홈페이지 공지 기준",
        "payload": {"festivalName": "강릉 커피축제", "month": "10"},
        "serviceBoundary": {"cityId": "gangneung"},
        "gatewayCity": {"cityId": "gangneung", "name": "강릉"},
    }
    payload.update(overrides)
    return payload


def provider_context(user_id="provider-1", organization_ids=None):
    return {
        "userId": user_id,
        "roles": "R-DATA-PROVIDER",
        "organization_ids": ",".join(organization_ids or ["org-gangneung"]),
    }


def admin_context(user_id="admin-1"):
    return {"userId": user_id, "roles": "R-ADMIN"}


def local_operator_context(user_id="operator-1", region_ids=None):
    return {
        "userId": user_id,
        "roles": "R-LOCAL-OPERATOR",
        "region_ids": ",".join(region_ids or ["KR-42-150"]),
    }


class AdminProposalsAppTest(unittest.TestCase):
    def setUp(self):
        self.repository = InMemoryAdminProposalRepository(now="2026-06-23T09:00:00Z")

    def request(self, event):
        return handle_request(event, proposal_repository=self.repository)

    def create_proposal(self, user_id="provider-1", organization_ids=None, **overrides):
        response = self.request(
            make_event(
                "POST",
                "/api/v1/admin/data-proposals",
                proposal_payload(**overrides),
                authorizer_context=provider_context(user_id=user_id, organization_ids=organization_ids),
            )
        )
        self.assertEqual(response["statusCode"], 201)
        return json.loads(response["body"])["proposal"]

    def test_data_provider_can_create_submitted_data_proposal(self):
        response = self.request(
            make_event(
                "POST",
                "/api/v1/admin/data-proposals",
                proposal_payload(),
                authorizer_context=provider_context(),
            )
        )
        body = json.loads(response["body"])
        proposal = body["proposal"]

        self.assertEqual(response["statusCode"], 201)
        self.assertEqual(proposal["contentType"], "festival")
        self.assertEqual(proposal["regionId"], "KR-42-150")
        self.assertEqual(proposal["title"], "강릉 커피축제 공식 정보 갱신")
        self.assertEqual(proposal["status"], "submitted")
        self.assertEqual(proposal["createdBy"], "provider-1")
        self.assertEqual(proposal["organizationId"], "org-gangneung")
        self.assertIsInstance(proposal["submittedAt"], str)
        self.assertTrue(proposal["submittedAt"].endswith("Z"))
        self.assertEqual(proposal["payload"]["festivalName"], "강릉 커피축제")
        self.assertEqual(self.repository.history[0]["action"], "submitted")

    def test_create_rejects_client_supplied_authority_fields(self):
        response = self.request(
            make_event(
                "POST",
                "/api/v1/admin/data-proposals",
                proposal_payload(organizationId="org-forged", createdBy="other-user"),
                authorizer_context=provider_context(),
            )
        )
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 400)
        self.assertEqual(body["error"]["code"], "INVALID_PROPOSAL_PAYLOAD")
        self.assertEqual(self.repository.proposals, {})

    def test_admin_without_data_provider_role_cannot_create_proposal(self):
        response = self.request(
            make_event(
                "POST",
                "/api/v1/admin/data-proposals",
                proposal_payload(),
                authorizer_context=admin_context(),
            )
        )
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 403)
        self.assertEqual(body["error"]["code"], "ROLE_FORBIDDEN")

    def test_regular_user_cannot_list_proposals(self):
        response = self.request(
            make_event(
                "GET",
                "/api/v1/admin/data-proposals",
                authorizer_context={"userId": "user-1", "roles": "R-USER"},
            )
        )
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 403)
        self.assertEqual(body["error"]["code"], "ROLE_FORBIDDEN")

    def test_local_operator_cannot_create_proposal(self):
        response = self.request(
            make_event(
                "POST",
                "/api/v1/admin/data-proposals",
                proposal_payload(),
                authorizer_context=local_operator_context(),
            )
        )
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 403)
        self.assertEqual(body["error"]["code"], "ROLE_FORBIDDEN")

    def test_admin_can_list_all_proposals(self):
        first = self.create_proposal(user_id="provider-1", organization_ids=["org-gangneung"], title="강릉 제안")
        second = self.create_proposal(user_id="provider-2", organization_ids=["org-andong"], title="안동 제안")

        response = self.request(
            make_event(
                "GET",
                "/api/v1/admin/data-proposals",
                authorizer_context=admin_context(),
            )
        )
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual({item["proposalId"] for item in body["items"]}, {first["proposalId"], second["proposalId"]})
        self.assertEqual(body["nextCursor"], None)
        self.assertNotIn("payload", body["items"][0])

    def test_data_provider_lists_own_and_same_organization_proposals_only(self):
        own = self.create_proposal(user_id="provider-1", organization_ids=["org-gangneung"], title="내 제안")
        same_org = self.create_proposal(user_id="provider-2", organization_ids=["org-gangneung"], title="같은 기관 제안")
        self.create_proposal(user_id="provider-3", organization_ids=["org-andong"], title="다른 기관 제안")

        response = self.request(
            make_event(
                "GET",
                "/api/v1/admin/data-proposals",
                authorizer_context=provider_context(user_id="provider-1", organization_ids=["org-gangneung"]),
            )
        )
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual({item["proposalId"] for item in body["items"]}, {own["proposalId"], same_org["proposalId"]})

    def test_local_operator_lists_assigned_region_proposals_only(self):
        assigned = self.create_proposal(
            user_id="provider-1",
            organization_ids=["org-gangneung"],
            title="강릉 지역 제안",
            regionId="KR-42-150",
        )
        self.create_proposal(
            user_id="provider-2",
            organization_ids=["org-andong"],
            title="안동 지역 제안",
            regionId="KR-47-170",
        )

        response = self.request(
            make_event(
                "GET",
                "/api/v1/admin/data-proposals",
                authorizer_context=local_operator_context(user_id="operator-1", region_ids=["KR-42-150"]),
            )
        )
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual([item["proposalId"] for item in body["items"]], [assigned["proposalId"]])

    def test_local_operator_without_regions_gets_empty_proposal_list(self):
        self.create_proposal(user_id="provider-1", regionId="KR-42-150")

        response = self.request(
            make_event(
                "GET",
                "/api/v1/admin/data-proposals",
                authorizer_context={"userId": "operator-1", "roles": "R-LOCAL-OPERATOR", "region_ids": ""},
            )
        )
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(body["items"], [])

    def test_provider_can_get_own_detail_but_not_other_organization_detail(self):
        own = self.create_proposal(user_id="provider-1", organization_ids=["org-gangneung"], title="내 제안")
        other = self.create_proposal(user_id="provider-2", organization_ids=["org-andong"], title="다른 기관 제안")

        own_response = self.request(
            make_event(
                "GET",
                f"/api/v1/admin/data-proposals/{own['proposalId']}",
                authorizer_context=provider_context(user_id="provider-1", organization_ids=["org-gangneung"]),
                path_parameters={"proposalId": own["proposalId"]},
            )
        )
        other_response = self.request(
            make_event(
                "GET",
                f"/api/v1/admin/data-proposals/{other['proposalId']}",
                authorizer_context=provider_context(user_id="provider-1", organization_ids=["org-gangneung"]),
                path_parameters={"proposalId": other["proposalId"]},
            )
        )
        own_body = json.loads(own_response["body"])
        other_body = json.loads(other_response["body"])

        self.assertEqual(own_response["statusCode"], 200)
        self.assertEqual(own_body["proposal"]["proposalId"], own["proposalId"])
        self.assertEqual(own_body["proposal"]["payload"]["festivalName"], "강릉 커피축제")
        self.assertEqual(other_response["statusCode"], 404)
        self.assertEqual(other_body["error"]["code"], "PROPOSAL_NOT_FOUND")

    def test_local_operator_can_get_assigned_region_detail_only(self):
        assigned = self.create_proposal(
            user_id="provider-1",
            organization_ids=["org-gangneung"],
            title="강릉 지역 제안",
            regionId="KR-42-150",
        )
        other = self.create_proposal(
            user_id="provider-2",
            organization_ids=["org-andong"],
            title="안동 지역 제안",
            regionId="KR-47-170",
        )

        assigned_response = self.request(
            make_event(
                "GET",
                f"/api/v1/admin/data-proposals/{assigned['proposalId']}",
                authorizer_context=local_operator_context(region_ids=["KR-42-150"]),
                path_parameters={"proposalId": assigned["proposalId"]},
            )
        )
        other_response = self.request(
            make_event(
                "GET",
                f"/api/v1/admin/data-proposals/{other['proposalId']}",
                authorizer_context=local_operator_context(region_ids=["KR-42-150"]),
                path_parameters={"proposalId": other["proposalId"]},
            )
        )

        self.assertEqual(assigned_response["statusCode"], 200)
        self.assertEqual(json.loads(assigned_response["body"])["proposal"]["proposalId"], assigned["proposalId"])
        self.assertEqual(other_response["statusCode"], 404)

    def test_invalid_proposal_payload_returns_validation_error(self):
        response = self.request(
            make_event(
                "POST",
                "/api/v1/admin/data-proposals",
                proposal_payload(contentType="unknown", regionId="", payload=[]),
                authorizer_context=provider_context(),
            )
        )
        body = json.loads(response["body"])

        self.assertEqual(response["statusCode"], 400)
        self.assertEqual(body["error"]["code"], "INVALID_PROPOSAL_PAYLOAD")


if __name__ == "__main__":
    unittest.main()
