-- Lovv product API Aurora MySQL baseline.
-- Apply only after the Aurora cluster/database is selected for this backend.
-- Tourism detail content is intentionally excluded from Aurora in this scope.
-- Read attractions, festivals, and visitor statistics from S3 raw city JSON:
-- s3://lovv-data-pipeline-dev-925273580929/raw/KR/details/20260609/

CREATE TABLE IF NOT EXISTS users (
  id CHAR(36) PRIMARY KEY,
  email VARCHAR(255) NULL,
  email_verified BOOLEAN NOT NULL DEFAULT FALSE,
  display_name VARCHAR(80) NOT NULL,
  nickname VARCHAR(80) NULL,
  avatar_url TEXT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'active',
  last_login_at DATETIME(3) NULL,
  created_at DATETIME(3) NOT NULL,
  updated_at DATETIME(3) NOT NULL,
  deleted_at DATETIME(3) NULL,
  UNIQUE KEY uq_users_email (email),
  KEY idx_users_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS social_accounts (
  id CHAR(36) PRIMARY KEY,
  user_id CHAR(36) NOT NULL,
  provider VARCHAR(20) NOT NULL,
  provider_user_id VARCHAR(255) NOT NULL,
  email VARCHAR(255) NULL,
  email_verified BOOLEAN NOT NULL DEFAULT FALSE,
  provider_nickname VARCHAR(120) NULL,
  provider_profile_image_url TEXT NULL,
  created_at DATETIME(3) NOT NULL,
  last_login_at DATETIME(3) NULL,
  UNIQUE KEY uq_social_accounts_provider_user (provider, provider_user_id),
  KEY idx_social_accounts_user (user_id),
  CONSTRAINT fk_social_accounts_user FOREIGN KEY (user_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS preferences (
  id CHAR(36) PRIMARY KEY,
  user_id CHAR(36) NOT NULL,
  country_track VARCHAR(10) NOT NULL,
  mapped_themes JSON NOT NULL,
  preferred_regions JSON NOT NULL,
  selected_city_style VARCHAR(80) NULL,
  pace VARCHAR(20) NULL,
  trip_days INT NULL,
  companion_style VARCHAR(80) NULL,
  travel_styles JSON NOT NULL,
  onboarding_completed BOOLEAN NOT NULL DEFAULT TRUE,
  created_at DATETIME(3) NOT NULL,
  updated_at DATETIME(3) NOT NULL,
  UNIQUE KEY uq_preferences_user (user_id),
  CONSTRAINT fk_preferences_user FOREIGN KEY (user_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS saved_plans (
  id CHAR(36) PRIMARY KEY,
  user_id CHAR(36) NOT NULL,
  source_recommendation_id VARCHAR(80) NULL,
  idempotency_key VARCHAR(120) NULL,
  snapshot_hash CHAR(64) NOT NULL,
  title VARCHAR(160) NOT NULL,
  summary TEXT NULL,
  destination_json JSON NOT NULL,
  trip_type VARCHAR(20) NULL,
  duration_label VARCHAR(40) NULL,
  themes_json JSON NOT NULL,
  conditions_snapshot_json JSON NOT NULL,
  request_summary TEXT NULL,
  itinerary_json JSON NOT NULL,
  alternative_itinerary_json JSON NULL,
  is_liked BOOLEAN NOT NULL DEFAULT FALSE,
  saved_at DATETIME(3) NOT NULL,
  updated_at DATETIME(3) NOT NULL,
  deleted_at DATETIME(3) NULL,
  UNIQUE KEY uq_saved_plans_idempotency (user_id, idempotency_key),
  UNIQUE KEY uq_saved_plans_recommendation_hash (user_id, source_recommendation_id, snapshot_hash),
  KEY idx_saved_plans_user_saved_at (user_id, saved_at DESC),
  CONSTRAINT fk_saved_plans_user FOREIGN KEY (user_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
