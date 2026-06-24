# @file src/admin/publish_jobs_repository.py
# @description Admin publish job repository (step 12: approved-data reflection).
# @lastModified 2026-06-24
#
# Owns admin_publish_jobs: when a monthly curated destination is published, four
# reflection jobs (catalog/RAG/search/recommendation sync) are enqueued, then run
# through their status machine. This PoC records and transitions job rows so the
# console shows a reflection history; it does not perform the downstream sync.

import os
import uuid

from shared.database import create_database_client


# The four downstream targets a publish fans out to. Enqueued together so the
# reflection history shows every surface that must pick up the published data.
PUBLISH_JOB_TYPES = ("catalog_sync", "rag_index_sync", "search_cache_sync", "recommendation_cache_sync")

# Job status machine. For each action: the statuses it may move FROM and the
# status it moves TO. Enforced in _validate_transition (409 if not allowed).
PUBLISH_JOB_TRANSITIONS = {
    "start": {"from": {"queued"}, "to": "running"},
    "succeed": {"from": {"running"}, "to": "succeeded"},
    "fail": {"from": {"queued", "running"}, "to": "failed"},
    "retry": {"from": {"failed"}, "to": "queued"},
    "cancel": {"from": {"queued", "running"}, "to": "canceled"},
}


class PublishJobTransitionError(Exception):
    def __init__(self, status_code, code, message):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class RdsDataPublishJobRepository:
    def __init__(self, rds_client=None, table=None):
        self.rds = rds_client or create_database_client()
        self.table = table or os.environ.get("ADMIN_PUBLISH_JOBS_TABLE_NAME", "admin_publish_jobs")

    @classmethod
    def from_env(cls):
        return cls()

    def enqueue_for_destination(self, destination_id, principal, now):
        jobs = []
        for job_type in PUBLISH_JOB_TYPES:
            job = _build_job(str(uuid.uuid4()), destination_id, job_type, principal, now)
            self.rds.execute(
                f"""
                INSERT INTO {self.table}
                  (id, proposal_id, monthly_curated_destination_id, job_type, status,
                   attempt_count, last_error_code, last_error_message, requested_by,
                   started_at, finished_at, created_at, updated_at)
                VALUES
                  (:id, :proposal_id, :monthly_curated_destination_id, :job_type, :status,
                   :attempt_count, :last_error_code, :last_error_message, :requested_by,
                   :started_at, :finished_at, :created_at, :updated_at)
                """,
                _row_params(job),
                include_result_metadata=False,
            )
            jobs.append(job)
        return jobs

    def list_for_destination(self, destination_id, limit=20):
        rows = self.rds.fetch_all(
            f"""
            SELECT * FROM {self.table}
            WHERE monthly_curated_destination_id = :destination_id
            ORDER BY created_at ASC
            LIMIT :limit
            """,
            {"destination_id": destination_id, "limit": int(limit)},
        )
        return [_job_from_row(row) for row in rows]

    def get(self, job_id):
        row = self.rds.fetch_one(
            f"SELECT * FROM {self.table} WHERE id = :id",
            {"id": job_id},
        )
        return _job_from_row(row) if row else None

    def transition(self, job_id, action, principal, now, payload=None):
        job = self.get(job_id)
        if not job:
            return None
        to_status = _validate_transition(job, action)
        updates = _transition_updates(job, action, to_status, now, payload or {})
        job.update(updates)
        assignments = ", ".join(f"{column} = :{column}" for column in _row_params_subset(updates))
        self.rds.execute(
            f"UPDATE {self.table} SET {assignments} WHERE id = :id",
            {**_row_params_subset(updates), "id": job_id},
            include_result_metadata=False,
        )
        return job


class InMemoryPublishJobRepository:
    def __init__(self, now="2026-06-24T00:00:00Z"):
        self.now = now
        self.jobs = {}

    def enqueue_for_destination(self, destination_id, principal, now=None):
        created = []
        for job_type in PUBLISH_JOB_TYPES:
            job_id = f"job-{len(self.jobs) + 1}"
            job = _build_job(job_id, destination_id, job_type, principal, now or self.now)
            self.jobs[job_id] = job
            created.append(dict(job))
        return created

    def list_for_destination(self, destination_id, limit=20):
        items = [job for job in self.jobs.values() if job.get("monthlyCuratedDestinationId") == destination_id]
        items.sort(key=lambda job: job.get("createdAt") or "")
        return [dict(job) for job in items[:limit]]

    def get(self, job_id):
        job = self.jobs.get(job_id)
        return dict(job) if job else None

    def transition(self, job_id, action, principal, now=None, payload=None):
        job = self.jobs.get(job_id)
        if not job:
            return None
        to_status = _validate_transition(job, action)
        job.update(_transition_updates(job, action, to_status, now or self.now, payload or {}))
        return dict(job)


