from recommendations.reaction_repository import RdsRecommendationReactionRepository
from recommendations.service import (
    RecommendationValidationError,
    monthly_cities,
    parse_country,
    parse_limit,
    popular_destinations,
    reaction_cities,
)
from shared.auth import AuthTokenError
from shared.current_user import authenticated_claims
from shared.http import error_response, json_response
from shared.logger import Tag, get_logger
from small_cities.app import default_repository as default_city_repository
from small_cities.s3_raw_repository import CityDataInvalidError, CityDataUpstreamError


LOGGER = get_logger(__name__)


def lambda_handler(event, context):
    return handle_request(event or {})


def handle_request(event, city_repository=None, reaction_repository=None, now=None):
    try:
        method = _event_method(event)
        path = _event_path(event)
        if method == "OPTIONS":
            return json_response(200, {}, event=event)
        if method != "GET":
            return error_response(405, "INVALID_METHOD", "Only GET is supported.", event=event)

        query = event.get("queryStringParameters") or {}
        limit = parse_limit(query.get("limit"))

        if path == "/api/v1/recommendations/monthly-cities":
            country = parse_country(query.get("country"))
            repository = city_repository or default_city_repository()
            body = monthly_cities(repository.list_city_records(), country=country, limit=limit, now=now)
            return json_response(200, body, event=event)

        if path == "/api/v1/recommendations/reaction-cities":
            user_id = _current_user_id(event)
            repository = city_repository or default_city_repository()
            reactions = reaction_repository or RdsRecommendationReactionRepository.from_env()
            liked_signals = reactions.list_liked_itinerary_signals(user_id, limit=max(limit, 20))
            body = reaction_cities(repository.list_city_records(), liked_signals, limit=limit)
            return json_response(200, body, event=event)

        if path == "/api/v1/recommendations/popular-destinations":
            repository = city_repository or default_city_repository()
            reactions = reaction_repository or RdsRecommendationReactionRepository.from_env()
            signals = reactions.list_popular_destination_signals(limit=max(limit * 20, 120))
            body = popular_destinations(repository.list_city_records(), signals, limit=limit)
            return json_response(200, body, event=event)

        return error_response(404, "NOT_FOUND", "Route not found.", event=event)
    except RecommendationValidationError as error:
        return error_response(400, "INVALID_QUERY", str(error), event=event)
    except AuthTokenError as error:
        return error_response(error.status_code, error.code, error.message, event=event)
    except (CityDataInvalidError, CityDataUpstreamError) as error:
        LOGGER.error(Tag.SYSTEM, "Recommendation city source error: %s %s", error.code, error.message)
        return error_response(error.status_code, error.code, error.message, event=event)
    except Exception:
        LOGGER.exception(Tag.SYSTEM, "Unhandled recommendation feed API error")
        return error_response(500, "INTERNAL_ERROR", "Recommendation feed API is unavailable.", event=event)


def _current_user_id(event):
    claims = authenticated_claims(event)
    return claims.get("userId") or claims.get("sub")


def _event_method(event):
    return (
        ((event.get("requestContext") or {}).get("http") or {}).get("method")
        or event.get("httpMethod")
        or ""
    ).upper()


def _event_path(event):
    return event.get("rawPath") or event.get("path") or ""
