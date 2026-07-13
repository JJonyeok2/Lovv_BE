# @file src/agentcore/app.py
# @description AWS Bedrock Agent를 활용한 AI 기반 소도시 일정 생성 및 대화 인터페이스 핵심 Lambda 핸들러.
# @lastModified 2026-06-23

import base64
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone

from agentcore import routing
from shared.http import error_response, json_response
from shared.logger import Tag, get_logger


# 지원하는 진입 채널 타입. map_marker/chat/home_recommendation은 기존 API 호환 입력이며
# Runtime V2에는 create/modify/clarify/confirm envelope로 변환해 전달한다.
ENTRY_TYPES = {"create", "modify", "clarify", "confirm", "map_marker", "chat", "home_recommendation"}
# 지원하는 국가 코드
COUNTRIES = {"KR", "JP"}
# 여행 기간 유형 정의 (당일치기, 1박2일 등)
TRIP_TYPES = {"daytrip", "2d1n", "3d2n", "4d3n", "5d4n"}
THEME_IDS = {"sea_coast", "nature_trekking", "history_tradition", "art_sense", "healing_rest", "food_local"}
FRONTEND_THEME_LABEL_TO_THEME_ID = {
    "바다·해안": "sea_coast",
    "자연·트레킹": "nature_trekking",
    "역사·전통": "history_tradition",
    "예술·감성": "art_sense",
    "온천·휴양": "healing_rest",
    "미식·노포": "food_local",
}
LOGGER = get_logger(__name__)


class AgentCoreRequestError(Exception):
    """요청 검증 및 내부 처리 오류 시 발생하는 예외 클래스"""
    def __init__(self, status_code, code, message):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def lambda_handler(event, context):
    """AWS Lambda 실행 진입점"""
    return handle_request(event or {})


def handle_request(event):
    """API Gateway/Function URL 요청 진입로 및 에러 핸들링 구조"""
    try:
        return _handle_request(event or {})
    except AgentCoreRequestError as error:
        # 검증 오류 등 커스텀 에러 처리
        return error_response(error.status_code, error.code, error.message, event=event)
    except Exception:
        # 예상하지 못한 서버 오류 처리
        return error_response(500, "INTERNAL_ERROR", "Recommendation API is unavailable", event=event)


def _handle_request(event):
    """요청 메소드/경로 검사 및 요청 페이로드 검증"""
    method = _event_method(event)
    path = _event_path(event)
    
    # OPTIONS preflight 요청 지원
    if method == "OPTIONS":
        return json_response(200, {}, event=event)
        
    # POST /api/v1/recommendations 및 루트 경로("/") 허용 (Function URL 대응)
    if method != "POST" or path not in ("/api/v1/recommendations", "/"):
        return error_response(404, "NOT_FOUND", "Route not found", event=event)

    # 1. JSON 바디 파싱 및 스키마/값 정합성 검증
    payload = _validate_payload(_normalize_payload(_json_body(event)))

    # 2. 페이로드의 mock=True 또는 환경변수 설정 시 모의 데이터 즉시 반환
    if payload.get("mock") or os.environ.get("MOCK_RECOMMENDATION") == "true":
        return json_response(200, _mock_recommendation(payload), event=event)

    # 3. AWS Bedrock Agent 런타임 호출 시도. 운영 호출 실패는 저장 가능한 mock으로 위장하지 않는다.
    try:
        return json_response(200, _invoke_bedrock_agent(payload), event=event)
    except Exception as error:
        LOGGER.error(
            Tag.SYSTEM,
            "AgentCore invocation failed entryType=%s country=%s tripType=%s errorType=%s errorMessage=%s",
            payload.get("entryType"),
            payload.get("country"),
            payload.get("tripType"),
            error.__class__.__name__,
            str(error),
        )
        return error_response(
            502,
            "AGENTCORE_UNAVAILABLE",
            "Recommendation generation is temporarily unavailable",
            event=event,
        )


_bedrock_client = None


def _get_bedrock_client():
    """boto3 Bedrock Agent 런타임 클라이언트 지연 로딩 싱글톤 구현 (us-east-1 리전 고정)"""
    global _bedrock_client
    if _bedrock_client is None:
        import boto3

        _bedrock_client = boto3.client("bedrock-agentcore", region_name="us-east-1")
    return _bedrock_client


