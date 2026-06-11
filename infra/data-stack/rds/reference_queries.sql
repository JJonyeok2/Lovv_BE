-- Lovv Data Stack RDS 참조 쿼리
-- 기준 문서: docs/PRD/db_build_prd.md section 3.4
-- 서비스 API 보강 기준: docs/SPEC/service_api_schema_extension_spec.md v0.1
-- :param 형태의 값은 애플리케이션 코드에서 바인딩 변수로 치환한다.

-- A. 소셜 로그인 식별: provider 계정으로 내부 사용자 프로필을 조회한다.
SELECT u.id, u.email, u.email_verified, u.display_name, u.nickname,
       u.avatar_url, u.status, u.role, u.deleted_at
FROM social_accounts s
JOIN users u ON u.id = s.user_id
WHERE s.provider = :provider
  AND s.provider_user_id = :provider_user_id
  AND u.deleted_at IS NULL;

-- B. 로그인 성공 시각 갱신: 사용자와 provider 연결의 마지막 로그인 시각을 함께 기록한다.
UPDATE users
SET last_login_at = :now,
    updated_at = :now
WHERE id = :user_id
  AND deleted_at IS NULL;

UPDATE social_accounts
SET email = :provider_email,
    email_verified = :provider_email_verified,
    provider_nickname = :provider_nickname,
    provider_profile_image_url = :provider_profile_image_url,
    last_login_at = :now
WHERE provider = :provider
  AND provider_user_id = :provider_user_id;

-- C. 취향 upsert: 사용자별 취향은 1개 row만 유지한다.
INSERT INTO user_preferences (
  id, user_id, country_track, mapped_themes, preferred_regions,
  selected_city_style, pace, trip_days, companion_style, travel_styles,
  onboarding_completed, created_at, updated_at
) VALUES (
  :id, :user_id, :country_track, :mapped_themes, :preferred_regions,
  :selected_city_style, :pace, :trip_days, :companion_style, :travel_styles,
  :onboarding_completed, :now, :now
)
ON DUPLICATE KEY UPDATE
  country_track = VALUES(country_track),
  mapped_themes = VALUES(mapped_themes),
  preferred_regions = VALUES(preferred_regions),
  selected_city_style = VALUES(selected_city_style),
  pace = VALUES(pace),
  trip_days = VALUES(trip_days),
  companion_style = VALUES(companion_style),
  travel_styles = VALUES(travel_styles),
  onboarding_completed = VALUES(onboarding_completed),
  updated_at = VALUES(updated_at);

-- D. 취향 조회: 로그인 후 또는 마이페이지 진입 시 현재 취향을 로드한다.
SELECT id, user_id, country_track, mapped_themes, preferred_regions,
       selected_city_style, pace, trip_days, companion_style, travel_styles,
       onboarding_completed, created_at, updated_at
FROM user_preferences
WHERE user_id = :user_id;

-- E. 저장 일정 생성: idempotency_key 또는 recommendation snapshot 중복을 DB 제약으로 방지한다.
INSERT INTO itineraries (
  id, user_id, title, summary, duration_label, festival_choice, intensity_label,
  preference_snapshot, request_summary, source_recommendation_id, idempotency_key,
  snapshot_hash, destination_json, trip_type, themes_json, conditions_snapshot_json,
  alternative_itinerary_json, saved_at, created_at, updated_at
) VALUES (
  :id, :user_id, :title, :summary, :duration_label, :festival_choice, :intensity_label,
  :preference_snapshot, :request_summary, :source_recommendation_id, :idempotency_key,
  :snapshot_hash, :destination_json, :trip_type, :themes_json,
  :conditions_snapshot_json, :alternative_itinerary_json,
  :now, :now, :now
);

-- F. 마이페이지 저장 일정 목록: soft-delete되지 않은 사용자별 저장 일정을 최신순으로 조회한다.
SELECT id, title, summary, duration_label, intensity_label,
       source_recommendation_id, destination_json, trip_type, saved_at, updated_at
FROM itineraries
WHERE user_id = :user_id
  AND deleted_at IS NULL
ORDER BY saved_at DESC
LIMIT :limit OFFSET :offset;

-- G. 일정 상세: 일정 원장과 세부 방문 항목을 일차/방문 순서대로 조회한다.
SELECT i.id AS itinerary_id, i.title, i.summary, i.preference_snapshot,
       i.destination_json, i.themes_json, i.conditions_snapshot_json,
       it.day_index, it.sort_order, it.time_slot, it.place_name, it.content_id,
       it.place_id, it.latitude, it.longitude, it.move_hint,
       it.recommendation_reason, it.body, it.source_badges
FROM itineraries i
JOIN itinerary_items it ON it.itinerary_id = i.id
WHERE i.id = :itinerary_id
  AND i.user_id = :user_id
  AND i.deleted_at IS NULL
ORDER BY it.day_index ASC, it.sort_order ASC;

-- H. 일정 반응 토글: 사용자 1명 + 일정 1개에는 하나의 reaction row만 유지한다.
INSERT INTO plan_reactions (id, user_id, itinerary_id, reaction_type, created_at, updated_at)
VALUES (:id, :user_id, :itinerary_id, :reaction_type, :now, :now)
ON DUPLICATE KEY UPDATE
  reaction_type = VALUES(reaction_type),
  updated_at = VALUES(updated_at);

-- I. 일정별 반응 집계: reaction_type별 카운트를 계산한다.
SELECT reaction_type, COUNT(*) AS cnt
FROM plan_reactions
WHERE itinerary_id = :itinerary_id
GROUP BY reaction_type;

-- J. 저장 일정 soft delete: 목록에서는 제외하고 원장 row는 보존한다.
UPDATE itineraries
SET deleted_at = :now,
    updated_at = :now
WHERE id = :itinerary_id
  AND user_id = :user_id
  AND deleted_at IS NULL;

-- K. 제약 추가 전 중복 점검: 운영/공유 dev DB에 적용하기 전에 반드시 확인한다.
SELECT user_id, itinerary_id, COUNT(*) AS reaction_count
FROM plan_reactions
GROUP BY user_id, itinerary_id
HAVING COUNT(*) > 1;

SELECT user_id, idempotency_key, COUNT(*) AS duplicate_count
FROM itineraries
WHERE idempotency_key IS NOT NULL
GROUP BY user_id, idempotency_key
HAVING COUNT(*) > 1;

SELECT user_id, source_recommendation_id, snapshot_hash, COUNT(*) AS duplicate_count
FROM itineraries
WHERE source_recommendation_id IS NOT NULL
  AND snapshot_hash IS NOT NULL
GROUP BY user_id, source_recommendation_id, snapshot_hash
HAVING COUNT(*) > 1;
