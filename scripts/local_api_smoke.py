#!/usr/bin/env python3
"""Run a local handler-level smoke test for Lovv backend API contracts.

This script does not read real .env files or call provider services. It uses
fake provider verification and in-memory repositories to verify the Lambda
handler contracts that the frontend depends on.
"""

import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from auth.app import handle_request as handle_auth_request
from auth.provider_verifier import ProviderIdentity
from auth.session_repository import InMemorySessionRepository
from auth.user_repository import InMemoryUserRepository
from preferences.app import handle_request as handle_preference_request
from preferences.repository import InMemoryPreferenceRepository
from saved_plans.app import handle_request as handle_saved_plan_request
from saved_plans.repository import InMemorySavedPlanRepository
from small_cities.app import handle_request as handle_small_city_request


AUTH_ENV = {
    "AUTH_TOKEN_SIGNING_SECRET": "local-smoke-signing-secret-with-enough-length",
    "AUTH_TOKEN_TTL_SECONDS": "900",
    "AUTH_REFRESH_TTL_SECONDS": "1209600",
    "AUTH_ISSUER": "lovv-local-smoke-auth",
    "AUTH_AUDIENCE": "lovv-local-smoke-api",
    "AUTH_REFRESH_COOKIE_NAME": "lovv_session",
    "AUTH_REFRESH_COOKIE_SECURE": "false",
}


class SmokeFailure(AssertionError):
    pass


class FakeProviderVerifier:
    def verify(self, provider, credential_type, credential, nonce=None, redirect_uri=None, code_verifier=None):
        if provider != "google":
            raise SmokeFailure(f"Unexpected provider: {provider}")
        if credential_type != "id_token" or credential != "local-smoke-google-token":
            raise SmokeFailure("Unexpected local smoke credential")
        return ProviderIdentity(
            provider="google",
            provider_user_id="local-smoke-google-sub",
            email="smoke@example.com",
            email_verified=True,
            display_name="Smoke User",
            avatar_url="https://images.example.com/smoke.png",
        )


class FakeSmallCityRepository:
    def __init__(self):
        self.records = [
            {
                "id": "KR-Andong",
                "country": "KR",
                "country_label": "한국",
                "region": "경북",
                "name_ko": "안동",
                "name_local": "안동시",
                "latitude": 36.56,
                "longitude": 128.72,
                "themes": ["전통", "미식"],
                "summary": "안동은 전통과 미식 여행 후보입니다.",
                "detail": "하회마을과 월영교를 기준으로 추천합니다.",
                "highlights": ["하회마을", "월영교"],
                "route_seed": ["하회마을", "월영교"],
                "image_url": "https://images.example.com/andong.jpg",
                "internal_meta": {"source": "LocalSmoke"},
            }
        ]
        self.places = {
            "KR-Andong": {
                "cityId": "KR-Andong",
                "cityName": "안동",
                "summary": {"attractionCount": 1, "festivalCount": 1, "visitorStatisticsCount": 12},
                "attractions": [
                    {
                        "placeId": "ATT-1",
                        "type": "attraction",
                        "title": "하회마을",
                        "imageUrl": "https://images.example.com/hahoe.jpg",
                        "latitude": 36.54,
                        "longitude": 128.52,
                    }
                ],
                "festivals": [
                    {
                        "placeId": "FEST-1",
                        "type": "festival",
                        "title": "안동국제탈춤페스티벌",
                        "imageUrl": None,
                        "latitude": 36.55,
                        "longitude": 128.73,
                    }
                ],
            }
        }

    def list_city_records(self):
        return list(self.records)

    def get_city_record(self, city_id):
        return next((record for record in self.records if record["id"] == city_id), None)

    def get_city_places(self, city_id):
        return self.places.get(city_id)


def make_event(method, path, body=None, headers=None, cookies=None, path_parameters=None, query=None):
    event = {
        "rawPath": path,
        "headers": headers or {},
        "pathParameters": path_parameters or {},
        "queryStringParameters": query,
        "requestContext": {
            "http": {
                "method": method,
                "path": path,
                "sourceIp": "127.0.0.1",
                "userAgent": "local-api-smoke",
            }
        },
    }
    if body is not None:
        event["body"] = json.dumps(body, ensure_ascii=False)
    if cookies is not None:
        event["cookies"] = cookies
    return event


def assert_status(response, expected, label):
    actual = response.get("statusCode")
    if actual != expected:
        raise SmokeFailure(f"{label}: expected {expected}, got {actual}: {response.get('body')}")


def response_body(response):
    body = response.get("body")
    return json.loads(body) if body else {}


def assert_true(condition, label):
    if not condition:
        raise SmokeFailure(label)


def pass_step(label):
    print(f"PASS {label}")


