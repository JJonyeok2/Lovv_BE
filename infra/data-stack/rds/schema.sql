-- Lovv Data Stack RDS 스키마
-- 기준 문서: docs/SPEC/db_build_spec.md v0.1
-- 서비스 API 보강 기준: docs/SPEC/service_api_schema_extension_spec.md v0.1
-- 대상 DB: MySQL 8, utf8mb4, utf8mb4_0900_ai_ci

-- 1) users: 사용자 프로필 및 Auth 상태 원장
CREATE TABLE users (
  id             CHAR(36)     NOT NULL,
  email          VARCHAR(255) NULL,
  email_verified BOOLEAN      NOT NULL DEFAULT false,
  display_name   VARCHAR(80)  NULL,
  nickname       VARCHAR(80)  NULL,
  avatar_url     VARCHAR(500) NULL,
  status         VARCHAR(30)  NOT NULL DEFAULT 'active',
  role           VARCHAR(30)  NOT NULL DEFAULT 'user',
  last_login_at  DATETIME     NULL,
  created_at     DATETIME     NOT NULL,
  updated_at     DATETIME     NOT NULL,
  deleted_at     DATETIME     NULL,
  PRIMARY KEY (id),
  KEY idx_users_email (email),
  KEY idx_users_status (status),
  KEY idx_users_deleted_at (deleted_at),
  CONSTRAINT chk_users_status
    CHECK (status IN ('active', 'suspended', 'withdrawn')),
  CONSTRAINT chk_users_role
    CHECK (role IN ('user', 'admin', 'system'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 2) social_accounts: 소셜 로그인 제공자 계정 연결
CREATE TABLE social_accounts (
  id                         CHAR(36)     NOT NULL,
  user_id                    CHAR(36)     NOT NULL,
  provider                   VARCHAR(30)  NOT NULL,
  provider_user_id           VARCHAR(255) NOT NULL,
  email                      VARCHAR(255) NULL,
  email_verified             BOOLEAN      NOT NULL DEFAULT false,
  provider_nickname          VARCHAR(80)  NULL,
  provider_profile_image_url VARCHAR(500) NULL,
  last_login_at              DATETIME     NULL,
  created_at                 DATETIME     NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_social_provider_user (provider, provider_user_id),
  KEY idx_social_user (user_id),
  CONSTRAINT fk_social_user
    FOREIGN KEY (user_id) REFERENCES users (id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 3) user_preferences: 온보딩 및 마이페이지 취향 원장
CREATE TABLE user_preferences (
  id                   CHAR(36)    NOT NULL,
  user_id              CHAR(36)    NOT NULL,
  country_track        VARCHAR(30) NOT NULL,
  mapped_themes        JSON        NULL,
  preferred_regions    JSON        NULL,
  selected_city_style  VARCHAR(50) NULL,
  pace                 VARCHAR(30) NULL,
  trip_days            INT         NULL,
  companion_style      VARCHAR(50) NULL,
  travel_styles        JSON        NULL,
  onboarding_completed BOOLEAN     NOT NULL DEFAULT false,
  created_at           DATETIME    NOT NULL,
  updated_at           DATETIME    NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_user_preferences_user (user_id),
  KEY idx_user_preferences_country (country_track),
  CONSTRAINT fk_user_preferences_user
    FOREIGN KEY (user_id) REFERENCES users (id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT chk_user_preferences_trip_days
    CHECK (trip_days IS NULL OR trip_days > 0),
  CONSTRAINT chk_user_preferences_country
    CHECK (country_track IN ('KR', 'JP', 'BOTH'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 4) itineraries: 사용자가 저장한 최종 여행 일정
CREATE TABLE itineraries (
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
  saved_at                     DATETIME     NOT NULL,
  created_at                   DATETIME     NOT NULL,
  updated_at                   DATETIME     NOT NULL,
  deleted_at                   DATETIME     NULL,
  PRIMARY KEY (id),
  KEY idx_itinerary_user_saved (user_id, saved_at DESC),
  KEY idx_itinerary_user_deleted_saved (user_id, deleted_at, saved_at DESC),
  KEY idx_itinerary_source_recommendation (source_recommendation_id),
  UNIQUE KEY uq_itinerary_user_idempotency (user_id, idempotency_key),
  UNIQUE KEY uq_itinerary_user_source_snapshot (user_id, source_recommendation_id, snapshot_hash),
  CONSTRAINT fk_itinerary_user
    FOREIGN KEY (user_id) REFERENCES users (id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 5) itinerary_items: 일정 내 세부 장소와 방문 순서
CREATE TABLE itinerary_items (
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
  CONSTRAINT fk_item_itinerary
    FOREIGN KEY (itinerary_id) REFERENCES itineraries (id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT chk_item_day_index
    CHECK (day_index > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 6) plan_reactions: 저장 일정에 대한 사용자 반응
CREATE TABLE plan_reactions (
  id            CHAR(36)    NOT NULL,
  user_id       CHAR(36)    NOT NULL,
  itinerary_id  CHAR(36)    NOT NULL,
  reaction_type VARCHAR(30) NOT NULL,
  created_at    DATETIME    NOT NULL,
  updated_at    DATETIME    NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_plan_reaction_user_itinerary (user_id, itinerary_id),
  KEY idx_reaction_user (user_id, created_at DESC),
  KEY idx_reaction_itinerary (itinerary_id, created_at),
  CONSTRAINT fk_reaction_user
    FOREIGN KEY (user_id) REFERENCES users (id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_reaction_itinerary
    FOREIGN KEY (itinerary_id) REFERENCES itineraries (id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT chk_plan_reaction_type
    CHECK (reaction_type IN ('like', 'dislike'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
