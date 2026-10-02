"""提示词管理入口的凭据能力边界。"""

import os
import unittest

from fastapi import HTTPException, Response
from starlette.requests import Request

from routers.prompt_routes import content_role


class PromptPermissionTests(unittest.TestCase):
    def setUp(self):
        self.original = {key: os.environ.get(key) for key in ("DOTTY_CONTENT_EDITOR_TOKEN", "DOTTY_CONTENT_PUBLISHER_TOKEN")}
        os.environ["DOTTY_CONTENT_EDITOR_TOKEN"] = "test-editor"
        os.environ["DOTTY_CONTENT_PUBLISHER_TOKEN"] = "test-publisher"

    def tearDown(self):
        for key, value in self.original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def request(self, token=""):
        return Request({"type": "http", "headers": [(b"x-content-token", token.encode())]})

    def test_user_editor_and_publisher_receive_distinct_capabilities(self):
        """Given 独立凭据；When 访问管理；Then 服务端区分编辑和发布权限且不缓存模板。"""
        response = Response()
        self.assertEqual(content_role(self.request("test-editor"), response), "content-editor")
        self.assertEqual(content_role(self.request("test-publisher"), response), "content-publisher")
        self.assertEqual(response.headers["cache-control"], "no-store")
        with self.assertRaises(HTTPException) as context:
            content_role(self.request(), Response())
        self.assertEqual(context.exception.status_code, 403)

    def test_user_cannot_access_when_management_is_unconfigured(self):
        """Given 未配置内容凭据；When 访问管理；Then 不开放匿名编辑。"""
        os.environ.pop("DOTTY_CONTENT_EDITOR_TOKEN")
        os.environ.pop("DOTTY_CONTENT_PUBLISHER_TOKEN")
        with self.assertRaises(HTTPException) as context:
            content_role(self.request(), Response())
        self.assertEqual(context.exception.status_code, 503)

    def test_user_cannot_receive_publish_permissions_from_equal_credentials(self):
        """Given 编辑和发布误用了相同凭据；When 访问管理；Then 拒绝错误配置。"""
        os.environ["DOTTY_CONTENT_PUBLISHER_TOKEN"] = "test-editor"
        with self.assertRaises(HTTPException) as context:
            content_role(self.request("test-editor"), Response())
        self.assertEqual(context.exception.status_code, 503)