def _validate_transition(job, action):
    rule = PUBLISH_JOB_TRANSITIONS.get(action)
    if not rule:
        raise PublishJobTransitionError(400, "INVALID_PUBLISH_JOB_ACTION", "Unsupported publish job action")
    current = job.get("status")
    if current not in rule["from"]:
        raise PublishJobTransitionError(
            409,
            "PUBLISH_JOB_TRANSITION_FORBIDDEN",
            f"Cannot {action} a job in status '{current}'",
        )
    return rule["to"]


def _transition_updates(job, action, to_status, now, payload):
    updates = {"status": to_status, "updatedAt": now}
    if action == "start":
        updates["startedAt"] = now
    elif action in ("succeed", "fail"):
        updates["finishedAt"] = now
        if action == "fail":
            updates["lastErrorCode"] = _optional_text(payload.get("errorCode")) or "REFLECTION_FAILED"
            updates["lastErrorMessage"] = _optional_text(payload.get("errorMessage"))
    elif action == "retry":
        # Re-queue for another attempt; clear the run timestamps but keep the
        # previous error on record until the next run overwrites it.
        updates["attemptCount"] = int(job.get("attemptCount") or 0) + 1
        updates["startedAt"] = None
        updates["finishedAt"] = None
    return updates


def _build_job(job_id, destination_id, job_type, principal, now):
    return {
        "id": job_id,
        "proposalId": None,
        "monthlyCuratedDestinationId": destination_id,
        "jobType": job_type,
        "status": "queued",
        "attemptCount": 0,
        "lastErrorCode": None,
        "lastErrorMessage": None,
        "requestedBy": (principal or {}).get("userId"),
        "startedAt": None,
        "finishedAt": None,
        "createdAt": now,
        "updatedAt": now,
    }


def _row_params(job):
    return {
        "id": job.get("id"),
        "proposal_id": job.get("proposalId"),
        "monthly_curated_destination_id": job.get("monthlyCuratedDestinationId"),
        "job_type": job.get("jobType"),
        "status": job.get("status"),
        "attempt_count": job.get("attemptCount") or 0,
        "last_error_code": job.get("lastErrorCode"),
        "last_error_message": job.get("lastErrorMessage"),
        "requested_by": job.get("requestedBy"),
        "started_at": job.get("startedAt"),
        "finished_at": job.get("finishedAt"),
        "created_at": job.get("createdAt"),
        "updated_at": job.get("updatedAt"),
    }


# Map the camelCase keys a transition can change to their snake_case columns so
# the UPDATE only writes fields that actually changed.
_COLUMN_FOR_FIELD = {
    "status": "status",
    "attemptCount": "attempt_count",
    "lastErrorCode": "last_error_code",
    "lastErrorMessage": "last_error_message",
    "startedAt": "started_at",
    "finishedAt": "finished_at",
    "updatedAt": "updated_at",
}


def _row_params_subset(updates):
    return {_COLUMN_FOR_FIELD[field]: value for field, value in updates.items() if field in _COLUMN_FOR_FIELD}


def _job_from_row(row):
    return {
        "id": row.get("id"),
        "proposalId": row.get("proposal_id"),
        "monthlyCuratedDestinationId": row.get("monthly_curated_destination_id"),
        "jobType": row.get("job_type"),
        "status": row.get("status"),
        "attemptCount": row.get("attempt_count") or 0,
        "lastErrorCode": row.get("last_error_code"),
        "lastErrorMessage": row.get("last_error_message"),
        "requestedBy": row.get("requested_by"),
        "startedAt": row.get("started_at"),
        "finishedAt": row.get("finished_at"),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


def _optional_text(value):
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        return value
    text = value.strip()
    return text or None
