# @file src/admin/metrics_repository.py
# @description Daily metrics repository for monthly curated destinations.
# @lastModified 2026-06-24
#
# Stores B2G-safe aggregate counters in destination_metrics_daily. This PoC
# records only daily aggregate increments, not raw user-level event logs.

import os

from shared.database import create_database_client


# Maps an inbound event type to the daily counter column it increments. The
# official vs partner link split (step 14) is two separate counters so official
# and affiliate engagement can be reported independently.
EVENT_COUNTER_COLUMNS = {
    "destination_impression": "destination_impressions",
    "destination_detail_open": "destination_detail_opens",
    "itinerary_generated": "itinerary_generated",
    "transport_detail_open": "transport_detail_opens",
    "itinerary_saved": "itinerary_saved",
    "itinerary_shared_or_exported": "itinerary_shared_or_exported",
    "official_link_click": "official_link_clicks",
    "partner_link_click": "partner_link_clicks",
    "visit_intent_submitted": "visit_intent_submitted",
    "visit_confirmed": "visit_confirmed",
}

METRIC_COUNTER_FIELDS = tuple(EVENT_COUNTER_COLUMNS.values()) + ("distinct_user_count",)
# k-anonymity threshold for B2G safety: a day's row is only marked
# min_group_size_met once it has at least this many distinct users, so small
# (re-identifiable) groups are flagged before any external reporting.
MIN_GROUP_SIZE = 5


class RdsDataDestinationMetricsRepository:
    def __init__(self, rds_client=None, table=None):
        self.rds = rds_client or create_database_client()
        self.table = table or os.environ.get("DESTINATION_METRICS_DAILY_TABLE_NAME", "destination_metrics_daily")

    @classmethod
    def from_env(cls):
        return cls()

    def record_event(self, destination, event_type, metric_date, now, increment=1, distinct_user_increment=0):
        # Upsert the (destination, day) row: insert it if missing, otherwise add
        # the increment to the matching counter. Only aggregate counts are stored,
        # never raw per-user events.
        column = _counter_column(event_type)
        row = _empty_metrics_row(destination, metric_date, now)
        row[column] = int(increment)
        row["distinct_user_count"] = int(distinct_user_increment)
        row["min_group_size_met"] = bool(row["distinct_user_count"] >= MIN_GROUP_SIZE)
        params = _row_params(row)
        params["increment"] = int(increment)
        params["distinct_user_increment"] = int(distinct_user_increment)
        params["min_group_size"] = MIN_GROUP_SIZE
        self.rds.execute(
            f"""
            INSERT INTO {self.table}
              (metric_date, monthly_curated_destination_id, city_id, region_id,
               destination_impressions, destination_detail_opens, itinerary_generated,
               transport_detail_opens, itinerary_saved, itinerary_shared_or_exported,
               official_link_clicks, partner_link_clicks, visit_intent_submitted,
               visit_confirmed, distinct_user_count, min_group_size_met,
               aggregation_status, created_at, updated_at)
            VALUES
              (:metric_date, :monthly_curated_destination_id, :city_id, :region_id,
               :destination_impressions, :destination_detail_opens, :itinerary_generated,
               :transport_detail_opens, :itinerary_saved, :itinerary_shared_or_exported,
               :official_link_clicks, :partner_link_clicks, :visit_intent_submitted,
               :visit_confirmed, :distinct_user_count, :min_group_size_met,
               :aggregation_status, :created_at, :updated_at)
            ON DUPLICATE KEY UPDATE
              {column} = {column} + :increment,
              distinct_user_count = distinct_user_count + :distinct_user_increment,
              min_group_size_met = IF(distinct_user_count + :distinct_user_increment >= :min_group_size, TRUE, min_group_size_met),
              aggregation_status = 'complete',
              updated_at = :updated_at
            """,
            params,
            include_result_metadata=False,
        )
        return self.get_daily(destination.get("id"), metric_date)

    def get_daily(self, destination_id, metric_date):
        row = self.rds.fetch_one(
            f"""
            SELECT * FROM {self.table}
            WHERE monthly_curated_destination_id = :destination_id
              AND metric_date = :metric_date
            """,
            {"destination_id": destination_id, "metric_date": metric_date},
        )
        return _metrics_from_row(row) if row else None

    def list_for_destination(self, destination_id, start_date=None, end_date=None, limit=31):
        clauses = ["monthly_curated_destination_id = :destination_id"]
        params = {"destination_id": destination_id}
        _add_date_filters(clauses, params, start_date, end_date)
        rows = self.rds.fetch_all(
            f"""
            SELECT * FROM {self.table}
            WHERE {' AND '.join(clauses)}
            ORDER BY metric_date DESC
            LIMIT :limit
            """,
            {**params, "limit": int(limit)},
        )
        return [_metrics_from_row(row) for row in rows]

    def list_summary(self, start_date=None, end_date=None, region_id=None, region_ids=None, limit=50):
        clauses = []
        params = {}
        _add_date_filters(clauses, params, start_date, end_date)
        if region_id:
            clauses.append("region_id = :region_id")
            params["region_id"] = region_id
        if region_ids is not None:
            region_ids = [value for value in region_ids if value]
            if not region_ids:
                return []
            placeholders = ", ".join(f":region_{index}" for index in range(len(region_ids)))
            clauses.append(f"region_id IN ({placeholders})")
            params.update({f"region_{index}": value for index, value in enumerate(region_ids)})
        rows = self.rds.fetch_all(
            f"""
            SELECT
              monthly_curated_destination_id,
              city_id,
              region_id,
              MIN(metric_date) AS start_date,
              MAX(metric_date) AS end_date,
              SUM(destination_impressions) AS destination_impressions,
              SUM(destination_detail_opens) AS destination_detail_opens,
              SUM(itinerary_generated) AS itinerary_generated,
              SUM(transport_detail_opens) AS transport_detail_opens,
              SUM(itinerary_saved) AS itinerary_saved,
              SUM(itinerary_shared_or_exported) AS itinerary_shared_or_exported,
              SUM(official_link_clicks) AS official_link_clicks,
              SUM(partner_link_clicks) AS partner_link_clicks,
              SUM(visit_intent_submitted) AS visit_intent_submitted,
              SUM(visit_confirmed) AS visit_confirmed,
              SUM(distinct_user_count) AS distinct_user_count,
              MIN(min_group_size_met) AS min_group_size_met
            FROM {self.table}
            {(' WHERE ' + ' AND '.join(clauses)) if clauses else ''}
            GROUP BY monthly_curated_destination_id, city_id, region_id
            ORDER BY destination_impressions DESC, monthly_curated_destination_id ASC
            LIMIT :limit
            """,
            {**params, "limit": int(limit)},
        )
        return [_summary_from_row(row) for row in rows]


