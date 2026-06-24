import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from admin.app import handle_request
from admin.proposals_repository import InMemoryAdminProposalRepository
from admin.monthly_destinations_repository import InMemoryMonthlyDestinationRepository
from admin.publish_jobs_repository import InMemoryPublishJobRepository, PUBLISH_JOB_TYPES


MONTHLY = "/api/v1/admin/monthly-destinations"
JOBS = "/api/v1/admin/publish-jobs"


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
    return {"userId": user_id, "roles": "R-LOCAL-OPERATOR", "region_ids": ",".join(region_ids or ["KR-42-150"])}


def seed_published_destination(proposals, monthly, jobs, region_id="KR-42-150"):
    provider = {"userId": "provider-1", "roles": ["R-DATA-PROVIDER"], "organizationIds": ["org-1"]}
    admin = {"userId": "admin-1", "roles": ["R-ADMIN"]}
    created = proposals.create(provider, {"contentType": "festival", "regionId": region_id, "cityName": "강릉", "title": "강릉 커피축제"})
    proposals.transition(created["proposalId"], "in_review", admin)
    approved = proposals.transition(created["proposalId"], "approved", admin)
    destination = monthly.create(admin, {
        "sourceProposalId": approved["proposalId"],
        "curationMonth": "2026-10",
        "themeCodes": ["coffee"],
        "regionId": region_id,
        "cityName": "강릉",
    })
    return destination


class PublishJobApiTests(unittest.TestCase):
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

    def _publish(self, region_id="KR-42-150"):
        destination = seed_published_destination(self.proposals, self.monthly, self.jobs, region_id=region_id)
        response = self._call("POST", f"{MONTHLY}/{destination['id']}/publish", body={}, context=admin_context())
        return destination, response

    def test_publishing_enqueues_four_reflection_jobs(self):
        _, response = self._publish()
        self.assertEqual(response["statusCode"], 200)
        body = json.loads(response["body"])
        self.assertEqual(body["destination"]["status"], "published")
        jobs = body["reflectionJobs"]
        self.assertEqual(len(jobs), 4)
        self.assertEqual({job["jobType"] for job in jobs}, set(PUBLISH_JOB_TYPES))
        self.assertTrue(all(job["status"] == "queued" for job in jobs))

    def test_non_publish_action_does_not_enqueue(self):
        destination = seed_published_destination(self.proposals, self.monthly, self.jobs)
        response = self._call("POST", f"{MONTHLY}/{destination['id']}/reject", body={}, context=admin_context())
        self.assertEqual(response["statusCode"], 200)
        self.assertNotIn("reflectionJobs", json.loads(response["body"]))

    def test_lists_reflection_history_for_destination(self):
        destination, _ = self._publish()
        response = self._call("GET", f"{MONTHLY}/{destination['id']}/publish-jobs", context=admin_context())
        self.assertEqual(response["statusCode"], 200)
        items = json.loads(response["body"])["items"]
        self.assertEqual(len(items), 4)

    def test_local_operator_scoped_history(self):
        destination, _ = self._publish(region_id="KR-42-150")
        ok = self._call("GET", f"{MONTHLY}/{destination['id']}/publish-jobs", context=local_operator_context(region_ids=["KR-42-150"]))
        self.assertEqual(ok["statusCode"], 200)
        denied = self._call("GET", f"{MONTHLY}/{destination['id']}/publish-jobs", context=local_operator_context(region_ids=["KR-99-999"]))
        self.assertEqual(denied["statusCode"], 404)

    def test_start_then_succeed_flow(self):
        destination, response = self._publish()
        job_id = json.loads(response["body"])["reflectionJobs"][0]["id"]
        started = self._call("POST", f"{JOBS}/{job_id}/start", body={}, context=admin_context())
        self.assertEqual(started["statusCode"], 200)
        self.assertEqual(json.loads(started["body"])["job"]["status"], "running")
        succeeded = self._call("POST", f"{JOBS}/{job_id}/succeed", body={}, context=admin_context())
        self.assertEqual(json.loads(succeeded["body"])["job"]["status"], "succeeded")

    def test_fail_then_retry_increments_attempt(self):
        destination, response = self._publish()
        job_id = json.loads(response["body"])["reflectionJobs"][0]["id"]
        self._call("POST", f"{JOBS}/{job_id}/start", body={}, context=admin_context())
        failed = self._call("POST", f"{JOBS}/{job_id}/fail", body={"errorMessage": "downstream timeout"}, context=admin_context())
        failed_job = json.loads(failed["body"])["job"]
        self.assertEqual(failed_job["status"], "failed")
        self.assertEqual(failed_job["lastErrorMessage"], "downstream timeout")
        retried = self._call("POST", f"{JOBS}/{job_id}/retry", body={}, context=admin_context())
        retried_job = json.loads(retried["body"])["job"]
        self.assertEqual(retried_job["status"], "queued")
        self.assertEqual(retried_job["attemptCount"], 1)

    def test_illegal_transition_is_409(self):
        destination, response = self._publish()
        job_id = json.loads(response["body"])["reflectionJobs"][0]["id"]
        # cannot succeed a job that has not started
        res = self._call("POST", f"{JOBS}/{job_id}/succeed", body={}, context=admin_context())
        self.assertEqual(res["statusCode"], 409)
        self.assertEqual(json.loads(res["body"])["error"]["code"], "PUBLISH_JOB_TRANSITION_FORBIDDEN")

    def test_transition_requires_admin(self):
        destination, response = self._publish()
        job_id = json.loads(response["body"])["reflectionJobs"][0]["id"]
        res = self._call("POST", f"{JOBS}/{job_id}/start", body={}, context=local_operator_context())
        self.assertEqual(res["statusCode"], 403)

    def test_transition_unknown_job_is_404(self):
        res = self._call("POST", f"{JOBS}/missing/start", body={}, context=admin_context())
        self.assertEqual(res["statusCode"], 404)
        self.assertEqual(json.loads(res["body"])["error"]["code"], "PUBLISH_JOB_NOT_FOUND")

    def test_transition_rejects_authority_fields(self):
        destination, response = self._publish()
        job_id = json.loads(response["body"])["reflectionJobs"][0]["id"]
        res = self._call("POST", f"{JOBS}/{job_id}/start", body={"status": "succeeded"}, context=admin_context())
        self.assertEqual(res["statusCode"], 400)
        self.assertEqual(json.loads(res["body"])["error"]["code"], "INVALID_PUBLISH_JOB_PAYLOAD")


if __name__ == "__main__":
    unittest.main()
