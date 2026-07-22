import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

import notion_sync


class ImagePipelineTests(unittest.TestCase):
    def test_natural_image_sort_keeps_numeric_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            images = [root / "10.jpg", root / "2.jpg", root / "1.jpg"]

            ordered = notion_sync.sort_product_images(images, root)

            self.assertEqual([path.name for path in ordered], ["1.jpg", "2.jpg", "10.jpg"])

    def test_webp_uses_webp_mime_and_ascii_upload_name(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "Ảnh sản phẩm 1.webp"
            image.write_bytes(b"RIFF\x04\x00\x00\x00WEBPVP8 ")

            upload = notion_sync.inspect_image_for_upload(image)

            self.assertEqual(upload.mime_type, "image/webp")
            self.assertEqual(upload.filename, "anh-san-pham-1.webp")

    def test_rejects_extension_that_does_not_match_image_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "1.jpg"
            image.write_bytes(b"RIFF\x04\x00\x00\x00WEBPVP8 ")

            with self.assertRaises(notion_sync.WorkflowValidationError):
                notion_sync.inspect_image_for_upload(image)

    @patch("notion_sync.urllib.request.urlopen")
    def test_wordpress_upload_uses_verified_mime_and_safe_filename(self, urlopen):
        urlopen.return_value = _FakeResponse({"id": 123, "mime_type": "image/webp"})
        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "Ảnh số 1.webp"
            image.write_bytes(b"RIFF\x04\x00\x00\x00WEBPVP8 ")

            media_id = notion_sync.wp_upload_media(
                {
                    "WORDPRESS_SITE_URL": "https://example.test",
                    "WORDPRESS_USERNAME": "user",
                    "WORDPRESS_PASSWORD": "password",
                },
                image,
            )

        request = urlopen.call_args.args[0]
        self.assertEqual(media_id, 123)
        self.assertEqual(request.headers["Content-type"], "image/webp")
        self.assertEqual(request.headers["Content-disposition"], 'attachment; filename="anh-so-1.webp"')

    def test_image_sequence_requires_one_and_contiguous_numbers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaises(notion_sync.WorkflowValidationError):
                notion_sync.validate_image_sequence([root / "2.jpg"], root)
            with self.assertRaises(notion_sync.WorkflowValidationError):
                notion_sync.validate_image_sequence([root / "1.jpg", root / "3.jpg"], root)

    def test_image_sequence_rejects_duplicate_number_with_different_extension(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaises(notion_sync.WorkflowValidationError):
                notion_sync.validate_image_sequence([root / "1.jpg", root / "1.png"], root)


class ProductValidationTests(unittest.TestCase):
    def test_catalog_metadata_can_be_on_separate_notion_lines(self):
        blocks = [
            {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Danh mục sản phẩm: Chăm sóc da", "annotations": {}}]}},
            {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Giá: 280k", "annotations": {}}]}},
            {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Giá khuyến mãi: 250.000", "annotations": {}}]}},
            {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Mô tả chính", "annotations": {}}]}},
        ]

        description, category, regular_price, sale_price = notion_sync.parse_notion_blocks(blocks)

        self.assertEqual(category, "Chăm sóc da")
        self.assertEqual(regular_price, 280000)
        self.assertEqual(sale_price, 250000)
        self.assertEqual(description, "<p>Mô tả chính</p>")

    def test_product_requires_image_category_description_and_positive_price(self):
        invalid_cases = [
            {"title": "Sản phẩm", "description": "Mô tả", "category": "Da", "regular_price": 100, "image_count": 0},
            {"title": "Sản phẩm", "description": "Mô tả", "category": "Da", "regular_price": 0, "image_count": 1},
            {"title": "Sản phẩm", "description": "", "category": "Da", "regular_price": 100, "image_count": 1},
            {"title": "Sản phẩm", "description": "Mô tả", "category": "", "regular_price": 100, "image_count": 1},
        ]

        for case in invalid_cases:
            with self.subTest(case=case):
                with self.assertRaises(notion_sync.WorkflowValidationError):
                    notion_sync.validate_product_for_publish(**case, sale_price=0)

    def test_sale_price_must_be_lower_than_regular_price(self):
        with self.assertRaises(notion_sync.WorkflowValidationError):
            notion_sync.validate_product_for_publish(
                title="Sản phẩm",
                description="Mô tả",
                category="Da",
                regular_price=100,
                sale_price=100,
                image_count=1,
            )

    def test_notion_page_id_produces_stable_unique_sku(self):
        self.assertEqual(
            notion_sync.build_notion_sku("12345678-1234-1234-1234-123456789abc"),
            "notion-12345678123412341234123456789abc",
        )

    def test_existing_draft_requires_expected_image_metadata(self):
        product = {
            "name": "Sản phẩm",
            "description": "Mô tả",
            "regular_price": "100000",
            "sale_price": "90000",
            "categories": [{"name": "Chăm sóc da"}],
            "images": [{"id": 11}],
            "meta_data": [],
        }

        with self.assertRaises(notion_sync.WorkflowValidationError):
            notion_sync.validate_existing_product_for_recovery(product)

    def test_recovered_product_must_belong_to_current_sync_attempt(self):
        product = {
            "meta_data": [
                {"key": "_khd_notion_page_id", "value": "page-1"},
                {"key": "_khd_sync_attempt_id", "value": "other-attempt"},
            ]
        }

        with self.assertRaises(notion_sync.WorkflowValidationError):
            notion_sync.validate_sync_attempt_ownership(product, "page-1", "current-attempt")

    def test_publish_confirmation_rejects_truthy_draft_response(self):
        with self.assertRaises(RuntimeError):
            notion_sync.require_published_product({"id": 123, "status": "draft"})


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class NotionPaginationTests(unittest.TestCase):
    @patch("notion_sync.urllib.request.urlopen")
    def test_reads_all_database_pages(self, urlopen):
        urlopen.side_effect = [
            _FakeResponse({"results": [{"id": "page-1"}], "has_more": True, "next_cursor": "cursor-2"}),
            _FakeResponse({"results": [{"id": "page-2"}], "has_more": False, "next_cursor": None}),
        ]

        pages = notion_sync.query_notion_pages_to_post("token", "database")

        self.assertEqual([page["id"] for page in pages], ["page-1", "page-2"])
        second_request_body = json.loads(urlopen.call_args_list[1].args[0].data.decode("utf-8"))
        self.assertEqual(second_request_body["start_cursor"], "cursor-2")

    @patch("notion_sync.urllib.request.urlopen")
    def test_reads_all_content_blocks(self, urlopen):
        urlopen.side_effect = [
            _FakeResponse({"results": [{"id": "block-1"}], "has_more": True, "next_cursor": "cursor-2"}),
            _FakeResponse({"results": [{"id": "block-2"}], "has_more": False, "next_cursor": None}),
        ]

        blocks = notion_sync.get_page_blocks("token", "page")

        self.assertEqual([block["id"] for block in blocks], ["block-1", "block-2"])
        self.assertIn("start_cursor=cursor-2", urlopen.call_args_list[1].args[0].full_url)


class WorkflowLockTests(unittest.TestCase):
    def test_second_sync_is_rejected_while_first_sync_holds_lock(self):
        acquired = notion_sync.NOTION_SYNC_LOCK.acquire(blocking=False)
        self.assertTrue(acquired)
        try:
            result = notion_sync.run_notion_sync_workflow()
        finally:
            notion_sync.NOTION_SYNC_LOCK.release()

        self.assertEqual(result["status"], "busy")

    def test_file_lock_blocks_second_process_on_same_machine(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "sync.lock"
            first = notion_sync.ProcessFileLock(lock_path)
            second = notion_sync.ProcessFileLock(lock_path)
            self.assertTrue(first.acquire())
            try:
                self.assertFalse(second.acquire())
            finally:
                first.release()


if __name__ == "__main__":
    unittest.main()