class InMemoryDestinationMetricsRepository:
    def __init__(self):
        self.rows = {}

    def record_event(self, destination, event_type, metric_date, now, increment=1, distinct_user_increment=0):
        column = _counter_column(event_type)
        key = (metric_date, destination.get("id"))
        row = self.rows.get(key)
        if row is None:
            row = _empty_metrics_row(destination, metric_date, now)
            self.rows[key] = row
        row[column] = int(row.get(column) or 0) + int(increment)
        row["distinct_user_count"] = int(row.get("distinct_user_count") or 0) + int(distinct_user_increment)
        row["min_group_size_met"] = bool(row["distinct_user_count"] >= MIN_GROUP_SIZE)
        row["aggregation_status"] = "complete"
        row["updated_at"] = now
        return _metrics_from_row(row)

    def get_daily(self, destination_id, metric_date):
        row = self.rows.get((metric_date, destination_id))
        return _metrics_from_row(row) if row else None

    def list_for_destination(self, destination_id, start_date=None, end_date=None, limit=31):
        items = [
            row for (metric_date, row_destination_id), row in self.rows.items()
            if row_destination_id == destination_id and _date_matches(metric_date, start_date, end_date)
        ]
        items.sort(key=lambda row: row.get("metric_date") or "", reverse=True)
        return [_metrics_from_row(row) for row in items[:limit]]

    def list_summary(self, start_date=None, end_date=None, region_id=None, region_ids=None, limit=50):
        allowed_regions = set(region_ids or []) if region_ids is not None else None
        grouped = {}
        for (metric_date, destination_id), row in self.rows.items():
            if not _date_matches(metric_date, start_date, end_date):
                continue
            if region_id and row.get("region_id") != region_id:
                continue
            if allowed_regions is not None and row.get("region_id") not in allowed_regions:
                continue
            key = (destination_id, row.get("city_id"), row.get("region_id"))
            target = grouped.setdefault(key, _empty_summary(destination_id, row.get("city_id"), row.get("region_id")))
            target["startDate"] = min(target["startDate"], metric_date) if target["startDate"] else metric_date
            target["endDate"] = max(target["endDate"], metric_date) if target["endDate"] else metric_date
            for field in METRIC_COUNTER_FIELDS:
                target[_camel_metric_field(field)] += int(row.get(field) or 0)
            target["minGroupSizeMet"] = target["minGroupSizeMet"] and bool(row.get("min_group_size_met"))
        items = list(grouped.values())
        items.sort(key=lambda item: (-item["destinationImpressions"], item["monthlyCuratedDestinationId"]))
        return items[:limit]


