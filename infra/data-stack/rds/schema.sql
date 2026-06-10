-- Lovv Data Stack RDS 스키마
-- 기준 문서: docs/SPEC/db_build_spec.md v0.1
-- 대상 DB: MySQL 8, utf8mb4, utf8mb4_0900_ai_ci

-- 1) users: 사용자 프로필 원장
CREATE TABLE users (
  id           CHAR(36)     NOT NULL,
  email        VARCHAR(255) NULL,
  display_name VARCHAR(80)  NOT NULL,
  avatar_url   VARCHAR(500) NULL,
  created_at   DATETIME     NOT NULL,
  PRIMARY KEY (id),
  KEY idx_users_email (email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 2) social_accounts: 소셜 로그인 제공자 계정 연결
CREATE TABLE social_accounts (
  id               CHAR(36)     NOT NULL,
  user_id          CHAR(36)     NOT NULL,
  provider         VARCHAR(30)  NOT NULL,
  provider_user_id VARCHAR(255) NOT NULL,
  created_at       DATETIME     NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_social_provider_user (provider, provider_user_id),
  KEY idx_social_user (user_id),
  CONSTRAINT fk_social_user
    FOREIGN KEY (user_id) REFERENCES users (id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 3) itineraries: 사용자가 저장한 최종 여행 일정
CREATE TABLE itineraries (
  id                  CHAR(36)     NOT NULL,
  user_id             CHAR(36)     NOT NULL,
  title               VARCHAR(160) NOT NULL,
  summary             TEXT         NULL,
  duration_label      VARCHAR(40)  NOT NULL,
  festival_choice     VARCHAR(80)  NULL,
  intensity_label     VARCHAR(40)  NULL,
  preference_snapshot JSON         NULL,
  request_summary     TEXT         NULL,
  saved_at            DATETIME     NOT NULL,
  created_at          DATETIME     NOT NULL,
  PRIMARY KEY (id),
  KEY idx_itinerary_user_saved (user_id, saved_at DESC),
  CONSTRAINT fk_itinerary_user
    FOREIGN KEY (user_id) REFERENCES users (id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 4) itinerary_items: 일정 내 세부 장소와 방문 순서
CREATE TABLE itinerary_items (
  id                    CHAR(36)     NOT NULL,
  itinerary_id          CHAR(36)     NOT NULL,
  sort_order            INT          NOT NULL,
  time_slot             VARCHAR(40)  NULL,
  place_name            VARCHAR(160) NOT NULL,
  move_hint             VARCHAR(255) NULL,
  recommendation_reason TEXT         NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_item_order (itinerary_id, sort_order),
  CONSTRAINT fk_item_itinerary
    FOREIGN KEY (itinerary_id) REFERENCES itineraries (id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 5) plan_reactions: 저장 일정에 대한 사용자 반응
CREATE TABLE plan_reactions (
  id            CHAR(36)    NOT NULL,
  user_id       CHAR(36)    NOT NULL,
  itinerary_id  CHAR(36)    NOT NULL,
  reaction_type VARCHAR(30) NOT NULL,
  created_at    DATETIME    NOT NULL,
  PRIMARY KEY (id),
  KEY idx_reaction_user (user_id, created_at DESC),
  KEY idx_reaction_itinerary (itinerary_id, created_at),
  CONSTRAINT fk_reaction_user
    FOREIGN KEY (user_id) REFERENCES users (id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_reaction_itinerary
    FOREIGN KEY (itinerary_id) REFERENCES itineraries (id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
