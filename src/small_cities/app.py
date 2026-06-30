# @file src/small_cities/app.py
# @description 소도시 원천 데이터를 분석하여 프론트엔드에 마커 및 상세 관광 콘텐츠 목록을 제공하는 Lambda 핸들러.
# @lastModified 2026-06-23

import json
import os
from decimal import Decimal

from shared.http import cors_headers
from shared.logger import Tag, get_logger
from small_cities.dynamodb_repository import DynamoDbSmallCityRepository
from small_cities.mapper import VALID_THEMES
from small_cities.s3_raw_repository import CityDataInvalidError, CityDataNotFoundError, CityDataUpstreamError, S3RawCityRepository
from small_cities.service import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, SmallCityService


LOGGER = get_logger(__name__)

VALID_COUNTRIES = {"KR", "JP"}


class RequestValidationError(ValueError):
    """요청 파라미터가 유효하지 않을 때 발생하는 커스텀 예외"""
    pass


def lambda_handler(event, context):
    """AWS Lambda 실행 진입점"""
    return handle_request(event)


def handle_request(event, repository=None):
    """API 게이트웨이 요청 라우팅 및 예외 처리 핵심 로직"""
    try:
        method = get_method(event)
        # OPTIONS preflight 요청 처리
        if method == "OPTIONS":
            return json_response({}, event=event)
        # 소도시 API는 오직 GET 요청만 지원
        if method != "GET":
            return error_response("INVALID_METHOD", "Only GET is supported.", 405, event=event)

        service = SmallCityService(repository or default_repository())
        city_id = get_city_id(event)
        markers_only = is_marker_request(event)
        places_request = is_places_request(event)

        # 1. 특정 소도시의 하위 장소(플레이스/식당/명소 등) 리스트 조회 처리
        if city_id and places_request:
            places = service.get_city_places(city_id)
            if not places:
                return error_response("NOT_FOUND", "Small city places were not found.", 404, event=event)
            return json_response(places, event=event)

        # 2. 단일 소도시 상세 정보 조회 처리
        if city_id:
            LOGGER.info(Tag.CITY, "Loading small city detail (cityId=%s)", city_id)
            record = service.get_city(city_id)
            if not record:
                return error_response("NOT_FOUND", "Small city was not found.", 404, event=event)
            return json_response({"data": record}, event=event)

        # 3. 소도시 목록 및 지도 마커 조회를 위한 쿼리 파라미터 파싱 및 검증
        params = event.get("queryStringParameters") or {}
        country = parse_country(params.get("country"))
        query = parse_query(params.get("q"))
        themes = parse_themes(params.get("themes"))
        page = parse_positive_int(params.get("page"), DEFAULT_PAGE, "page")
        page_size = parse_positive_int(params.get("page_size"), DEFAULT_PAGE_SIZE, "page_size", MAX_PAGE_SIZE)

        # 4. 지도 마커 전용 간략 목록 반환 처리
        if markers_only:
            return json_response(service.list_markers(country=country, query=query, themes=themes, page=page, page_size=page_size), event=event)

        # 5. 소도시 전체 속성이 포함된 상세 목록 반환 처리
        return json_response(service.list_cities(country=country, query=query, themes=themes, page=page, page_size=page_size), event=event)
    except RequestValidationError as error:
        # 쿼리 파라미터 검증 실패 에러
        return error_response("INVALID_QUERY", str(error), 400, event=event)
    except CityDataNotFoundError as error:
        # 소도시 또는 데이터가 존재하지 않는 에러 (404)
        return error_response(error.code, error.message, error.status_code, event=event)
    except CityDataUpstreamError as error:
        # S3 오리진 서버 또는 네트워크 등 업스트림 에러
        LOGGER.error(Tag.SYSTEM, "Upstream raw city data error: %s %s", error.code, error.message)
        return error_response(error.code, error.message, error.status_code, event=event)
    except CityDataInvalidError as error:
        # 데이터 정합성 검증 에러
        LOGGER.error(Tag.CITY, "Invalid raw city data: %s %s", error.code, error.message)
        return error_response(error.code, error.message, error.status_code, event=event)
    except Exception:
        # 예상하지 못한 백엔드 서버 핸들링 에러 (500)
        LOGGER.exception(Tag.SYSTEM, "Unhandled small-city API error")
        return error_response("INTERNAL_ERROR", "Small-city API is unavailable.", 500, event=event)


