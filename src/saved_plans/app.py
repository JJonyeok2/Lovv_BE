# @file src/saved_plans/app.py
# @description 사용자가 저장한 여행 일정(itinerary)의 조회, 신규 생성, 삭제 및 좋아요 반응 상태를 RDS MySQL에 기록하는 Lambda 핸들러.
# @lastModified 2026-06-23

import base64
import json
from datetime import datetime, timezone

from saved_plans.repository import (
    IdempotencyConflictError,
    RdsDataSavedPlanRepository,
    canonical_snapshot_hash,
)
from shared.auth import AuthTokenError
from shared.current_user import authenticated_claims
from shared.http import empty_response, error_response, json_response
from shared.logger import Tag, get_logger


LOGGER = get_logger(__name__)


# 대화 이력이 담긴 원시 데이터 필드는 일정 저장 시 포함할 수 없다.
RAW_HISTORY_FIELDS = {"messages", "chatHistory", "conversation", "transcript"}
# 소유권 관련 메타데이터 필드는 클라이언트가 임의로 수정할 수 없다.
FORBIDDEN_OWNER_FIELDS = {"userId", "user_id", "ownerId", "createdBy"}


class SavedPlanRequestError(Exception):
    """여행 일정 관리 API에서 발생하는 비즈니스 로직 예외"""
    def __init__(self, status_code, code, message):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def lambda_handler(event, context):
    """AWS Lambda 실행 진입점"""
    return handle_request(event or {})


def handle_request(event, repository=None):
    """API Gateway 요청을 수집하고, 인증/데이터베이스 에러를 전역 처리"""
    try:
        return _handle_request(event or {}, repository)
    except SavedPlanRequestError as error:
        return error_response(error.status_code, error.code, error.message)
    except AuthTokenError as error:
        return error_response(error.status_code, error.code, error.message)
    except Exception:
        LOGGER.exception(Tag.SYSTEM, "Unhandled saved plans API error")
        return error_response(500, "INTERNAL_ERROR", "Saved plans API is unavailable")


def _handle_request(event, repository=None):
    """HTTP Method 및 경로 조건에 맞게 세부 CRUD 동작 분기"""
    method = _event_method(event)
    path = _event_path(event)
    
    # OPTIONS preflight 요청 처리
    if method == "OPTIONS":
        return json_response(200, {})

    # GET /api/v1/itineraries/public은 인증 없이 접근 가능
    if method == "GET" and path == "/api/v1/itineraries/public":
        repository = repository or RdsDataSavedPlanRepository.from_env()
        return _list_public_plans(event, repository)

    # 1. JWT 클레임에서 사용자 식별자 획득 (퍼블릭 조회는 토큰 없어도 됨)
    user_id = _optional_user_id(event) if (method == "GET" and path.startswith("/api/v1/itineraries/")) else _current_user_id(event)
    repository = repository or RdsDataSavedPlanRepository.from_env()
    itinerary_id = _itinerary_id(event, path)

    # 2. POST /api/v1/me/itineraries: 신규 여행 일정 저장 (중복 저장은 멱등성 검사로 처리)
    if method == "POST" and path == "/api/v1/me/itineraries":
        return _save_plan(event, user_id, repository)
        
    # 3. GET /api/v1/me/itineraries: 현재 로그인 사용자의 저장 목록 조회
    if method == "GET" and path == "/api/v1/me/itineraries":
        return _list_plans(event, user_id, repository)
        
    # 4. GET /api/v1/me/itineraries/{itineraryId} or GET /api/v1/itineraries/{itineraryId}: 특정 일정 상세 조회
    if method == "GET" and itinerary_id and (path.startswith("/api/v1/me/itineraries/") or path.startswith("/api/v1/itineraries/")) and path.endswith(f"/{itinerary_id}"):
        if path.startswith("/api/v1/me/") and not user_id:
            raise SavedPlanRequestError(401, "UNAUTHORIZED", "Authentication is required")
        return _get_plan(user_id, itinerary_id, repository)
        
    # 5. DELETE /api/v1/me/itineraries/{itineraryId}: 특정 일정 영구 삭제
    if method == "DELETE" and itinerary_id and path.endswith(f"/{itinerary_id}"):
        return _delete_plan(user_id, itinerary_id, repository)
        
    # 6. PUT /api/v1/me/itineraries/{itineraryId}/reactions/like: 좋아요 설정
    if method == "PUT" and itinerary_id and path.endswith(f"/{itinerary_id}/reactions/like"):
        return _set_like(user_id, itinerary_id, True, repository)
        
    # 7. DELETE /api/v1/me/itineraries/{itineraryId}/reactions/like: 좋아요 취소
    if method == "DELETE" and itinerary_id and path.endswith(f"/{itinerary_id}/reactions/like"):
        return _set_like(user_id, itinerary_id, False, repository)

    # 8. PATCH /api/v1/me/itineraries/{itineraryId}/share: 공개 여부 변경
    if method == "PATCH" and itinerary_id and path.endswith(f"/{itinerary_id}/share"):
        return _share_plan(event, user_id, itinerary_id, repository)

    # 9. POST /api/v1/me/itineraries/{itineraryId}/clone: 타인의 공개 일정 복제
    if method == "POST" and itinerary_id and path.endswith(f"/{itinerary_id}/clone"):
        return _clone_plan(user_id, itinerary_id, repository)

    return error_response(404, "NOT_FOUND", "Route not found")


