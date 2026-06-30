"""
image_resolver.py
-----------------
S3 이미지 매핑 테이블(image_map.json)을 사용해서
관광지 제목(title) → CloudFront 이미지 URL을 해결하는 모듈.

매핑 key 형식: "{city_id}/{S3 파일명 stem}"
예: "KR-Cheongdo/Bulryeongsa"
    "KR-Andong/AndongBeopheungsajiChilcheungjeontap"
"""

from __future__ import annotations

import json
import hashlib
import os
import re

# --------------------------------------------------------------------------- #
# Korean Revised Romanization tables
# --------------------------------------------------------------------------- #

# 19 onset consonants (초성)
_ONSET = [
    "g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s", "ss", "",
    "j", "jj", "ch", "k", "t", "p", "h",
]

# 21 vowels (중성)
_VOWEL = [
    "a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o",
    "wa", "wae", "oe", "yo", "u", "wo", "we", "wi", "yu",
    "eu", "ui", "i",
]

# 28 coda values (종성, 0 = 없음)
_CODA = [
    "",    # 0  (없음)
    "k",   # 1  ㄱ
    "k",   # 2  ㄲ
    "k",   # 3  ㄳ
    "n",   # 4  ㄴ
    "n",   # 5  ㄵ
    "n",   # 6  ㄶ
    "t",   # 7  ㄷ
    "l",   # 8  ㄹ
    "k",   # 9  ㄺ
    "m",   # 10 ㄻ
    "p",   # 11 ㄼ
    "l",   # 12 ㄽ
    "k",   # 13 ㄾ
    "p",   # 14 ㄿ
    "l",   # 15 ㅀ
    "m",   # 16 ㅁ
    "p",   # 17 ㅂ
    "p",   # 18 ㅄ
    "t",   # 19 ㅅ
    "t",   # 20 ㅆ
    "ng",  # 21 ㅇ
    "t",   # 22 ㅈ
    "t",   # 23 ㅊ
    "k",   # 24 ㅋ
    "t",   # 25 ㅌ
    "p",   # 26 ㅍ
    "h",   # 27 ㅎ
]

_HANGUL_START = 0xAC00


def _romanize_korean(text: str) -> str:
    """한국어 문자열을 개정 국어 표기법 로마자로 변환."""
    result = []
    for ch in text:
        code = ord(ch)
        if _HANGUL_START <= code <= 0xD7A3:
            offset = code - _HANGUL_START
            onset_idx = offset // (21 * 28)
            vowel_idx = (offset % (21 * 28)) // 28
            coda_idx = offset % 28
            result.append(_ONSET[onset_idx] + _VOWEL[vowel_idx] + _CODA[coda_idx])
        else:
            result.append(ch.lower())
    return "".join(result)


def _to_pascal_stem(title: str) -> str:
    """
    한국어 제목 → S3 PascalCase stem.
    띄어쓰기 단위로 각 단어를 로마자화 후 첫 글자 대문자로 이어붙임.
    예: "봉화 북지리 마애여래좌상" → "BonghwaBukjiriMaeyeoraejwasang"
    """
    parts = []
    for word in title.split():
        romanized = re.sub(r"[^a-z0-9]", "", _romanize_korean(word))
        if romanized:
            parts.append(romanized[0].upper() + romanized[1:])
    return "".join(parts)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def load_image_map(path: str | None = None) -> dict:
    """
    image_map.json을 로드해서 반환.
    path 생략 시 이 모듈과 같은 폴더의 image_map.json을 사용.

    반환 형식:
    {
        "cdnBase": "https://...",
        "images": { "KR-City/Stem": "images/KR/City/Stem_1.jpg", ... }
    }
    """
    if path is None:
        path = os.path.join(os.path.dirname(__file__), "image_map.json")

    if not os.path.exists(path):
        return {"cdnBase": "", "images": {}}

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        return {"cdnBase": "", "images": {}}

    return {
        "cdnBase": data.get("cdnBase", ""),
        "images": data.get("images", {}),
    }


def resolve_image_url(
    city_id: str,
    title: str,
    cdn_base: str,
    image_map: dict | None,
    allow_city_fallback: bool = False,
) -> str | None:
    """
    city_id + title 조합으로 S3 이미지 URL을 해결.

    시도 순서:
    1. title 전체를 PascalCase stem으로 변환 후 image_map 조회
    2. 도시영문명을 앞에 붙인 stem으로 조회
    3. allow_city_fallback=True이면 같은 city의 S3 이미지 중 title 기반으로 안정 선택
       (일부 지역은 파일명에 도시명이 prefix로 포함됨)

    Args:
        city_id:   e.g. "KR-Cheongdo"
        title:     관광지 한국어 제목, e.g. "청도박물관"
        cdn_base:  CloudFront base URL, e.g. "https://det7vj7wxfmim.cloudfront.net"
        image_map: load_image_map() 반환값의 "images" dict
        allow_city_fallback: title 정확 매칭 실패 시 같은 도시의 S3 이미지를 fallback으로 허용

    Returns:
        CloudFront 전체 URL 또는 None
    """
    if not city_id or not title or not cdn_base or not isinstance(image_map, dict):
        return None

    # city_id → 영문 도시명 (e.g. "KR-Cheongdo" → "Cheongdo")
    city_en = city_id.split("-", 1)[1] if "-" in city_id else city_id

    stem = _to_pascal_stem(title)
    if not stem:
        return None

    # 후보 key 목록 (순서대로 시도)
    candidates = [
        f"{city_id}/{stem}",          # e.g. "KR-Cheongdo/Bulryeongsa"
        f"{city_id}/{city_en}{stem}",  # e.g. "KR-Cheongdo/CheongdoBulryeongsa"
    ]

    cdn_base = cdn_base.rstrip("/")
    for key in candidates:
        s3_key = image_map.get(key)
        if s3_key:
            return f"{cdn_base}/{s3_key}"

    if allow_city_fallback:
        return _resolve_city_fallback_url(city_id, title, cdn_base, image_map)

    return None


def _resolve_city_fallback_url(
    city_id: str,
    title: str,
    cdn_base: str,
    image_map: dict,
) -> str | None:
    city_prefix = f"{city_id}/"
    city_images = sorted(
        s3_key
        for key, s3_key in image_map.items()
        if isinstance(key, str)
        and key.startswith(city_prefix)
        and isinstance(s3_key, str)
        and s3_key.strip()
    )

    if not city_images:
        return None

    digest = hashlib.sha256(title.encode("utf-8")).hexdigest()
    selected = city_images[int(digest[:8], 16) % len(city_images)]
    return f"{cdn_base.rstrip('/')}/{selected}"
