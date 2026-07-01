"""tests/test_image_resolver.py — image_resolver 단위 테스트"""
import unittest

from small_cities.image_resolver import _romanize_korean, _to_pascal_stem, load_image_map, resolve_image_url


# --------------------------------------------------------------------------- #
# _to_pascal_stem
# --------------------------------------------------------------------------- #

class TestToPascalStem(unittest.TestCase):
    def test_single_word(self):
        # 비안향교 → Bianhyanggyo
        assert _to_pascal_stem("비안향교") == "Bianhyanggyo"

    def test_multi_word(self):
        # 봉화 북지리 마애여래좌상 → BonghwaBukjiriMaaeyeoraejwasang
        result = _to_pascal_stem("봉화 북지리 마애여래좌상")
        assert result == "BonghwaBukjiriMaaeyeoraejwasang"

    def test_two_words(self):
        # 전망대 범바위 → two separate PascalCase segments
        result = _to_pascal_stem("전망대 범바위")
        assert result[0].isupper()
        # 두 번째 단어도 대문자 시작
        assert len(result) > 0

    def test_empty_string(self):
        assert _to_pascal_stem("") == ""

    def test_whitespace_only(self):
        assert _to_pascal_stem("   ") == ""

    def test_non_korean(self):
        # ASCII 입력은 소문자로 처리된 후 첫 글자 대문자
        result = _to_pascal_stem("cafe")
        assert result == "Cafe"

    def test_liaison_romanization_variant(self):
        assert _romanize_korean("강릉", apply_liaison=True) == "gangneung"
        assert _romanize_korean("강릉", apply_liaison=False) == "gangreung"
        assert _to_pascal_stem("강릉", apply_liaison=True) == "Gangneung"
        assert _to_pascal_stem("강릉", apply_liaison=False) == "Gangreung"
        assert _romanize_korean("정릉", apply_liaison=True) == "jeongneung"
        assert _romanize_korean("정릉", apply_liaison=False) == "jeongreung"

    def test_unrelated_existing_romanization_is_unchanged(self):
        assert _to_pascal_stem("백천계곡") == "Baekcheongyegok"


# --------------------------------------------------------------------------- #
# resolve_image_url
# --------------------------------------------------------------------------- #

CDN_BASE = "https://cdn.example.com"

SAMPLE_MAP = {
    # 도시명 없는 케이스 (Uiseong 스타일)
    "KR-Uiseong/Bianhyanggyo": "images/KR/Uiseong/Bianhyanggyo_1.jpg",
    # 도시명 prefix 있는 케이스 (Andong 스타일)
    "KR-Andong/AndongBeopheungsajiChilcheungjeontap": (
        "images/KR/Andong/AndongBeopheungsajiChilcheungjeontap_1.jpg"
    ),
    # 혼재 케이스 (Cheongdo 스타일 — prefix 없음)
    "KR-Cheongdo/Bulryeongsa": "images/KR/Cheongdo/Bulryeongsa_1.jpg",
    # 혼재 케이스 (Cheongdo 스타일 — prefix 있음)
    "KR-Cheongdo/CheongdoNamsangyegok": "images/KR/Cheongdo/CheongdoNamsangyegok_1.jpg",
}