def get_method(event):
    """API Gateway 또는 Function URL 요청 객체에서 HTTP Method(GET, POST 등)를 대문자로 추출"""
    return (
        ((event.get("requestContext") or {}).get("http") or {}).get("method")
        or event.get("httpMethod")
        or ""
    ).upper()


def get_city_id(event):
    """경로 파라미터(pathParameters) 또는 rawPath 주소 패턴에서 소도시 식별자(cityId)를 추출"""
    path_parameters = event.get("pathParameters") or {}
    if path_parameters.get("cityId"):
        return path_parameters["cityId"]

    path = event.get("rawPath") or event.get("path") or ""
    prefixes = (
        "/api/small-cities/",
        "/api/v1/small-cities/",
        "/api/v1/map/cities/",
    )
    for prefix in prefixes:
        if path.startswith(prefix):
            return path[len(prefix) :].split("/", 1)[0]

    return None


def is_marker_request(event):
    """지도 마커 전용 단순 데이터 요청인지 경로 검사"""
    path = event.get("rawPath") or event.get("path") or ""
    return path == "/api/v1/map/markers"


def is_places_request(event):
    """특정 도시의 하위 관광 장소(places) 리스트 요청인지 경로 검사"""
    path = event.get("rawPath") or event.get("path") or ""
    return path.endswith("/places")


def parse_country(value):
    """국가 코드 파싱 및 지원 여부 검증 (KR, JP)"""
    if value in (None, ""):
        return None
    if value not in VALID_COUNTRIES:
        raise RequestValidationError("country must be KR or JP.")
    return value


def parse_query(value):
    """검색 쿼리 문자열 공백 제거 및 최대 길이(80자) 제한 검증"""
    if value is None:
        return None

    query = value.strip()
    if len(query) > 80:
        raise RequestValidationError("q must be 80 characters or fewer.")
    return query or None


def parse_themes(value):
    """쉼표(,)로 구분된 테마 리스트 파싱 및 지원 여부 유효성 검증"""
    if value in (None, ""):
        return []

    themes = [theme.strip() for theme in value.split(",") if theme.strip()]
    invalid = [theme for theme in themes if theme not in VALID_THEMES]
    if invalid:
        raise RequestValidationError("themes contains unsupported values.")

    return themes


def parse_positive_int(value, default, name, maximum=None):
    """양의 정수형 쿼리 파라미터 파싱 및 범위(최댓값) 유효성 검증"""
    if value in (None, ""):
        return default

    if not str(value).isdigit():
        raise RequestValidationError(f"{name} must be a positive integer.")

    parsed = int(value)
    if parsed < 1:
        raise RequestValidationError(f"{name} must be a positive integer.")
    if maximum is not None and parsed > maximum:
        raise RequestValidationError(f"{name} must be {maximum} or lower.")

    return parsed


def default_repository():
    source = os.environ.get("MAP_CITY_SOURCE", "dynamodb").strip().lower()
    if source in ("", "dynamodb", "dynamo"):
        return DynamoDbSmallCityRepository.from_env()
    if source in ("s3", "s3_raw", "s3-raw"):
        return S3RawCityRepository.from_env()
    raise CityDataInvalidError()


def json_response(body, status_code=200, event=None):
    """CORS 헤더를 동적으로 동기화하고 캐싱을 방지하는 JSON 응답 생성기"""
    headers = cors_headers(event)
    headers["Cache-Control"] = "no-store"
    return {
        "statusCode": status_code,
        "headers": headers,
        "body": json.dumps(body, ensure_ascii=False, default=json_default),
    }


def error_response(code, message, status_code, event=None):
    """일관된 에러 구조(code, message)로 에러 응답 JSON을 생성"""
    return json_response({"error": {"code": code, "message": message}}, status_code, event=event)


def json_default(value):
    """JSON 직렬화 중 Decimal 타입 등의 데이터 변환 예외 처리"""
    if isinstance(value, Decimal):
        if value % 1 == 0:
            return int(value)
        return float(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