def _save_plan(event, user_id, repository):
    """신규 여행 일정 데이터 유효성 검사 후 데이터베이스 저장 (멱등키 해시 적용)"""
    payload = _validate_save_payload(_json_body(event))
    snapshot_hash = canonical_snapshot_hash(_hash_payload(payload))
    try:
        # 중복 저장 방지를 위해 멱등키 및 스냅샷 해시 비교 처리
        plan, duplicate = repository.save(user_id, payload, snapshot_hash, _now_iso())
    except IdempotencyConflictError:
        raise SavedPlanRequestError(409, "IDEMPOTENCY_KEY_CONFLICT", "Idempotency key conflicts with another payload")

    LOGGER.info(
        Tag.PLAN,
        "Itinerary saved (userId=%s, itineraryId=%s, duplicate=%s)",
        user_id,
        plan["itineraryId"],
        bool(duplicate),
    )
    return json_response(
        200 if duplicate else 201,
        _public_save_response(plan, duplicate),
    )


def _list_plans(event, user_id, repository):
    """로그인 사용자의 일정 리스트 조회 (조회 개수 제한 제한 파라미터 반영)"""
    limit = _parse_limit((event.get("queryStringParameters") or {}).get("limit"))
    return json_response(200, {"items": repository.list_by_user(user_id, limit=limit), "nextCursor": None})


def _get_plan(user_id, itinerary_id, repository):
    """사용자가 소유하거나 공개된 특정 여행 일정 상세 데이터 로드"""
    plan = repository.get_public_or_owned(user_id, itinerary_id)
    if not plan:
        raise SavedPlanRequestError(404, "ITINERARY_NOT_FOUND", "Saved itinerary was not found")
    return json_response(200, _public_detail(plan))


def _delete_plan(user_id, itinerary_id, repository):
    """지정한 여행 일정을 논리적/물리적으로 데이터베이스에서 영구 삭제"""
    result = repository.delete_owned(user_id, itinerary_id, _now_iso())
    if result == "not_found":
        raise SavedPlanRequestError(404, "ITINERARY_NOT_FOUND", "Saved itinerary was not found")
    if result == "forbidden":
        raise SavedPlanRequestError(403, "FORBIDDEN", "You cannot delete another user's saved itinerary")
    LOGGER.info(Tag.PLAN, "Itinerary deleted (userId=%s, itineraryId=%s)", user_id, itinerary_id)
    return empty_response(204)


def _set_like(user_id, itinerary_id, liked, repository):
    """특정 일정에 좋아요 설정을 하거나 해제하는 트랜잭션 수행"""
    plan, changed = repository.set_like(user_id, itinerary_id, liked, _now_iso())
    if not plan:
        raise SavedPlanRequestError(404, "ITINERARY_NOT_FOUND", "Saved itinerary was not found")
    LOGGER.info(
        Tag.PLAN,
        "Itinerary %s (userId=%s, itineraryId=%s, changed=%s)",
        "liked" if liked else "unliked",
        user_id,
        itinerary_id,
        changed,
    )
    if not liked:
        return empty_response(204)
    return json_response(
        200,
        {
            "itineraryId": itinerary_id,
            "reactionType": "like",
            "isLiked": True,
            "changed": changed,
            "updatedAt": plan.get("updatedAt"),
        },
    )