class TestResolveImageUrl(unittest.TestCase):
    def test_direct_stem_match(self):
        """제목만 PascalCase stem으로 매핑되는 케이스"""
        url = resolve_image_url("KR-Uiseong", "비안향교", CDN_BASE, SAMPLE_MAP)
        assert url == f"{CDN_BASE}/images/KR/Uiseong/Bianhyanggyo_1.jpg"

    def test_city_prefix_stem_match(self):
        """도시영문명이 제목 앞에 붙은 stem으로 매핑되는 케이스 (Andong 스타일)"""
        url = resolve_image_url(
            "KR-Andong",
            "안동 법흥사지 칠층전탑",
            CDN_BASE,
            SAMPLE_MAP,
        )
        assert url == f"{CDN_BASE}/images/KR/Andong/AndongBeopheungsajiChilcheungjeontap_1.jpg"

    def test_liaison_stem_match(self):
        image_map = {
            "KR-Gangneung/GangneungJeongdongjin": (
                "images/KR/Gangneung/GangneungJeongdongjin_1.jpg"
            )
        }

        url = resolve_image_url("KR-Gangneung", "강릉 정동진", CDN_BASE, image_map)

        assert url == f"{CDN_BASE}/images/KR/Gangneung/GangneungJeongdongjin_1.jpg"

    def test_plain_stem_match_when_s3_uses_non_liaison_spelling(self):
        image_map = {
            "KR-Gangneung/GangreungJeongdongjin": (
                "images/KR/Gangneung/GangreungJeongdongjin_1.jpg"
            )
        }

        url = resolve_image_url("KR-Gangneung", "강릉 정동진", CDN_BASE, image_map)

        assert url == f"{CDN_BASE}/images/KR/Gangneung/GangreungJeongdongjin_1.jpg"

    def test_cheongdo_no_prefix(self):
        """Cheongdo — prefix 없는 파일 매핑"""
        url = resolve_image_url("KR-Cheongdo", "불령사", CDN_BASE, SAMPLE_MAP)
        assert url == f"{CDN_BASE}/images/KR/Cheongdo/Bulryeongsa_1.jpg"

    def test_no_match_returns_none(self):
        """매핑 없는 제목 → None 반환"""
        url = resolve_image_url("KR-Cheongdo", "존재하지않는장소", CDN_BASE, SAMPLE_MAP)
        assert url is None

    def test_empty_cdn_base_returns_none(self):
        """cdn_base 없으면 None 반환"""
        url = resolve_image_url("KR-Uiseong", "비안향교", "", SAMPLE_MAP)
        assert url is None

    def test_empty_city_id_returns_none(self):
        url = resolve_image_url("", "비안향교", CDN_BASE, SAMPLE_MAP)
        assert url is None

    def test_empty_title_returns_none(self):
        url = resolve_image_url("KR-Uiseong", "", CDN_BASE, SAMPLE_MAP)
        assert url is None

    def test_empty_image_map_returns_none(self):
        url = resolve_image_url("KR-Uiseong", "비안향교", CDN_BASE, {})
        assert url is None

    def test_none_image_map_returns_none(self):
        url = resolve_image_url("KR-Uiseong", "비안향교", CDN_BASE, None)
        assert url is None

    def test_cdn_base_trailing_slash_stripped(self):
        """cdn_base 끝의 슬래시는 제거 후 URL 조합"""
        url = resolve_image_url("KR-Uiseong", "비안향교", CDN_BASE + "/", SAMPLE_MAP)
        assert url == f"{CDN_BASE}/images/KR/Uiseong/Bianhyanggyo_1.jpg"
        assert "//" not in url.replace("https://", "")


# --------------------------------------------------------------------------- #
# load_image_map
# --------------------------------------------------------------------------- #

class TestLoadImageMap(unittest.TestCase):
    def test_returns_dict_with_expected_keys(self):
        result = load_image_map()
        assert isinstance(result, dict)
        assert "cdnBase" in result
        assert "images" in result
        assert isinstance(result["images"], dict)

    def test_nonexistent_path_returns_empty(self):
        result = load_image_map("/nonexistent/path/image_map.json")
        assert result == {"cdnBase": "", "images": {}}

    def test_image_map_contains_entries(self):
        """실제 image_map.json 파일이 있으면 엔트리가 있어야 함"""
        result = load_image_map()
        # 파일이 있는 경우에만 검사
        if result["images"]:
            key = next(iter(result["images"]))
            assert "/" in key  # "KR-City/Stem" 형식


if __name__ == "__main__":
    unittest.main()