def _invoke_bedrock_agent(payload):
    """Bedrock Agent에 정형화된 JSON 요청을 인코딩하여 전송하고, 실행 결과 스트림/출력을 수신하여 일정 응답으로 매핑"""
    agent_arn = os.environ.get("BEDROCK_AGENT_ARN")
    if not agent_arn:
        raise ValueError("AgentCore runtime ARN is not configured")

    # 1. AgentCore/LangGraph checkpoint 식별자 확인 또는 자동 생성
    session_id = payload.get("sessionId") or payload.get("threadId")
    if not session_id:
        session_id = f"session-{uuid.uuid4().hex}"  # 40글자 길이 식별자

    country = payload.get("country")
    trip_type = payload.get("tripType")
    themes = payload.get("themes", [])
    active_required_themes = payload.get("activeRequiredThemes") or themes
    include_festivals = payload.get("includeFestivals", False)
    destination_id = payload.get("destinationId", "")
    query = payload.get("naturalLanguageQuery", "")
    request_id = payload.get("requestId") or session_id

    # 2. Bedrock AgentCore V2에 주입할 표준 요청 페이로드 구조화
    now = datetime.now(timezone.utc)
    structured_payload = {
        "entryType": payload.get("runtimeEntryType") or payload.get("entryType", "create"),
        "requestId": request_id,
        "recommendation_request_id": payload.get("recommendation_request_id") or payload.get("recommendationId") or request_id,
        "session_id": session_id,
        "sessionId": session_id,
        "threadId": session_id,
        "actorId": payload.get("actorId") or payload.get("userId"),
        "userId": payload.get("userId"),
        "recommendationId": payload.get("recommendationId"),
        "selectedOptionId": payload.get("selectedOptionId"),
        "destinationId": destination_id or None,
        "country": country,
        "travelYear": payload.get("travelYear") or now.year,
        "travelMonth": payload.get("travelMonth") or now.month,
        "tripType": trip_type,
        "themes": themes,
        "activeRequiredThemes": active_required_themes,
        "includeFestivals": include_festivals,
        "naturalLanguageQuery": query or "",
        "rawQuery": payload.get("rawQuery") or query or "",
        "rawModifyQuery": payload.get("rawModifyQuery") or "",
        "softPreferenceQuery": payload.get("softPreferenceQuery") or "",
        "userLocation": payload.get("userLocation") or None,
        "user_location": payload.get("user_location") or payload.get("userLocation") or None,
        "onboardingProfile": payload.get("onboardingProfile") or {"themes": themes},
        "feedbackHistory": payload.get("feedbackHistory") or [],
        "itineraryRevision": payload.get("itineraryRevision") or payload.get("recommendationId") or request_id,
        "currentOrder": payload.get("currentOrder") or [],
    }

    LOGGER.info(
        Tag.SYSTEM,
        "Invoking AgentCore runtime entryType=%s country=%s tripType=%s themeCount=%s hasLocation=%s",
        structured_payload["entryType"],
        structured_payload["country"],
        structured_payload["tripType"],
        len(structured_payload["themes"]),
        bool(structured_payload["userLocation"]),
    )

    # 3. UTF-8 바이트로 인코딩하여 Bedrock API 전송
    bedrock_payload = json.dumps(structured_payload).encode("utf-8")

    client = _get_bedrock_client()
    response = client.invoke_agent_runtime(
        agentRuntimeArn=agent_arn,
        runtimeSessionId=session_id,
        payload=bedrock_payload,
    )

    LOGGER.info(Tag.SYSTEM, "AgentCore runtime returned keys=%s", sorted(response.keys()))

    # 4. Bedrock 런타임의 반환 객체(응답 바디, 스트림 등)를 순차적으로 역직렬화 시도
    raw_body = None
    for key in ("response", "body", "completion", "outputText"):
        if key in response:
            candidate = response[key]
            if hasattr(candidate, "read"):
                raw_body = candidate.read()
            elif isinstance(candidate, (str, bytes)):
                raw_body = candidate if isinstance(candidate, bytes) else candidate.encode("utf-8")
            if raw_body:
                LOGGER.info(Tag.SYSTEM, "AgentCore runtime body read key=%s byteLength=%s", key, len(raw_body))
                break

    if raw_body is None:
        raise ValueError("AgentCore response has no readable body")

    try:
        response_data = json.loads(raw_body)
    except json.JSONDecodeError:
        # JSON 포맷이 아닌 경우 일반 텍스트 포맷으로 매핑
        response_data = {"text": raw_body.decode("utf-8") if isinstance(raw_body, bytes) else raw_body}

    LOGGER.info(
        Tag.SYSTEM,
        "AgentCore runtime response parsed type=%s keys=%s",
        type(response_data).__name__,
        sorted(response_data.keys()) if isinstance(response_data, dict) else [],
    )

    # 5. 응답 본문 내 결과 노드 추출. V2는 최상위 출력, result/output/data 래퍼를 모두 허용한다.
    result = _extract_agent_result(response_data)

    itinerary = result.get("itinerary") if isinstance(result, dict) else None
    destination = _extract_destination(result)
    explainability = _extract_explainability(result)

    LOGGER.info(
        Tag.SYSTEM,
        "AgentCore itinerary mapped hasDestination=%s dayCount=%s",
        bool(destination),
        len(itinerary.get("days", [])) if isinstance(itinerary, dict) else 0,
    )

    res = _empty_agentcore_response(payload)
    res["mock"] = False
    res["sessionId"] = session_id
    res["threadId"] = session_id
    if isinstance(result, dict):
        res["recommendationId"] = result.get("recommendationId") or res["recommendationId"]
        if result.get("threadId"):
            res["threadId"] = result["threadId"]
        if result.get("expiresAt"):
            res["expiresAt"] = result["expiresAt"]
        if result.get("clarification"):
            res["clarification"] = result["clarification"]

    # 6. Bedrock Agent가 반환한 실제 소도시 정보로 오버라이드
    if destination and any(v for v in destination.values() if v is not None):
        res["destination"] = {
            "destinationId": destination.get("destinationId") or destination.get("cityId") or res["destination"]["destinationId"],
            "cityId": destination.get("cityId") or destination.get("destinationId") or res["destination"]["cityId"],
            "name": destination.get("name") or res["destination"]["name"],
            "country": destination.get("country") or payload.get("country") or res["destination"]["country"],
            "region": destination.get("region"),
        }

    # 7. Bedrock Agent가 반환한 AI 설명 근거 및 이유 데이터를 적용
    if explainability:
        res["explainability"] = explainability
        res["explanations"] = {
            "userNotice": explainability.get("userNotice") or "",
            "confidence": explainability.get("confidence", 0),
            "recommendationReasons": explainability.get("recommendationReasons", []),
        }
    if isinstance(result, dict):
        if result.get("festivalDateVerifications") is not None:
            res["festivalDateVerifications"] = result["festivalDateVerifications"]
        if result.get("alternativeItinerary") is not None:
            res["alternativeItinerary"] = result["alternativeItinerary"]
        links = result.get("links") or result.get("externalLinks")
        if links is not None:
            res["links"] = links
        validation_status = result.get("validationStatus")
        if validation_status:
            res["validationStatus"].update(validation_status)

    # 8. 생성된 일(Day)별 여행 코스가 실존할 때만 기본 모의 일정을 대체하여 덮어쓰기
    if isinstance(itinerary, dict) and itinerary.get("days"):
        resolved_trip_type = itinerary.get("tripType") or payload.get("tripType") or "2d1n"
        itinerary_title = itinerary.get("title") or _real_itinerary_title(res["destination"], resolved_trip_type)
        itinerary_summary = (
            itinerary.get("summary")
            or (explainability.get("itineraryFlowReason", "") if explainability else "")
            or _real_itinerary_summary(res["destination"])
        )
        res["itinerary"] = {
            "tripType": resolved_trip_type,
            "title": itinerary_title,
            "summary": itinerary_summary,
            "durationLabel": _duration_label(resolved_trip_type),
            "days": itinerary["days"],
        }
        routing.enrich_itinerary_routes(res["itinerary"])
        if "saveCompatibility" in res and "payload" in res["saveCompatibility"]:
            res["saveCompatibility"]["payload"]["title"] = itinerary_title
            res["saveCompatibility"]["payload"]["summary"] = itinerary_summary
            res["saveCompatibility"]["payload"]["sourceRecommendationId"] = res["recommendationId"]
            res["saveCompatibility"]["payload"]["destination"] = {
                "destinationId": res["destination"]["destinationId"],
                "name": res["destination"]["name"],
                "country": res["destination"]["country"],
                "region": res["destination"]["region"],
            }
            res["saveCompatibility"]["payload"]["itinerary"] = {"days": res["itinerary"]["days"]}
            if res.get("alternativeItinerary") is not None:
                res["saveCompatibility"]["payload"]["alternativeItinerary"] = res["alternativeItinerary"]
            if res.get("links") is not None:
                res["saveCompatibility"]["payload"]["links"] = res["links"]

    return res


