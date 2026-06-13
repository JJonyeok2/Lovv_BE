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

CREATE TABLE IF NOT EXISTS itineraries (
  id                           CHAR(36)     NOT NULL,
  user_id                      CHAR(36)     NOT NULL,
  title                        VARCHAR(160) NULL,
  summary                      TEXT         NULL,
  duration_label               VARCHAR(40)  NULL,
  festival_choice              VARCHAR(80)  NULL,
  intensity_label              VARCHAR(40)  NULL,
  preference_snapshot          JSON         NULL,
  request_summary              TEXT         NULL,
  source_recommendation_id     VARCHAR(80)  NULL,
  idempotency_key              VARCHAR(120) NULL,
  snapshot_hash                CHAR(64)     NULL,
  destination_json             JSON         NULL,
  trip_type                    VARCHAR(50)  NULL,
  themes_json                  JSON         NULL,
  conditions_snapshot_json     JSON         NULL,
  alternative_itinerary_json   JSON         NULL,
  saved_at                     DATETIME(3)  NOT NULL,
  created_at                   DATETIME(3)  NOT NULL,
  updated_at                   DATETIME(3)  NOT NULL,
  deleted_at                   DATETIME(3)  NULL,
  PRIMARY KEY (id),
  KEY idx_itinerary_user_saved (user_id, saved_at DESC),
  KEY idx_itinerary_user_deleted_saved (user_id, deleted_at, saved_at DESC),
  KEY idx_itinerary_source_recommendation (source_recommendation_id),
  UNIQUE KEY uq_itinerary_user_idempotency (user_id, idempotency_key),
  UNIQUE KEY uq_itinerary_user_source_snapshot (user_id, source_recommendation_id, snapshot_hash),
  CONSTRAINT fk_itinerary_user FOREIGN KEY (user_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS itinerary_items (
  id                    CHAR(36)      NOT NULL,
  itinerary_id          CHAR(36)      NOT NULL,
  day_index             INT           NOT NULL,
  sort_order            INT           NOT NULL,
  time_slot             VARCHAR(40)   NULL,
  place_name            VARCHAR(160)  NULL,
  content_id            VARCHAR(80)   NULL,
  place_id              VARCHAR(120)  NULL,
  latitude              DECIMAL(10,7) NULL,
  longitude             DECIMAL(10,7) NULL,
  move_hint             VARCHAR(255)  NULL,
  recommendation_reason TEXT          NULL,
  body                  TEXT          NULL,
  source_badges         JSON          NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_item_day_order (itinerary_id, day_index, sort_order),
  KEY idx_item_content (content_id),
  KEY idx_item_place (place_id),
  CONSTRAINT fk_item_itinerary FOREIGN KEY (itinerary_id) REFERENCES itineraries(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT chk_item_day_index CHECK (day_index > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS plan_reactions (
  id            CHAR(36)    NOT NULL,
  user_id       CHAR(36)    NOT NULL,
  itinerary_id  CHAR(36)    NOT NULL,
  reaction_type VARCHAR(30) NOT NULL,
  created_at    DATETIME(3) NOT NULL,
  updated_at    DATETIME(3) NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_plan_reaction_user_itinerary (user_id, itinerary_id),
  KEY idx_reaction_user (user_id, created_at DESC),
  KEY idx_reaction_itinerary (itinerary_id, created_at),
  CONSTRAINT fk_reaction_user FOREIGN KEY (user_id) REFERENCES users(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_reaction_itinerary FOREIGN KEY (itinerary_id) REFERENCES itineraries(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT chk_plan_reaction_type CHECK (reaction_type IN ('like', 'dislike'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