def _counter_column(event_type):
    column = EVENT_COUNTER_COLUMNS.get(event_type)
    if not column:
        raise ValueError("Unsupported metrics event type")
    return column


def _empty_metrics_row(destination, metric_date, now):
    row = {
        "metric_date": metric_date,
        "monthly_curated_destination_id": destination.get("id"),
        "city_id": destination.get("cityId") or "unknown",
        "region_id": destination.get("regionId") or "unknown",
        "min_group_size_met": False,
        "aggregation_status": "complete",
        "created_at": now,
        "updated_at": now,
    }
    for field in METRIC_COUNTER_FIELDS:
        row[field] = 0
    return row


def _row_params(row):
    return dict(row)


def _add_date_filters(clauses, params, start_date, end_date):
    if start_date:
        clauses.append("metric_date >= :start_date")
        params["start_date"] = start_date
    if end_date:
        clauses.append("metric_date <= :end_date")
        params["end_date"] = end_date


def _date_matches(metric_date, start_date, end_date):
    return (not start_date or metric_date >= start_date) and (not end_date or metric_date <= end_date)


def _metrics_from_row(row):
    return {
        "metricDate": _date_text(row.get("metric_date")),
        "monthlyCuratedDestinationId": row.get("monthly_curated_destination_id"),
        "cityId": row.get("city_id"),
        "regionId": row.get("region_id"),
        "destinationImpressions": int(row.get("destination_impressions") or 0),
        "destinationDetailOpens": int(row.get("destination_detail_opens") or 0),
        "itineraryGenerated": int(row.get("itinerary_generated") or 0),
        "transportDetailOpens": int(row.get("transport_detail_opens") or 0),
        "itinerarySaved": int(row.get("itinerary_saved") or 0),
        "itinerarySharedOrExported": int(row.get("itinerary_shared_or_exported") or 0),
        "officialLinkClicks": int(row.get("official_link_clicks") or 0),
        "partnerLinkClicks": int(row.get("partner_link_clicks") or 0),
        "visitIntentSubmitted": int(row.get("visit_intent_submitted") or 0),
        "visitConfirmed": int(row.get("visit_confirmed") or 0),
        "distinctUserCount": int(row.get("distinct_user_count") or 0),
        "minGroupSizeMet": bool(row.get("min_group_size_met")),
        "aggregationStatus": row.get("aggregation_status") or "complete",
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


def _summary_from_row(row):
    item = _empty_summary(
        row.get("monthly_curated_destination_id"),
        row.get("city_id"),
        row.get("region_id"),
    )
    item["startDate"] = _date_text(row.get("start_date"))
    item["endDate"] = _date_text(row.get("end_date"))
    for field in METRIC_COUNTER_FIELDS:
        item[_camel_metric_field(field)] = int(row.get(field) or 0)
    item["minGroupSizeMet"] = bool(row.get("min_group_size_met"))
    return item


def _empty_summary(destination_id, city_id, region_id):
    item = {
        "monthlyCuratedDestinationId": destination_id,
        "cityId": city_id,
        "regionId": region_id,
        "startDate": None,
        "endDate": None,
        "minGroupSizeMet": True,
    }
    for field in METRIC_COUNTER_FIELDS:
        item[_camel_metric_field(field)] = 0
    return item


def _camel_metric_field(field):
    return {
        "destination_impressions": "destinationImpressions",
        "destination_detail_opens": "destinationDetailOpens",
        "itinerary_generated": "itineraryGenerated",
        "transport_detail_opens": "transportDetailOpens",
        "itinerary_saved": "itinerarySaved",
        "itinerary_shared_or_exported": "itinerarySharedOrExported",
        "official_link_clicks": "officialLinkClicks",
        "partner_link_clicks": "partnerLinkClicks",
        "visit_intent_submitted": "visitIntentSubmitted",
        "visit_confirmed": "visitConfirmed",
        "distinct_user_count": "distinctUserCount",
    }[field]


def _date_text(value):
    return value.isoformat() if hasattr(value, "isoformat") else value