def _validate_payload(body):
    """입력 페이로드 정합성 및 필수 필드 검증"""
    entry_type = body.get("entryType")
    if entry_type not in ENTRY_TYPES:
        raise AgentCoreRequestError(400, "VALIDATION_ERROR", "entryType is invalid")
    if entry_type == "clarify":
        if not (body.get("sessionId") or body.get("threadId")):
            raise AgentCoreRequestError(400, "VALIDATION_ERROR", "sessionId or threadId is required for clarify entry")
        if not (body.get("selectedOptionId") or body.get("rawQuery") or body.get("naturalLanguageQuery")):
            raise AgentCoreRequestError(400, "VALIDATION_ERROR", "clarify response is required")
        return body
    if entry_type == "modify":
        if not (body.get("sessionId") or body.get("threadId")):
            raise AgentCoreRequestError(400, "VALIDATION_ERROR", "sessionId or threadId is required for modify entry")
        if not body.get("rawModifyQuery"):
            raise AgentCoreRequestError(400, "VALIDATION_ERROR", "rawModifyQuery is required for modify entry")
        if not isinstance(body.get("currentOrder"), list):
            raise AgentCoreRequestError(400, "VALIDATION_ERROR", "currentOrder is required for modify entry")
        return body
    if entry_type == "confirm":
        if not (body.get("sessionId") or body.get("threadId")):
            raise AgentCoreRequestError(400, "VALIDATION_ERROR", "sessionId or threadId is required for confirm entry")
        if not body.get("recommendationId"):
            raise AgentCoreRequestError(400, "VALIDATION_ERROR", "recommendationId is required for confirm entry")
        return body
    country = body.get("country")
    if country not in COUNTRIES:
        raise AgentCoreRequestError(400, "VALIDATION_ERROR", "country is invalid")
    trip_type = body.get("tripType")
    if trip_type not in TRIP_TYPES:
        raise AgentCoreRequestError(400, "VALIDATION_ERROR", "tripType is invalid")
    themes = body.get("themes")
    if not isinstance(themes, list) or not themes or not all(isinstance(theme, str) and theme for theme in themes):
        raise AgentCoreRequestError(400, "VALIDATION_ERROR", "themes is required")
    if not isinstance(body.get("includeFestivals"), bool):
        raise AgentCoreRequestError(400, "VALIDATION_ERROR", "includeFestivals is required")
    if (entry_type == "map_marker" or body.get("sourceEntryType") == "map_marker") and not body.get("destinationId"):
        raise AgentCoreRequestError(400, "VALIDATION_ERROR", "destinationId is required for map marker entry")
    return body