def run_smoke():
    os.environ.update(AUTH_ENV)

    provider_verifier = FakeProviderVerifier()
    user_repository = InMemoryUserRepository(now="2026-06-13T00:00:00Z")
    session_repository = InMemorySessionRepository(now_epoch=1_781_312_400)
    preference_repository = InMemoryPreferenceRepository(now="2026-06-13T00:00:00Z")
    saved_plan_repository = InMemorySavedPlanRepository(now="2026-06-13T00:00:00Z")
    small_city_repository = FakeSmallCityRepository()

    login = handle_auth_request(
        make_event(
            "POST",
            "/api/v1/auth/google",
            {"credentialType": "id_token", "credential": "local-smoke-google-token"},
        ),
        provider_verifier=provider_verifier,
        user_repository=user_repository,
        session_repository=session_repository,
        preference_repository=preference_repository,
    )
    assert_status(login, 200, "google login")
    login_body = response_body(login)
    access_token = login_body.get("accessToken")
    set_cookie = login.get("headers", {}).get("Set-Cookie")
    assert_true(access_token and set_cookie, "login returned accessToken and refresh cookie")
    auth_headers = {"authorization": f"Bearer {access_token}", "content-type": "application/json"}
    pass_step("auth login")

    session = handle_auth_request(
        make_event("GET", "/api/v1/auth/session", headers={"cookie": set_cookie}),
        user_repository=user_repository,
        session_repository=session_repository,
        preference_repository=preference_repository,
    )
    assert_status(session, 200, "auth session")
    assert_true(response_body(session).get("authenticated") is True, "session is authenticated")
    pass_step("auth session")

    preference_payload = {
        "selectedThemeIds": ["history_tradition", "food_local"],
        "preferredRegions": ["경북"],
        "travelStyles": ["slow_walk"],
        "pace": "relaxed",
        "tripDays": 1,
        "countryTrack": "KR",
    }
    put_preferences = handle_preference_request(
        make_event("PUT", "/api/v1/me/preferences", preference_payload, headers=auth_headers),
        repository=preference_repository,
    )
    assert_status(put_preferences, 200, "put preferences")
    assert_true(response_body(put_preferences)["preferences"]["selectedThemeIds"] == ["history_tradition", "food_local"], "preferences selectedThemeIds alias")

    get_preferences = handle_preference_request(
        make_event("GET", "/api/v1/me/preferences", headers=auth_headers),
        repository=preference_repository,
    )
    assert_status(get_preferences, 200, "get preferences")
    assert_true(response_body(get_preferences)["onboardingCompleted"] is True, "preferences onboardingCompleted")
    pass_step("preferences put/get")

    me = handle_auth_request(
        make_event("GET", "/api/v1/auth/me", headers=auth_headers),
        user_repository=user_repository,
        preference_repository=preference_repository,
    )
    assert_status(me, 200, "auth me")
    assert_true(response_body(me)["preferences"]["selectedThemeIds"] == ["history_tradition", "food_local"], "auth me includes preferences alias")
    pass_step("auth me")

    cities = handle_small_city_request(
        make_event("GET", "/api/v1/map/cities", query={"country": "KR", "themes": "전통", "page_size": "5"}),
        repository=small_city_repository,
    )
    assert_status(cities, 200, "small city list")
    city_id = response_body(cities)["data"][0]["id"]
    assert_true(city_id == "KR-Andong", "small city list returned expected city")

    detail = handle_small_city_request(
        make_event("GET", f"/api/v1/map/cities/{city_id}", path_parameters={"cityId": city_id}),
        repository=small_city_repository,
    )
    assert_status(detail, 200, "small city detail")

    places = handle_small_city_request(
        make_event("GET", f"/api/v1/map/cities/{city_id}/places", path_parameters={"cityId": city_id}),
        repository=small_city_repository,
    )
    assert_status(places, 200, "small city places")
    assert_true(response_body(places)["attractions"][0]["title"] == "하회마을", "small city places returned attractions")
    pass_step("small-cities list/detail/places")

    itinerary_payload = {
        "sourceRecommendationId": "rec-smoke-1",
        "idempotencyKey": "smoke-save-1",
        "title": "안동 당일 전통 산책",
        "summary": "전통 명소와 야경 산책을 묶은 일정입니다.",
        "destination": {"destinationId": city_id, "name": "안동", "country": "KR", "region": "경북"},
        "tripType": "daytrip",
        "durationLabel": "당일치기",
        "themes": ["history_tradition"],
        "conditionsSnapshot": {"travelMonth": 6},
        "requestSummary": "전통과 느린 산책",
        "itinerary": {
            "days": [
                {
                    "day": 1,
                    "title": "전통 산책",
                    "stops": [
                        {
                            "title": "하회마을",
                            "time": "오전",
                            "move": "도보",
                            "reason": "전통 테마와 맞습니다.",
                            "body": "마을을 천천히 둘러봅니다.",
                        },
                        {
                            "title": "월영교",
                            "time": "저녁",
                            "move": "택시 20분",
                            "reason": "하루 마무리 산책에 좋습니다.",
                            "body": "야경을 보며 산책합니다.",
                        },
                    ],
                }
            ]
        },
    }
    save = handle_saved_plan_request(
        make_event("POST", "/api/v1/me/itineraries", itinerary_payload, headers=auth_headers),
        repository=saved_plan_repository,
    )
    assert_status(save, 201, "save itinerary")
    itinerary_id = response_body(save)["itineraryId"]

    saved_list = handle_saved_plan_request(
        make_event("GET", "/api/v1/me/itineraries", headers=auth_headers),
        repository=saved_plan_repository,
    )
    assert_status(saved_list, 200, "list itineraries")
    list_item = response_body(saved_list)["items"][0]
    assert_true(list_item["itineraryId"] == itinerary_id, "list includes saved itinerary")
    assert_true(list_item["userId"] == "user-1", "list includes userId")
    assert_true(list_item["ownerId"] == "user-1", "list includes ownerId")

    saved_detail = handle_saved_plan_request(
        make_event("GET", f"/api/v1/me/itineraries/{itinerary_id}", headers=auth_headers, path_parameters={"itineraryId": itinerary_id}),
        repository=saved_plan_repository,
    )
    assert_status(saved_detail, 200, "detail itinerary")
    detail_body = response_body(saved_detail)
    first_day = detail_body["itinerary"]["days"][0]
    assert_true(detail_body["userId"] == "user-1", "detail includes userId")
    assert_true(detail_body["ownerId"] == "user-1", "detail includes ownerId")
    assert_true(first_day["items"][0]["title"] == "하회마을", "detail includes items")
    assert_true(first_day["stops"][0]["title"] == "하회마을", "detail includes stops alias")
    assert_true(detail_body["isLiked"] is False, "detail starts unliked")
    pass_step("saved plans save/list/detail")

    like = handle_saved_plan_request(
        make_event("PUT", f"/api/v1/me/itineraries/{itinerary_id}/reactions/like", headers=auth_headers, path_parameters={"itineraryId": itinerary_id}),
        repository=saved_plan_repository,
    )
    assert_status(like, 200, "like itinerary")
    assert_true(response_body(like)["isLiked"] is True, "like response isLiked")

    liked_detail = handle_saved_plan_request(
        make_event("GET", f"/api/v1/me/itineraries/{itinerary_id}", headers=auth_headers, path_parameters={"itineraryId": itinerary_id}),
        repository=saved_plan_repository,
    )
    assert_true(response_body(liked_detail)["isLiked"] is True, "detail reflects like")

    unlike = handle_saved_plan_request(
        make_event("DELETE", f"/api/v1/me/itineraries/{itinerary_id}/reactions/like", headers=auth_headers, path_parameters={"itineraryId": itinerary_id}),
        repository=saved_plan_repository,
    )
    assert_status(unlike, 204, "unlike itinerary")
    pass_step("saved plans like/unlike")

    delete = handle_saved_plan_request(
        make_event("DELETE", f"/api/v1/me/itineraries/{itinerary_id}", headers=auth_headers, path_parameters={"itineraryId": itinerary_id}),
        repository=saved_plan_repository,
    )
    assert_status(delete, 204, "delete itinerary")

    list_after_delete = handle_saved_plan_request(
        make_event("GET", "/api/v1/me/itineraries", headers=auth_headers),
        repository=saved_plan_repository,
    )
    assert_status(list_after_delete, 200, "list after delete")
    assert_true(response_body(list_after_delete)["items"] == [], "deleted itinerary removed from list")

    detail_after_delete = handle_saved_plan_request(
        make_event("GET", f"/api/v1/me/itineraries/{itinerary_id}", headers=auth_headers, path_parameters={"itineraryId": itinerary_id}),
        repository=saved_plan_repository,
    )
    assert_status(detail_after_delete, 404, "detail after delete")

    like_after_delete = handle_saved_plan_request(
        make_event("PUT", f"/api/v1/me/itineraries/{itinerary_id}/reactions/like", headers=auth_headers, path_parameters={"itineraryId": itinerary_id}),
        repository=saved_plan_repository,
    )
    assert_status(like_after_delete, 404, "like after delete")
    pass_step("saved plans delete exclusions")

    print("OK local backend API smoke passed")


if __name__ == "__main__":
    try:
        run_smoke()
    except SmokeFailure as error:
        print(f"FAIL {error}", file=sys.stderr)
        raise SystemExit(1)
