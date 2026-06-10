-- Lovv Data Stack RDS 참조 쿼리
-- 기준 문서: docs/PRD/db_build_prd.md section 3.4
-- :param 형태의 값은 애플리케이션 코드에서 바인딩 변수로 치환한다.

-- A. 소셜 로그인 식별: provider 계정으로 내부 사용자 프로필을 조회한다.
SELECT u.id, u.email, u.display_name, u.avatar_url
FROM social_accounts s
JOIN users u ON u.id = s.user_id
WHERE s.provider = :provider AND s.provider_user_id = :provider_user_id;

-- B. 마이페이지 저장 일정 목록: 사용자별 저장 일정을 최신순으로 조회한다.
SELECT id, title, summary, duration_label, intensity_label, saved_at
FROM itineraries
WHERE user_id = :user_id
ORDER BY saved_at DESC
LIMIT :limit OFFSET :offset;

-- C. 일정 상세: 일정 원장과 세부 방문 항목을 방문 순서대로 조회한다.
SELECT i.id AS itinerary_id, i.title, i.summary, i.preference_snapshot,
       it.sort_order, it.time_slot, it.place_name, it.move_hint, it.recommendation_reason
FROM itineraries i
JOIN itinerary_items it ON it.itinerary_id = i.id
WHERE i.id = :itinerary_id AND i.user_id = :user_id
ORDER BY it.sort_order ASC;

-- D. 일정 반응 등록: 사용자의 like/dislike 등 반응을 저장한다.
INSERT INTO plan_reactions (id, user_id, itinerary_id, reaction_type, created_at)
VALUES (:id, :user_id, :itinerary_id, :reaction_type, :now);

-- E. 일정별 반응 집계: reaction_type별 카운트를 계산한다.
SELECT reaction_type, COUNT(*) AS cnt
FROM plan_reactions
WHERE itinerary_id = :itinerary_id
GROUP BY reaction_type;

-- F. 저장 일정 삭제: itinerary_items와 plan_reactions는 FK cascade로 함께 삭제된다.
DELETE FROM itineraries WHERE id = :itinerary_id AND user_id = :user_id;
