from __future__ import annotations

import unittest
from unittest.mock import patch

from app.integrations import NaverImageSearchClient


class NaverImageSearchClientTests(unittest.TestCase):
    def test_api_hub_image_search_prioritizes_place_images_and_returns_four(self) -> None:
        captured: dict = {}

        def fake_get_json(url: str, headers: dict[str, str], timeout: int) -> dict:
            captured.update({"url": url, "headers": headers, "timeout": timeout})
            return {
                "items": [
                    {
                        "title": "다른 음식점",
                        "link": "https://example.com/wrong.jpg",
                        "thumbnail": "https://search.pstatic.net/wrong.jpg",
                        "sizewidth": "1600",
                        "sizeheight": "900",
                    },
                    {
                        "title": "<b>테스트식당</b> 내부",
                        "link": "https://example.com/interior.jpg",
                        "thumbnail": "https://search.pstatic.net/interior.jpg",
                    },
                    {
                        "title": "부산 테스트식당 대표 메뉴",
                        "link": "https://example.com/menu.jpg",
                        "thumbnail": "https://search.pstatic.net/menu.jpg",
                    },
                    {
                        "title": "테스트식당 업체 사진",
                        "link": "https://ldb-phinf.pstatic.net/place.jpg",
                        "thumbnail": "https://search.pstatic.net/place.jpg",
                    },
                    {
                        "title": "테스트식당 외관",
                        "link": "https://example.com/exterior.jpg",
                        "thumbnail": "https://search.pstatic.net/exterior.jpg",
                    },
                    {
                        "title": "테스트식당 중복",
                        "link": "https://example.com/menu.jpg",
                        "thumbnail": "https://search.pstatic.net/menu-duplicate.jpg",
                    },
                ]
            }

        client = NaverImageSearchClient("hub-id", "hub-secret", api_hub=True)
        with patch("app.integrations._get_json", side_effect=fake_get_json):
            images = client.search_restaurant_images(
                "테스트식당",
                "부산광역시 연제구 중앙대로 1001 2층",
            )

        self.assertEqual(len(images), 4)
        self.assertEqual(images[0]["source_url"], "https://ldb-phinf.pstatic.net/place.jpg")
        self.assertTrue(images[0]["is_naver_place_image"])
        self.assertEqual(
            [image["source_url"] for image in images[1:]],
            [
                "https://example.com/interior.jpg",
                "https://example.com/menu.jpg",
                "https://example.com/exterior.jpg",
            ],
        )
        self.assertTrue(captured["url"].startswith(
            "https://naverapihub.apigw.ntruss.com/search/v1/image?"
        ))
        self.assertIn("display=20", captured["url"])
        self.assertEqual(captured["headers"]["X-NCP-APIGW-API-KEY-ID"], "hub-id")
        self.assertEqual(captured["headers"]["X-NCP-APIGW-API-KEY"], "hub-secret")
        self.assertEqual(captured["timeout"], 4)

    def test_image_search_rejects_unmatched_or_unsafe_results(self) -> None:
        client = NaverImageSearchClient("legacy-id", "legacy-secret")
        with patch(
            "app.integrations._get_json",
            return_value={
                "items": [
                    {
                        "title": "테스트식당",
                        "link": "javascript:alert(1)",
                        "thumbnail": "data:image/png;base64,abc",
                    },
                    {
                        "title": "이름이 다른 식당",
                        "link": "https://example.com/other.jpg",
                        "thumbnail": "https://search.pstatic.net/other.jpg",
                    },
                ]
            },
        ):
            image = client.search_restaurant_image("테스트식당", "부산광역시 연제구")

        self.assertIsNone(image)


if __name__ == "__main__":
    unittest.main()
