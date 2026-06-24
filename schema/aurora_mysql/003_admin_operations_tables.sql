-- Lovv Admin Console operations tables
-- Step 16: notices and recommendation policy management.

CREATE TABLE IF NOT EXISTS admin_notices (
  id              CHAR(36)      NOT NULL,
  title           VARCHAR(160)  NOT NULL,
  body            TEXT          NOT NULL,
  audience        VARCHAR(30)   NOT NULL DEFAULT 'all',
  severity        VARCHAR(20)   NOT NULL DEFAULT 'info',
  status          VARCHAR(20)   NOT NULL DEFAULT 'draft',
  starts_at       DATETIME(3)   NULL,
  ends_at         DATETIME(3)   NULL,
  created_by      CHAR(36)      NULL,
  published_by    CHAR(36)      NULL,
  published_at    DATETIME(3)   NULL,
  archived_at     DATETIME(3)   NULL,
  created_at      DATETIME(3)   NOT NULL,
  updated_at      DATETIME(3)   NOT NULL,
  PRIMARY KEY (id),
  KEY idx_admin_notice_status_updated (status, updated_at),
  KEY idx_admin_notice_audience_status (audience, status, updated_at),
  KEY idx_admin_notice_created_by (created_by, created_at),
  CONSTRAINT fk_admin_notice_created_by
    FOREIGN KEY (created_by) REFERENCES users(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_admin_notice_published_by
    FOREIGN KEY (published_by) REFERENCES users(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT chk_admin_notice_audience
    CHECK (audience IN ('all', 'traveler', 'local_operator', 'data_provider', 'admin')),
  CONSTRAINT chk_admin_notice_severity
    CHECK (severity IN ('info', 'warning', 'critical')),
  CONSTRAINT chk_admin_notice_status
    CHECK (status IN ('draft', 'published', 'archived')),
  CONSTRAINT chk_admin_notice_valid_window
    CHECK (ends_at IS NULL OR starts_at IS NULL OR ends_at > starts_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS admin_recommendation_policies (
  id                CHAR(36)      NOT NULL,
  policy_key        VARCHAR(80)   NOT NULL,
  title             VARCHAR(160)  NOT NULL,
  description       TEXT          NULL,
  rules_json        JSON          NOT NULL,
  priority          INT           NOT NULL DEFAULT 0,
  status            VARCHAR(20)   NOT NULL DEFAULT 'draft',
  effective_from    DATETIME(3)   NULL,
  effective_until   DATETIME(3)   NULL,
  created_by        CHAR(36)      NULL,
  activated_by      CHAR(36)      NULL,
  activated_at      DATETIME(3)   NULL,
  archived_at       DATETIME(3)   NULL,
  created_at        DATETIME(3)   NOT NULL,
  updated_at        DATETIME(3)   NOT NULL,
  PRIMARY KEY (id),
  KEY idx_admin_policy_status_priority (status, priority, updated_at),
  KEY idx_admin_policy_key_status (policy_key, status, updated_at),
  KEY idx_admin_policy_created_by (created_by, created_at),
  CONSTRAINT fk_admin_policy_created_by
    FOREIGN KEY (created_by) REFERENCES users(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_admin_policy_activated_by
    FOREIGN KEY (activated_by) REFERENCES users(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT chk_admin_policy_status
    CHECK (status IN ('draft', 'active', 'archived')),
  CONSTRAINT chk_admin_policy_priority
    CHECK (priority >= 0),
  CONSTRAINT chk_admin_policy_valid_window
    CHECK (effective_until IS NULL OR effective_from IS NULL OR effective_until > effective_from)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