def _validate_save_payload(payload):
    """일정 저장 전 필수 속성 및 타입 검증"""
    # 1. 시스템 관리 필드나 금지된 채팅 로그가 담겨 있는지 검증
    owner_fields = FORBIDDEN_OWNER_FIELDS.intersection(payload.keys())
    if owner_fields:
        raise SavedPlanRequestError(400, "INVALID_ITINERARY_SNAPSHOT", "Owner fields are not writable")
    if RAW_HISTORY_FIELDS.intersection(payload.keys()):
        raise SavedPlanRequestError(400, "RAW_CHAT_HISTORY_NOT_ALLOWED", "Raw chat history cannot be saved")
        
    # 2. 제목, 추천 ID, 목적지, 기간 정보 필수값 체크
    if not _non_empty_string(payload.get("title")):
        raise SavedPlanRequestError(400, "INVALID_ITINERARY_SNAPSHOT", "title is required")
    if not _non_empty_string(payload.get("sourceRecommendationId")):
        raise SavedPlanRequestError(400, "INVALID_ITINERARY_SNAPSHOT", "sourceRecommendationId is required")
    if not isinstance(payload.get("destination"), dict) or not _non_empty_string(payload["destination"].get("destinationId")):
        raise SavedPlanRequestError(400, "INVALID_ITINERARY_SNAPSHOT", "destination is required")
    if not _non_empty_string(payload.get("durationLabel")):
        raise SavedPlanRequestError(400, "INVALID_ITINERARY_SNAPSHOT", "durationLabel is required")
        
    # 3. 일자(days)와 장소 아이템들의 존재 조건 검사
    itinerary = payload.get("itinerary")
    if not isinstance(itinerary, dict) or not isinstance(itinerary.get("days"), list) or not itinerary["days"]:
        raise SavedPlanRequestError(400, "INVALID_ITINERARY_SNAPSHOT", "itinerary days are required")
    if not any(isinstance(day, dict) and _day_entries(day) for day in itinerary["days"]):
        raise SavedPlanRequestError(400, "INVALID_ITINERARY_SNAPSHOT", "itinerary items are required")
    return payload


def _public_detail(plan):
    """일정 상세 조회 결과를 프론트엔드용 표준 응답 구조로 정규화"""
    return {
        "itineraryId": plan.get("itineraryId"),
        "sourceRecommendationId": plan.get("sourceRecommendationId"),
        "userId": plan.get("userId"),
        "ownerId": plan.get("userId"),
        "title": plan.get("title"),
        "summary": plan.get("summary"),
        "destination": plan.get("destination") or {},
        "tripType": plan.get("tripType"),
        "durationLabel": plan.get("durationLabel"),
        "themes": plan.get("themes") or [],
        "festivalChoice": plan.get("festivalChoice"),
        "festivalThemeLabel": plan.get("festivalThemeLabel"),
        "intensityLabel": plan.get("intensityLabel"),
        "conditionsSnapshot": plan.get("conditionsSnapshot") or {},
        "requestSummary": plan.get("requestSummary"),
        "itinerary": plan.get("itinerary") or {},
        "alternativeItinerary": plan.get("alternativeItinerary"),
        "isLiked": bool(plan.get("isLiked")),
        "isPublic": bool(plan.get("isPublic")),
        "copiedFromItineraryId": plan.get("copiedFromItineraryId"),
        "savedAt": plan.get("savedAt"),
        "updatedAt": plan.get("updatedAt"),
    }


def _public_save_response(plan, duplicate):
    response = _public_detail(plan)
    response["duplicate"] = bool(duplicate)
    return response


def _hash_payload(payload):
    """멱등키(idempotencyKey) 필드를 제외한 순수 페이로드 필드만 추출하여 해싱 대상 빌드"""
    return {
        key: value
        for key, value in payload.items()
        if key not in {"idempotencyKey"}
    }


def _parse_limit(value):
    """목록 조회 시 최대 결과 수(Limit) 검증 및 범위 제한"""
    if value in (None, ""):
        return 20
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise SavedPlanRequestError(400, "VALIDATION_ERROR", "limit must be a positive integer")
    if parsed < 1:
        raise SavedPlanRequestError(400, "VALIDATION_ERROR", "limit must be a positive integer")
    return min(parsed, 50)