def _normalize_payload(body):
    """Frontend recommendation-create contract를 AgentCore V2 runtime contract로 정렬한다."""
    if body.get("entryType") not in ("create", "map_marker", "chat", "home_recommendation"):
        return body

    destination_id = body.get("destinationId")
    raw_themes = body.get("themes") or body.get("activeRequiredThemes") or []
    themes = [
        FRONTEND_THEME_LABEL_TO_THEME_ID.get(theme, theme if theme in THEME_IDS else theme)
        for theme in raw_themes
        if isinstance(theme, str) and theme
    ]

    normalized = dict(body)
    normalized["entryType"] = "create"
    normalized["sourceEntryType"] = body.get("entryType")
    normalized["themes"] = themes
    normalized["activeRequiredThemes"] = body.get("activeRequiredThemes") or body.get("themes") or themes
    normalized["naturalLanguageQuery"] = (
        body.get("naturalLanguageQuery")
        or body.get("rawQuery")
        or body.get("softPreferenceQuery")
        or ""
    )
    normalized["includeFestivals"] = bool(body.get("includeFestivals"))

    return normalized


def _extract_agent_result(response_data):
    """AgentCore V2 응답의 최종 JSON 노드를 추출한다."""
    if not isinstance(response_data, dict):
        return response_data
    for key in ("result", "output", "data"):
        value = response_data.get(key)
        if isinstance(value, dict):
            return value
    return response_data


