import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from admin.app import handle_request
from admin.metrics_repository import InMemoryDestinationMetricsRepository
from admin.monthly_destinations_repository import InMemoryMonthlyDestinationRepository
from admin.proposals_repository import InMemoryAdminProposalRepository
from admin.publish_jobs_repository import InMemoryPublishJobRepository


MONTHLY = "/api/v1/admin/monthly-destinations"
METRICS = "/api/v1/admin/metrics/destinations"


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


def local_operator_context(user_id="operator-1", region_ids=None):
    return {
        "userId": user_id,
        "roles": "R-LOCAL-OPERATOR",
        "region_ids": ",".join(region_ids or ["KR-42-150"]),
    }


def provider_context(user_id="provider-1"):
    return {"userId": user_id, "roles": "R-DATA-PROVIDER"}


class AdminMetricsApiTests(unittest.TestCase):
    def setUp(self):
        self.proposals = InMemoryAdminProposalRepository()
        self.monthly = InMemoryMonthlyDestinationRepository()
        self.jobs = InMemoryPublishJobRepository()
        self.metrics = InMemoryDestinationMetricsRepository()

    def _call(self, method, path, body=None, context=None, query=None):
        return handle_request(
            make_event(method, path, body=body, authorizer_context=context, query=query),
            proposal_repository=self.proposals,
            monthly_repository=self.monthly,
            publish_jobs_repository=self.jobs,
            metrics_repository=self.metrics,
        )

    def _destination(self, region_id="KR-42-150"):
        return self.monthly.create(
            {"userId": "admin-1", "roles": ["R-ADMIN"]},
            {
                "sourceProposalId": "proposal-1",
                "curationMonth": "2026-10",
                "themeCodes": ["coffee"],
                "regionId": region_id,
                "cityId": "gangneung",
                "cityName": "강릉",
            },
        )

    def test_records_daily_destination_event(self):
        destination = self._destination()
        response = self._call(
            "POST",
            f"{MONTHLY}/{destination['id']}/events",
            body={
                "eventType": "destination_impression",
                "metricDate": "2026-10-01",
                "increment": 3,
                "distinctUserIncrement": 2,
            },
            context=admin_context(),
        )

        self.assertEqual(response["statusCode"], 202)
        metric = json.loads(response["body"])["metric"]
        self.assertEqual(metric["metricDate"], "2026-10-01")
        self.assertEqual(metric["monthlyCuratedDestinationId"], destination["id"])
        self.assertEqual(metric["destinationImpressions"], 3)
        self.assertEqual(metric["distinctUserCount"], 2)
        self.assertFalse(metric["minGroupSizeMet"])

    def test_accumulates_link_clicks_and_group_size(self):
        destination = self._destination()
        for _ in range(2):
            self._call(
                "POST",
                f"{MONTHLY}/{destination['id']}/events",
                body={
                    "eventType": "official_link_click",
                    "metricDate": "2026-10-01",
                    "distinctUserIncrement": 3,
                },
                context=admin_context(),
            )
        self._call(
            "POST",
            f"{MONTHLY}/{destination['id']}/events",
            body={
                "eventType": "partner_link_click",
                "metricDate": "2026-10-01",
                "increment": 4,
            },
            context=admin_context(),
        )

        response = self._call("GET", f"{MONTHLY}/{destination['id']}/metrics", context=admin_context())
        self.assertEqual(response["statusCode"], 200)
        metric = json.loads(response["body"])["items"][0]
        self.assertEqual(metric["officialLinkClicks"], 2)
        self.assertEqual(metric["partnerLinkClicks"], 4)
        self.assertEqual(metric["distinctUserCount"], 6)
        self.assertTrue(metric["minGroupSizeMet"])

        summary_response = self._call("GET", METRICS, context=admin_context())
        summary = json.loads(summary_response["body"])["items"][0]
        self.assertEqual(summary["officialLinkClicks"], 2)
        self.assertEqual(summary["partnerLinkClicks"], 4)

    def test_lists_summary_scoped_by_local_operator_regions(self):
        own = self._destination(region_id="KR-42-150")
        other = self._destination(region_id="KR-11-000")
        self._call(
            "POST",
            f"{MONTHLY}/{own['id']}/events",
            body={"eventType": "destination_impression", "metricDate": "2026-10-01", "increment": 4},
            context=admin_context(),
        )
        self._call(
            "POST",
            f"{MONTHLY}/{other['id']}/events",
            body={"eventType": "destination_impression", "metricDate": "2026-10-01", "increment": 7},
            context=admin_context(),
        )

        admin_response = self._call("GET", METRICS, context=admin_context())
        self.assertEqual(len(json.loads(admin_response["body"])["items"]), 2)

        operator_response = self._call("GET", METRICS, context=local_operator_context(region_ids=["KR-42-150"]))
        items = json.loads(operator_response["body"])["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["monthlyCuratedDestinationId"], own["id"])
        self.assertEqual(items[0]["destinationImpressions"], 4)

    def test_local_operator_cannot_record_outside_region(self):
        destination = self._destination(region_id="KR-11-000")
        response = self._call(
            "POST",
            f"{MONTHLY}/{destination['id']}/events",
            body={"eventType": "destination_impression", "metricDate": "2026-10-01"},
            context=local_operator_context(region_ids=["KR-42-150"]),
        )
        self.assertEqual(response["statusCode"], 404)

    def test_data_provider_cannot_record_metrics(self):
        destination = self._destination()
        response = self._call(
            "POST",
            f"{MONTHLY}/{destination['id']}/events",
            body={"eventType": "destination_impression", "metricDate": "2026-10-01"},
            context=provider_context(),
        )
        self.assertEqual(response["statusCode"], 403)

    def test_rejects_invalid_event_payload(self):
        destination = self._destination()
        invalid_type = self._call(
            "POST",
            f"{MONTHLY}/{destination['id']}/events",
            body={"eventType": "unknown", "metricDate": "2026-10-01"},
            context=admin_context(),
        )
        self.assertEqual(invalid_type["statusCode"], 400)

        authority_field = self._call(
            "POST",
            f"{MONTHLY}/{destination['id']}/events",
            body={"eventType": "destination_impression", "metricDate": "2026-10-01", "regionId": "KR-42-150"},
            context=admin_context(),
        )
        self.assertEqual(authority_field["statusCode"], 400)
        self.assertEqual(json.loads(authority_field["body"])["error"]["code"], "INVALID_METRICS_EVENT_PAYLOAD")


if __name__ == "__main__":
    unittest.main()
