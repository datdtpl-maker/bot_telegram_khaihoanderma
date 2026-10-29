import tempfile
import unittest
import json
import sys
import types
from pathlib import Path
from unittest.mock import patch

import notion_sync
import telegram_woocommerce_bot as bot
bot.LOG_FILE = Path(tempfile.gettempdir()) / "test_bot.log" 


class ImagePipelineTests(unittest.TestCase):
    def test_drive_download_is_compatible_with_gdown_6(self):
        fake_gdown = types.SimpleNamespace()

        def download_folder(**kwargs):
            self.assertNotIn("remaining_ok", kwargs)
            out_dir = kwargs.get("output")
            if out_dir:
                image = Path(out_dir) / "1.png"
                image.write_bytes(b"image")
                return [str(image)]
            return []

        fake_gdown.download_folder = download_folder
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(sys.modules, {"gdown": fake_gdown}):
                images = notion_sync.download_drive_folder(
                    "https://drive.google.com/drive/folders/1234567890123456789012345",
                    Path(temp_dir),
                )

        self.assertEqual([image.name for image in images], ["1.png"])

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

    def test_auto_corrects_extension_that_does_not_match_image_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            # File có đuôi .png nhưng nội dung là JPEG
            image = Path(temp_dir) / "1.png"
            image.write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF")

            info = notion_sync.inspect_image_for_upload(image)
            self.assertEqual(info.mime_type, "image/jpeg")
            self.assertEqual(info.filename, "1.jpg")

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

    @patch("notion_sync.urllib.request.urlopen")
    def test_wordpress_upload_falls_back_to_multipart_on_waf_403(self, urlopen):
        import urllib.error
        import io
        http_403 = urllib.error.HTTPError(
            url="https://example.test/wp-json/wp/v2/media",
            code=403,
            msg="Forbidden",
            hdrs={},
            fp=io.BytesIO(b"Blocked by Wordfence"),
        )
        urlopen.side_effect = [
            http_403,
            _FakeResponse({"id": 456, "mime_type": "image/webp"})
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "1.webp"
            image.write_bytes(b"RIFF\x04\x00\x00\x00WEBPVP8 ")

            media_id = notion_sync.wp_upload_media(
                {
                    "WORDPRESS_SITE_URL": "https://example.test",
                    "WORDPRESS_USERNAME": "user",
                    "WORDPRESS_PASSWORD": "password",
                },
                image,
            )

        self.assertEqual(media_id, 456)
        self.assertEqual(urlopen.call_count, 2)
        fallback_request = urlopen.call_args_list[1].args[0]
        self.assertIn("multipart/form-data", fallback_request.headers["Content-type"])

    @patch("notion_sync.wp_update_media_metadata")
    @patch("notion_sync.wp_upload_media")
    def test_upload_product_images_parallel_keeps_exact_order(self, mock_upload, mock_meta):
        def fake_upload(cfg, path):
            return int(path.stem) + 100
        mock_upload.side_effect = fake_upload
        mock_meta.return_value = True

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            images = [root / "1.jpg", root / "2.jpg", root / "3.jpg"]
            callbacks = []
            media_ids = notion_sync.upload_product_images(
                {"config": "val"},
                images,
                "Sản phẩm test",
                progress_callback=lambda msg: callbacks.append(msg),
                max_workers=3
            )

        self.assertEqual(media_ids, [101, 102, 103])
        self.assertTrue(any("Đang upload ảnh lên website" in c for c in callbacks))

    @patch("notion_sync.wp_upload_media")
    def test_upload_product_images_raises_on_failure(self, mock_upload):
        mock_upload.side_effect = [101, None, 103]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            images = [root / "1.jpg", root / "2.jpg", root / "3.jpg"]
            with patch("notion_sync.wp_update_media_metadata", return_value=True), \
                 patch("notion_sync.rollback_uploaded_media") as mock_rollback:
                with self.assertRaises(notion_sync.WorkflowValidationError) as ctx:
                    notion_sync.upload_product_images(
                        {},
                        images,
                        "Sản phẩm lỗi",
                        max_workers=2
                    )
                self.assertIn("Upload thất bại ảnh số", str(ctx.exception))
                mock_rollback.assert_called_once()
                self.assertIn(101, mock_rollback.call_args.args[1])

    @patch("notion_sync.time.sleep", return_value=None)
    @patch("notion_sync.urllib.request.urlopen")
    def test_wp_upload_media_retries_on_server_error_and_succeeds(self, mock_urlopen, mock_sleep):
        import urllib.error
        import io
        http_500 = urllib.error.HTTPError("https://example.test", 500, "Internal Server Error", {}, io.BytesIO(b""))
        http_503 = urllib.error.HTTPError("https://example.test", 503, "Service Unavailable", {}, io.BytesIO(b""))

        # Attempt 1: direct fails 500 -> multipart fails 503
        # Attempt 2: direct succeeds
        mock_urlopen.side_effect = [
            http_500,
            http_503,
            _FakeResponse({"id": 789, "mime_type": "image/webp"})
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "1.webp"
            image.write_bytes(b"RIFF\x04\x00\x00\x00WEBPVP8 ")

            media_id = notion_sync.wp_upload_media(
                {
                    "WORDPRESS_SITE_URL": "https://example.test",
                    "WORDPRESS_USERNAME": "user",
                    "WORDPRESS_PASSWORD": "password",
                },
                image,
                max_retries=3
            )

        self.assertEqual(media_id, 789)
        self.assertEqual(mock_urlopen.call_count, 3)


    def test_optimize_single_image_converts_png_to_webp(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as temp_dir:
            img_path = Path(temp_dir) / '1.png'
            im = Image.new('RGBA', (200, 200), color=(255, 0, 0, 128))
            im.save(img_path, format='PNG')

            opt = notion_sync.optimize_single_image(img_path)
            self.assertEqual(opt.suffix.lower(), '.webp')
            self.assertTrue(opt.exists())
            info = notion_sync.inspect_image_for_upload(opt)
            self.assertEqual(info.mime_type, 'image/webp')

    def test_optimize_product_images_preserves_sequence(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            files = []
            for i in range(1, 4):
                p = root / f'{i}.png'
                im = Image.new('RGB', (100, 100), color=(i * 50, i * 50, i * 50))
                im.save(p, format='PNG')
                files.append(p)

            optimized = notion_sync.optimize_product_images(files, root)
            self.assertEqual(len(optimized), 3)
            self.assertEqual([p.name for p in optimized], ['1.webp', '2.webp', '3.webp'])
            notion_sync.validate_image_sequence(optimized, root)

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

    @patch("notion_sync.urllib.request.urlopen")
    def test_update_notion_status_payload(self, mock_urlopen):
        mock_urlopen.return_value = _FakeResponse({"id": "page-123"})
        notion_sync.update_notion_status(
            "fake-token",
            "page-123",
            "https://khaihoanderma.com/product/test"
        )
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.get_method(), "PATCH")
        self.assertEqual(req.full_url, "https://api.notion.com/v1/pages/page-123")
        payload = json.loads(req.data.decode("utf-8"))
        props = payload.get("properties", {})
        self.assertEqual(props["Trạng thái"]["select"]["name"], "Đã đăng web")
        self.assertEqual(props["Facebook"]["select"]["name"], "Chưa đăng")
        self.assertEqual(props["IT đã đăng"]["checkbox"], True)
        self.assertEqual(props["Link web"]["url"], "https://khaihoanderma.com/product/test")


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


class DriveVideoEmbedTests(unittest.TestCase):
    def test_build_drive_video_embed_html(self):
        videos = [
            {"id": "video123", "name": "8.MOV"},
            {"id": "video456", "name": "7.MOV"}
        ]
        html = notion_sync.build_drive_video_embed_html(videos)
        self.assertIn("product-video-section", html)
        self.assertIn("https://drive.google.com/file/d/video123/preview", html)
        self.assertIn("https://drive.google.com/file/d/video456/preview", html)
        self.assertIn("https://drive.google.com/file/d/video123/view", html)
        self.assertIn("8.MOV", html)
        self.assertIn("<iframe", html)
        self.assertIn("margin: 0 0 28px 0", html)

    def test_build_drive_video_embed_html_empty(self):
        html = notion_sync.build_drive_video_embed_html([])
        self.assertEqual(html, "")

    def test_build_drive_video_gallery_script(self):
        videos = [
            {"id": "video123", "name": "8.MOV"},
            {"id": "video456", "name": "7.MOV"}
        ]
        script = notion_sync.build_drive_video_gallery_script(videos)
        self.assertIn("<script", script)
        self.assertIn("<style", script)
        self.assertIn("khd-gallery-video-player", script)
        self.assertIn("initKhdGalleryVideo", script)
        self.assertIn("khd-video-thumb-col", script)
        self.assertIn("khd-main-video-badge", script)
        self.assertIn("woocommerce-product-gallery", script)
        # Verify base64 decoded contents contain preview and view URLs
        import re, base64
        b64_matches = re.findall(r'window\.atob\("([^"]+)"\)', script)
        self.assertTrue(len(b64_matches) >= 1)
        decoded_all = " ".join(base64.b64decode(b).decode("utf-8") for b in b64_matches)
        self.assertIn("https://drive.google.com/file/d/video123/preview", decoded_all)
        self.assertIn("https://drive.google.com/file/d/video123/view", decoded_all)

    def test_build_drive_video_gallery_script_empty(self):
        script = notion_sync.build_drive_video_gallery_script([])
        self.assertEqual(script, "")

    def test_wp_update_product_content_mock(self):
        config = {
            "WORDPRESS_SITE_URL": "https://example.com",
            "WORDPRESS_USERNAME": "admin",
            "WORDPRESS_PASSWORD": "password123"
        }
        mock_resp = unittest.mock.MagicMock()
        mock_resp.read.return_value = json.dumps({"id": 123}).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        with patch("urllib.request.urlopen", return_value=mock_resp):
            success = notion_sync.wp_update_product_content(config, 123, "<iframe src='test'></iframe>")
            self.assertTrue(success)

    def test_scan_drive_folder_videos_mock(self):
        fake_gdown = types.SimpleNamespace()
        fake_files = [
            types.SimpleNamespace(path="1.png", id="img1"),
            types.SimpleNamespace(path="8.MOV", id="vid8"),
            types.SimpleNamespace(path="7.mp4", id="vid7")
        ]
        fake_gdown.download_folder = lambda **kwargs: fake_files
        with patch.dict(sys.modules, {"gdown": fake_gdown}):
            vids = notion_sync.scan_drive_folder_videos("https://drive.google.com/drive/folders/1234567890")
            self.assertEqual(len(vids), 2)
            # Natural sort should put 7.mp4 before 8.MOV
            self.assertEqual(vids[0]["id"], "vid7")
            self.assertEqual(vids[1]["id"], "vid8")


class MultiSiteNotionSyncTests(unittest.TestCase):
    def test_site_sync_config_derma_and_pharma(self):
        cfg = {
            "WORDPRESS_SITE_URL": "https://khaihoanderma.com",
            "WORDPRESS_USERNAME": "derma_admin",
            "WORDPRESS_PASSWORD": "derma_password",
            "WOOCOMMERCE_CONSUMER_KEY": "ck_derma",
            "WOOCOMMERCE_CONSUMER_SECRET": "cs_derma",
            "NOTION_DATABASE_ID": "9788b8a031cc42d391be4e26d6b8c8e8",
            "PHARMA_SITE_URL": "https://khaihoanpharma.com",
            "PHARMA_USERNAME": "pharma_admin",
            "PHARMA_PASSWORD": "pharma_password",
            "PHARMA_NOTION_DATABASE_ID": "40870655a9aa8150a9c0e01105450e7e",
        }
        derma_sync = notion_sync.get_site_sync_config(cfg, "derma")
        self.assertEqual(derma_sync["site_key"], "derma")
        self.assertEqual(derma_sync["WORDPRESS_SITE_URL"], "https://khaihoanderma.com")
        self.assertEqual(derma_sync["WORDPRESS_USERNAME"], "derma_admin")

        pharma_sync = notion_sync.get_site_sync_config(cfg, "pharma")
        self.assertEqual(pharma_sync["site_key"], "pharma")
        self.assertEqual(pharma_sync["WORDPRESS_SITE_URL"], "https://khaihoanpharma.com")
        self.assertEqual(pharma_sync["WORDPRESS_USERNAME"], "pharma_admin")
        self.assertEqual(pharma_sync["WOOCOMMERCE_CONSUMER_KEY"], "pharma_admin")

    def test_determine_page_site_key(self):
        cfg = {
            "NOTION_DATABASE_ID": "9788b8a031cc42d391be4e26d6b8c8e8",
            "PHARMA_NOTION_DATABASE_ID": "40870655a9aa8150a9c0e01105450e7e",
        }
        pharma_page = {
            "id": "p-1",
            "parent": {"type": "database_id", "database_id": "40870655-a9aa-8150-a9c0-e01105450e7e"}
        }
        self.assertEqual(notion_sync.determine_page_site_key(pharma_page, cfg), "pharma")

        derma_page = {
            "id": "p-2",
            "parent": {"type": "database_id", "database_id": "9788b8a0-31cc-42d3-91be-4e26d6b8c8e8"}
        }
        self.assertEqual(notion_sync.determine_page_site_key(derma_page, cfg), "derma")

    @patch("notion_sync.urllib.request.urlopen")
    def test_update_notion_status_omits_facebook_for_pharma(self, mock_urlopen):
        mock_urlopen.return_value = _FakeResponse({"id": "pharma-page-1"})
        notion_sync.update_notion_status(
            token="token123",
            page_id="pharma-page-1",
            product_url="https://khaihoanpharma.com/product/test-imiquad",
            page_properties={"Trạng thái": {}, "Link web": {}, "IT đã đăng": {}}
        )
        req = mock_urlopen.call_args[0][0]
        payload = json.loads(req.data.decode("utf-8"))
        props = payload.get("properties", {})
        self.assertIn("Trạng thái", props)
        self.assertIn("Link web", props)
        self.assertIn("IT đã đăng", props)
        self.assertNotIn("Facebook", props)


if __name__ == "__main__":
    unittest.main()
