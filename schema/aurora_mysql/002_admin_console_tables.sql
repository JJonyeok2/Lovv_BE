-- Lovv Admin Console Aurora MySQL migration.
-- Scope:
--   1. Admin RBAC role/region assignments
--   2. Public data proposal review workflow
--   3. Monthly curated destination publishing workflow
--   4. B2G-safe aggregated metrics with official/partner link separation
--   5. Admin audit log foundation
--
-- Apply after schema/aurora_mysql/001_product_api_tables.sql.

CREATE TABLE IF NOT EXISTS admin_organizations (
  id                VARCHAR(80)  NOT NULL,
  organization_type VARCHAR(30)  NOT NULL,
  name              VARCHAR(160) NOT NULL,
  country_code      CHAR(2)      NULL,
  region_id         VARCHAR(80)  NULL,
  contact_email     VARCHAR(255) NULL,
  status            VARCHAR(20)  NOT NULL DEFAULT 'active',
  created_at        DATETIME(3)  NOT NULL,
  updated_at        DATETIME(3)  NOT NULL,
  PRIMARY KEY (id),
  KEY idx_admin_org_type_status (organization_type, status),
  KEY idx_admin_org_region (region_id),
  CONSTRAINT chk_admin_org_type
    CHECK (organization_type IN ('public_agency', 'local_operator', 'data_provider', 'internal', 'partner')),
  CONSTRAINT chk_admin_org_status
    CHECK (status IN ('active', 'suspended', 'archived'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS user_role_assignments (
  id               CHAR(36)     NOT NULL,
  user_id          CHAR(36)     NOT NULL,
  role_code        VARCHAR(40)  NOT NULL,
  organization_id  VARCHAR(80)  NULL,
  status           VARCHAR(20)  NOT NULL DEFAULT 'active',
  valid_from       DATETIME(3)  NOT NULL,
  valid_until      DATETIME(3)  NULL,
  granted_by       CHAR(36)     NULL,
  grant_reason     VARCHAR(255) NULL,
  created_at       DATETIME(3)  NOT NULL,
  updated_at       DATETIME(3)  NOT NULL,
  -- VIRTUAL (not STORED): the base columns user_id / organization_id below carry
  -- FKs with CASCADE / SET NULL actions, which MySQL 8 forbids on base columns of
  -- a STORED generated column. VIRTUAL keeps the unique active-row guarantee
  -- (uq_user_role_active) without triggering that restriction.
  active_role_key  VARCHAR(220)
    GENERATED ALWAYS AS (
      CASE
        WHEN status = 'active'
        THEN CONCAT(user_id, '#', role_code, '#', COALESCE(organization_id, ''))
        ELSE NULL
      END
    ) VIRTUAL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_user_role_active (active_role_key),
  KEY idx_user_role_lookup (user_id, status, role_code, valid_from, valid_until),
  KEY idx_user_role_org (organization_id, status),
  KEY idx_user_role_granted_by (granted_by),
  CONSTRAINT fk_user_role_user
    FOREIGN KEY (user_id) REFERENCES users(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_user_role_org
    FOREIGN KEY (organization_id) REFERENCES admin_organizations(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_user_role_granted_by
    FOREIGN KEY (granted_by) REFERENCES users(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT chk_user_role_code
    CHECK (role_code IN ('R-ADMIN', 'R-DATA-PROVIDER', 'R-LOCAL-OPERATOR')),
  CONSTRAINT chk_user_role_status
    CHECK (status IN ('active', 'suspended', 'revoked')),
  CONSTRAINT chk_user_role_valid_window
    CHECK (valid_until IS NULL OR valid_until > valid_from)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS user_region_assignments (
  id                CHAR(36)     NOT NULL,
  user_id           CHAR(36)     NOT NULL,
  region_id         VARCHAR(80)  NOT NULL,
  organization_id   VARCHAR(80)  NULL,
  status            VARCHAR(20)  NOT NULL DEFAULT 'active',
  valid_from        DATETIME(3)  NOT NULL,
  valid_until       DATETIME(3)  NULL,
  granted_by        CHAR(36)     NULL,
  grant_reason      VARCHAR(255) NULL,
  created_at        DATETIME(3)  NOT NULL,
  updated_at        DATETIME(3)  NOT NULL,
  -- VIRTUAL (not STORED): same reason as user_role_assignments.active_role_key —
  -- base columns carry CASCADE / SET NULL FKs, disallowed on STORED gen columns.
  active_region_key VARCHAR(220)
    GENERATED ALWAYS AS (
      CASE
        WHEN status = 'active'
        THEN CONCAT(user_id, '#', region_id, '#', COALESCE(organization_id, ''))
        ELSE NULL
      END
    ) VIRTUAL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_user_region_active (active_region_key),
  KEY idx_user_region_lookup (user_id, status, region_id, valid_from, valid_until),
  KEY idx_user_region_region (region_id, status),
  KEY idx_user_region_org (organization_id, status),
  KEY idx_user_region_granted_by (granted_by),
  CONSTRAINT fk_user_region_user
    FOREIGN KEY (user_id) REFERENCES users(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_user_region_org
    FOREIGN KEY (organization_id) REFERENCES admin_organizations(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_user_region_granted_by
    FOREIGN KEY (granted_by) REFERENCES users(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT chk_user_region_status
    CHECK (status IN ('active', 'suspended', 'revoked')),
  CONSTRAINT chk_user_region_valid_window
    CHECK (valid_until IS NULL OR valid_until > valid_from)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS admin_data_proposals (
  id                    CHAR(36)     NOT NULL,
  proposal_code         VARCHAR(40)  NOT NULL,
  content_type          VARCHAR(30)  NOT NULL,
  region_id             VARCHAR(80)  NOT NULL,
  city_id               VARCHAR(80)  NULL,
  city_name             VARCHAR(120) NULL,
  title                 VARCHAR(180) NOT NULL,
  description           TEXT         NULL,
  official_source_name  VARCHAR(160) NULL,
  official_source_url   VARCHAR(500) NULL,
  source_updated_at     DATETIME(3)  NULL,
  evidence_text         TEXT         NULL,
  payload_json          JSON         NULL,
  service_boundary_json JSON         NULL,
  gateway_city_json     JSON         NULL,
  status                VARCHAR(30)  NOT NULL DEFAULT 'draft',
  created_by            CHAR(36)     NOT NULL,
  organization_id       VARCHAR(80)  NULL,
  submitted_at          DATETIME(3)  NULL,
  reviewed_by           CHAR(36)     NULL,
  reviewed_at           DATETIME(3)  NULL,
  review_note           TEXT         NULL,
  approved_content_hash CHAR(64)     NULL,
  created_at            DATETIME(3)  NOT NULL,
  updated_at            DATETIME(3)  NOT NULL,
  deleted_at            DATETIME(3)  NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_admin_data_proposal_code (proposal_code),
  KEY idx_admin_data_proposal_status (status, updated_at),
  KEY idx_admin_data_proposal_region_status (region_id, status, updated_at),
  KEY idx_admin_data_proposal_org_status (organization_id, status, updated_at),
  KEY idx_admin_data_proposal_created_by (created_by, status, updated_at),
  KEY idx_admin_data_proposal_reviewed_by (reviewed_by, reviewed_at),
  CONSTRAINT fk_admin_data_proposal_creator
    FOREIGN KEY (created_by) REFERENCES users(id)
    ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT fk_admin_data_proposal_reviewer
    FOREIGN KEY (reviewed_by) REFERENCES users(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_admin_data_proposal_org
    FOREIGN KEY (organization_id) REFERENCES admin_organizations(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT chk_admin_data_proposal_type
    CHECK (content_type IN ('attraction', 'festival', 'experience', 'transport', 'monthly_destination')),
  CONSTRAINT chk_admin_data_proposal_status
    CHECK (status IN ('draft', 'submitted', 'in_review', 'change_requested', 'approved', 'rejected', 'withdrawn', 'archived'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS admin_data_proposal_history (
  id                CHAR(36)    NOT NULL,
  proposal_id       CHAR(36)    NOT NULL,
  action            VARCHAR(40) NOT NULL,
  from_status       VARCHAR(30) NULL,
  to_status         VARCHAR(30) NULL,
  actor_user_id     CHAR(36)    NULL,
  actor_roles_json  JSON        NULL,
  note              TEXT        NULL,
  metadata_json     JSON        NULL,
  created_at        DATETIME(3) NOT NULL,
  PRIMARY KEY (id),
  KEY idx_admin_proposal_history_proposal (proposal_id, created_at),
  KEY idx_admin_proposal_history_actor (actor_user_id, created_at),
  CONSTRAINT fk_admin_proposal_history_proposal
    FOREIGN KEY (proposal_id) REFERENCES admin_data_proposals(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_admin_proposal_history_actor
    FOREIGN KEY (actor_user_id) REFERENCES users(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT chk_admin_proposal_history_action
    CHECK (action IN ('created', 'submitted', 'in_review', 'change_requested', 'approved', 'rejected', 'withdrawn', 'archived', 'updated', 'commented'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS monthly_curated_destinations (
  id                    CHAR(36)     NOT NULL,
  city_id               VARCHAR(80)  NOT NULL,
  city_name             VARCHAR(120) NULL,
  region_id             VARCHAR(80)  NOT NULL,
  source_proposal_id    CHAR(36)     NULL,
  curation_month        CHAR(7)      NOT NULL,
  theme_codes           JSON         NOT NULL,
  official_source_url   VARCHAR(500) NULL,
  official_source_name  VARCHAR(160) NULL,
  source_updated_at     DATETIME(3)  NULL,
  valid_from            DATETIME(3)  NULL,
  valid_until           DATETIME(3)  NULL,
  status                VARCHAR(30)  NOT NULL DEFAULT 'candidate',
  publish_reason        TEXT         NULL,
  service_boundary_json JSON         NULL,
  gateway_city_json     JSON         NULL,
  published_by          CHAR(36)     NULL,
  published_at          DATETIME(3)  NULL,
  hidden_by             CHAR(36)     NULL,
  hidden_at             DATETIME(3)  NULL,
  hidden_reason         TEXT         NULL,
  created_at            DATETIME(3)  NOT NULL,
  updated_at            DATETIME(3)  NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_monthly_destination_city_month (city_id, curation_month),
  KEY idx_monthly_destination_region_month (region_id, curation_month, status),
  KEY idx_monthly_destination_status (status, updated_at),
  KEY idx_monthly_destination_source_proposal (source_proposal_id),
  KEY idx_monthly_destination_published_by (published_by, published_at),
  KEY idx_monthly_destination_hidden_by (hidden_by, hidden_at),
  CONSTRAINT fk_monthly_destination_source_proposal
    FOREIGN KEY (source_proposal_id) REFERENCES admin_data_proposals(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_monthly_destination_published_by
    FOREIGN KEY (published_by) REFERENCES users(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_monthly_destination_hidden_by
    FOREIGN KEY (hidden_by) REFERENCES users(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT chk_monthly_destination_status
    CHECK (status IN ('candidate', 'scheduled', 'published', 'hidden', 'expired', 'rejected')),
  CONSTRAINT chk_monthly_destination_month
    CHECK (curation_month REGEXP '^[0-9]{4}-[0-9]{2}$'),
  CONSTRAINT chk_monthly_destination_valid_window
    CHECK (valid_until IS NULL OR valid_from IS NULL OR valid_until > valid_from)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS admin_publish_jobs (
  id                              CHAR(36)     NOT NULL,
  proposal_id                     CHAR(36)     NULL,
  monthly_curated_destination_id  CHAR(36)     NULL,
  job_type                        VARCHAR(40)  NOT NULL,
  status                          VARCHAR(30)  NOT NULL DEFAULT 'queued',
  attempt_count                   INT          NOT NULL DEFAULT 0,
  last_error_code                 VARCHAR(80)  NULL,
  last_error_message              TEXT         NULL,
  requested_by                    CHAR(36)     NULL,
  started_at                      DATETIME(3)  NULL,
  finished_at                     DATETIME(3)  NULL,
  created_at                      DATETIME(3)  NOT NULL,
  updated_at                      DATETIME(3)  NOT NULL,
  PRIMARY KEY (id),
  KEY idx_admin_publish_job_status (status, updated_at),
  KEY idx_admin_publish_job_proposal (proposal_id, created_at),
  KEY idx_admin_publish_job_destination (monthly_curated_destination_id, created_at),
  KEY idx_admin_publish_job_requested_by (requested_by, created_at),
  -- RESTRICT (not SET NULL): proposal_id and monthly_curated_destination_id are
  -- referenced by chk_admin_publish_job_resource. MySQL 8 (error 3823) forbids a
  -- column from being in a CHECK while also being the target of a SET NULL /
  -- CASCADE FK action, so these two resource FKs use RESTRICT.
  CONSTRAINT fk_admin_publish_job_proposal
    FOREIGN KEY (proposal_id) REFERENCES admin_data_proposals(id)
    ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_admin_publish_job_destination
    FOREIGN KEY (monthly_curated_destination_id) REFERENCES monthly_curated_destinations(id)
    ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_admin_publish_job_requested_by
    FOREIGN KEY (requested_by) REFERENCES users(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT chk_admin_publish_job_type
    CHECK (job_type IN ('catalog_sync', 'rag_index_sync', 'search_cache_sync', 'recommendation_cache_sync')),
  CONSTRAINT chk_admin_publish_job_status
    CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'canceled')),
  CONSTRAINT chk_admin_publish_job_attempt
    CHECK (attempt_count >= 0),
  CONSTRAINT chk_admin_publish_job_resource
    CHECK (proposal_id IS NOT NULL OR monthly_curated_destination_id IS NOT NULL)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS destination_metrics_daily (
  metric_date                       DATE        NOT NULL,
  monthly_curated_destination_id    CHAR(36)    NOT NULL,
  city_id                           VARCHAR(80) NOT NULL,
  region_id                         VARCHAR(80) NOT NULL,
  destination_impressions           INT         NOT NULL DEFAULT 0,
  destination_detail_opens          INT         NOT NULL DEFAULT 0,
  itinerary_generated               INT         NOT NULL DEFAULT 0,
  transport_detail_opens            INT         NOT NULL DEFAULT 0,
  itinerary_saved                   INT         NOT NULL DEFAULT 0,
  itinerary_shared_or_exported      INT         NOT NULL DEFAULT 0,
  official_link_clicks              INT         NOT NULL DEFAULT 0,
  partner_link_clicks               INT         NOT NULL DEFAULT 0,
  visit_intent_submitted            INT         NOT NULL DEFAULT 0,
  visit_confirmed                   INT         NOT NULL DEFAULT 0,
  distinct_user_count               INT         NOT NULL DEFAULT 0,
  min_group_size_met                BOOLEAN     NOT NULL DEFAULT FALSE,
  aggregation_status                VARCHAR(20) NOT NULL DEFAULT 'complete',
  created_at                        DATETIME(3) NOT NULL,
  updated_at                        DATETIME(3) NOT NULL,
  PRIMARY KEY (metric_date, monthly_curated_destination_id),
  KEY idx_destination_metrics_region_date (region_id, metric_date),
  KEY idx_destination_metrics_city_date (city_id, metric_date),
  CONSTRAINT fk_destination_metrics_destination
    FOREIGN KEY (monthly_curated_destination_id) REFERENCES monthly_curated_destinations(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT chk_destination_metrics_non_negative
    CHECK (
      destination_impressions >= 0
      AND destination_detail_opens >= 0
      AND itinerary_generated >= 0
      AND transport_detail_opens >= 0
      AND itinerary_saved >= 0
      AND itinerary_shared_or_exported >= 0
      AND official_link_clicks >= 0
      AND partner_link_clicks >= 0
      AND visit_intent_submitted >= 0
      AND visit_confirmed >= 0
      AND distinct_user_count >= 0
    ),
  CONSTRAINT chk_destination_metrics_aggregation_status
    CHECK (aggregation_status IN ('pending', 'complete', 'suppressed', 'partial'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS admin_audit_logs (
  id                          CHAR(36)     NOT NULL,
  occurred_at                 DATETIME(3)  NOT NULL,
  actor_user_id               CHAR(36)     NULL,
  session_id                  VARCHAR(120) NULL,
  roles_snapshot              JSON         NULL,
  organization_ids_snapshot   JSON         NULL,
  region_ids_snapshot         JSON         NULL,
  action                      VARCHAR(80)  NOT NULL,
  resource_type               VARCHAR(80)  NULL,
  resource_id                 VARCHAR(120) NULL,
  result                      VARCHAR(20)  NOT NULL,
  reason_code                 VARCHAR(80)  NULL,
  request_id                  VARCHAR(120) NULL,
  before_summary_json         JSON         NULL,
  after_summary_json          JSON         NULL,
  metadata_json               JSON         NULL,
  created_at                  DATETIME(3)  NOT NULL,
  PRIMARY KEY (id),
  KEY idx_admin_audit_actor_time (actor_user_id, occurred_at),
  KEY idx_admin_audit_resource_time (resource_type, resource_id, occurred_at),
  KEY idx_admin_audit_request (request_id),
  KEY idx_admin_audit_action_result (action, result, occurred_at),
  CONSTRAINT fk_admin_audit_actor
    FOREIGN KEY (actor_user_id) REFERENCES users(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT chk_admin_audit_result
    CHECK (result IN ('allowed', 'denied', 'succeeded', 'failed'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