def _current_user_id(event):
    """API Gateway 인증 결과 클레임에서 사용자 고유 ID 추출"""
    claims = authenticated_claims(event)
    user_id = claims.get("userId") or claims.get("sub")
    if not user_id:
        raise SavedPlanRequestError(401, "UNAUTHORIZED", "Authentication is required")
    return user_id


def _itinerary_id(event, path):
    """경로 파라미터 또는 rawPath 주소에서 저장 일정 ID(itineraryId)를 추출"""
    path_parameters = event.get("pathParameters") or {}
    if path_parameters.get("itineraryId"):
        return path_parameters["itineraryId"]
    for prefix in ("/api/v1/me/itineraries/", "/api/v1/itineraries/"):
        if path.startswith(prefix):
            remainder = path[len(prefix) :]
            return remainder.split("/", 1)[0]
    return None


def _json_body(event):
    """HTTP 요청 바디 디코딩 및 JSON 데이터 파싱"""
    raw_body = event.get("body")
    if raw_body in (None, ""):
        return {}
    if event.get("isBase64Encoded"):
        try:
            raw_body = base64.b64decode(raw_body).decode("utf-8")
        except Exception:
            raise SavedPlanRequestError(400, "INVALID_JSON", "Request body must be valid JSON")
    try:
        parsed = json.loads(raw_body)
    except json.JSONDecodeError:
        raise SavedPlanRequestError(400, "INVALID_JSON", "Request body must be valid JSON")
    if not isinstance(parsed, dict):
        raise SavedPlanRequestError(400, "INVALID_ITINERARY_SNAPSHOT", "Request body must be a JSON object")
    return parsed


def _event_method(event):
    """요청 메소드(HTTP Method) 추출"""
    return (((event.get("requestContext") or {}).get("http") or {}).get("method") or event.get("httpMethod") or "").upper()


def _event_path(event):
    """요청 URL 경로 추출"""
    return event.get("rawPath") or event.get("path") or ""


def _non_empty_string(value):
    """공백이 아닌 올바른 문자열 유형인지 검증"""
    return isinstance(value, str) and bool(value.strip())


def _day_entries(day):
    """특정 일자(Day)의 코스 장소 목록(items 또는 stops)을 추출"""
    items = day.get("items")
    stops = day.get("stops")
    if isinstance(items, list) and items:
        return items
    if isinstance(stops, list) and stops:
        return stops
    return []


def _now_iso():
    """현재 시각을 UTC 기준 ISO 8601 포맷으로 변환"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _share_plan(event, user_id, itinerary_id, repository):
    """일정의 공개 여부 상태를 변경"""
    body = _json_body(event)
    is_public = body.get("isPublic")
    if not isinstance(is_public, bool):
        raise SavedPlanRequestError(400, "VALIDATION_ERROR", "isPublic must be a boolean")
    plan = repository.update_share_status(user_id, itinerary_id, is_public, _now_iso())
    if not plan:
        raise SavedPlanRequestError(404, "ITINERARY_NOT_FOUND", "Saved itinerary was not found")
    return json_response(200, _public_detail(plan))


def _clone_plan(user_id, itinerary_id, repository):
    """타인의 공개 일정을 내 마이페이지에 저장하기 위해 복제"""
    plan = repository.clone_itinerary(user_id, itinerary_id, _now_iso())
    if not plan:
        raise SavedPlanRequestError(404, "ITINERARY_NOT_FOUND", "Source public itinerary was not found")
    LOGGER.info(
        Tag.PLAN,
        "Itinerary cloned (userId=%s, sourceId=%s, newId=%s)",
        user_id,
        itinerary_id,
        plan["itineraryId"],
    )
    return json_response(201, _public_detail(plan))


def _list_public_plans(event, repository):
    """모든 공개 일정을 리스트 형태로 조회"""
    user_id = _optional_user_id(event)
    limit = _parse_limit((event.get("queryStringParameters") or {}).get("limit"))
    items = repository.list_public(limit=limit, user_id=user_id)
    return json_response(200, {"items": items, "nextCursor": None})


def _optional_user_id(event):
    """토큰이 유효하면 사용자 ID를 추출하고, 유효하지 않거나 없으면 None 반환"""
    try:
        return _current_user_id(event)
    except Exception:
        return None


# EOF: src/saved_plans/app.py