def _extract_destination(result):
    if not isinstance(result, dict):
        return None
    destination = result.get("destination") or result.get("selectedDestination")
    return destination if isinstance(destination, dict) else None


def _extract_explainability(result):
    if not isinstance(result, dict):
        return None

    explainability = result.get("explainability")
    if isinstance(explainability, dict):
        normalized = dict(explainability)
    else:
        normalized = {}

    if result.get("recommendationReasons") is not None:
        normalized["recommendationReasons"] = result["recommendationReasons"]
    if result.get("itineraryFlowReason") is not None:
        normalized["itineraryFlowReason"] = result["itineraryFlowReason"]
    if result.get("confidence") is not None:
        normalized["confidence"] = result["confidence"]

    user_notice = result.get("userNotice")
    if user_notice is None:
        user_notice = result.get("user_notice")
    if user_notice is not None:
        normalized["userNotice"] = user_notice

    unsupported_conditions = result.get("unsupportedConditions")
    if unsupported_conditions is not None:
        normalized["unsupportedConditions"] = unsupported_conditions

    return normalized or None


def _real_itinerary_title(destination, trip_type):
    destination_name = destination.get("name") or destination.get("destinationId") or "추천 소도시"
    return f"{destination_name} {_duration_label(trip_type)} 추천 일정"


def _real_itinerary_summary(destination):
    destination_name = destination.get("name") or destination.get("destinationId") or "선택한 소도시"
    return f"{destination_name}의 장소와 이동 흐름을 반영한 AI 추천 일정입니다."


def _empty_agentcore_response(payload):
    recommendation_id = payload.get("recommendationId") or payload.get("requestId") or _stable_id("rec", payload)
    destination_id = payload.get("destinationId") or ""
    trip_type = payload.get("tripType") or "2d1n"
    request_summary = payload.get("naturalLanguageQuery") or payload.get("rawQuery") or ""
    return {
        "mock": False,
        "recommendationId": recommendation_id,
        "generatedAt": _now_iso(),
        "destination": {
            "destinationId": destination_id,
            "cityId": destination_id,
            "name": destination_id,
            "country": payload.get("country") or "",
            "region": None,
        },
        "requestSnapshot": {
            "entryType": payload.get("entryType"),
            "country": payload.get("country"),
            "tripType": payload.get("tripType"),
            "themes": payload.get("themes") or [],
            "includeFestivals": payload.get("includeFestivals"),
            "naturalLanguageQuery": payload.get("naturalLanguageQuery") or payload.get("rawQuery") or "",
        },
        "validationStatus": {
            "singleDestination": False,
            "countrySeparated": False,
            "festivalConfirmedOnly": False,
        },
        "saveCompatibility": {
            "targetEndpoint": "/api/v1/me/itineraries",
            "payload": {
                "sourceRecommendationId": recommendation_id,
                "title": "",
                "summary": "",
                "destination": {
                    "destinationId": destination_id,
                    "name": destination_id,
                    "country": payload.get("country") or "",
                    "region": None,
                },
                "tripType": trip_type,
                "durationLabel": _duration_label(trip_type),
                "themes": payload.get("themes") or [],
                "conditionsSnapshot": {
                    "entryType": payload.get("entryType"),
                    "includeFestivals": payload.get("includeFestivals"),
                },
                "requestSummary": request_summary,
                "itinerary": {"days": []},
            },
        },
    }


def _mock_recommendation(payload):
    """Bedrock Agent 연동 연기 시 프론트엔드 연동 테스트용 모의 일정 생성기"""
    now = _now_iso()
    destination_id = payload.get("destinationId") or ((payload.get("city") or {}).get("cityId")) or f"{payload['country']}-mock-city"
    recommendation_id = _stable_id("rec", payload)
    city_name = ((payload.get("city") or {}).get("name")) or destination_id
    title = f"{city_name} {payload['tripType']} mock itinerary"
    natural_language_query = payload.get("naturalLanguageQuery") or ""

    return {
        "mock": True,
        "recommendationId": recommendation_id,
        "generatedAt": now,
        "destination": {
            "destinationId": destination_id,
            "cityId": destination_id,
            "name": city_name,
            "country": payload["country"],
            "region": None,
        },
        "requestSnapshot": {
            "entryType": payload["entryType"],
            "country": payload["country"],
            "tripType": payload["tripType"],
            "themes": payload["themes"],
            "includeFestivals": payload["includeFestivals"],
            "naturalLanguageQuery": natural_language_query,
        },
        "itinerary": {
            "tripType": payload["tripType"],
            "title": title,
            "summary": "AgentCore actual integration is deferred; this mock response is for frontend API wiring.",
            "durationLabel": _duration_label(payload["tripType"]),
            "days": [
                {
                    "day": 1,
                    "title": "Mock route",
                    "summary": "City context and preference context will be used by the follow-up AgentCore integration.",
                    "items": [
                        {
                            "itemId": _stable_id("item", {"recommendationId": recommendation_id, "order": 1}),
                            "contentId": destination_id,
                            "sortOrder": 1,
                            "timeOfDay": "morning",
                            "title": "Mock city walk",
                            "body": "Frontend can render this placeholder itinerary while Bedrock AgentCore is deferred.",
                            "reason": "Mock response only; no LLM or Bedrock call was made.",
                            "moveMinutes": 0,
                            "latitude": None,
                            "longitude": None,
                            "sourceBadges": ["mock"],
                        }
                    ],
                }
            ],
        },
        "explanations": {
            "userNotice": "Mock itinerary only. Actual Bedrock AgentCore integration is a follow-up task.",
            "confidence": "mock",
        },
        "validationStatus": {
            "singleDestination": True,
            "countrySeparated": True,
            "festivalConfirmedOnly": bool(payload["includeFestivals"]),
        },
        "saveCompatibility": {
            "targetEndpoint": "/api/v1/me/itineraries",
            "payload": {
                "sourceRecommendationId": recommendation_id,
                "title": title,
                "summary": "AgentCore mock response for frontend integration.",
                "destination": {
                    "destinationId": destination_id,
                    "name": city_name,
                    "country": payload["country"],
                    "region": None,
                },
                "tripType": payload["tripType"],
                "durationLabel": _duration_label(payload["tripType"]),
                "themes": payload["themes"],
                "conditionsSnapshot": {
                    "entryType": payload["entryType"],
                    "includeFestivals": payload["includeFestivals"],
                },
                "requestSummary": natural_language_query[:240],
                "itinerary": {
                    "days": [
                        {
                            "day": 1,
                            "title": "Mock route",
                            "items": [
                                {
                                    "itemId": _stable_id("item", {"recommendationId": recommendation_id, "order": 1}),
                                    "sortOrder": 1,
                                    "title": "Mock city walk",
                                    "body": "Mock item.",
                                }
                            ],
                        }
                    ]
                },
            },
        },
    }


def _duration_label(trip_type):
    """여행 기간 유형 키(daytrip, 2d1n 등)에 대응하는 한글 레이블 반환"""
    labels = {
        "daytrip": "당일치기",
        "2d1n": "1박 2일",
        "3d2n": "2박 3일",
        "4d3n": "3박 4일",
        "5d4n": "4박 5일",
    }
    return labels.get(trip_type, trip_type)


def _stable_id(prefix, value):
    """요청 및 응답의 고유 속성값을 활용한 SHA-256 해시 기반의 고유 ID(기기 독립적) 생성"""
    digest = hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def _json_body(event):
    """API Gateway 또는 Function URL 요청 바디에서 JSON 데이터 파싱 및 Base64 디코딩 수행"""
    raw_body = event.get("body")
    if raw_body in (None, ""):
        return {}
    if event.get("isBase64Encoded"):
        try:
            raw_body = base64.b64decode(raw_body).decode("utf-8")
        except Exception:
            raise AgentCoreRequestError(400, "INVALID_JSON", "Request body must be valid JSON")
    try:
        parsed = json.loads(raw_body)
    except json.JSONDecodeError:
        raise AgentCoreRequestError(400, "INVALID_JSON", "Request body must be valid JSON")
    if not isinstance(parsed, dict):
        raise AgentCoreRequestError(400, "VALIDATION_ERROR", "Request body must be a JSON object")
    return parsed


def _event_method(event):
    """요청 메소드(HTTP Method) 추출"""
    return (((event.get("requestContext") or {}).get("http") or {}).get("method") or event.get("httpMethod") or "").upper()


def _event_path(event):
    """요청 URL 경로 추출"""
    return event.get("rawPath") or event.get("path") or ""


def _now_iso():
    """현재 시간을 UTC 기준 ISO 8601 포맷으로 변환"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
