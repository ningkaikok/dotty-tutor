"""模板迁移的输入保真与版本身份契约。"""

import hashlib
import json
import unittest
from pathlib import Path

from prompts import CATALOG, prompt_identity, render_prompt
from run_audit import build_run_config


class PromptCatalogTests(unittest.TestCase):
    def test_rendered_text_matches_pre_migration_baselines(self):
        # 基线由迁移前的 f-string 文本生成，包含首尾空白。
        baselines = json.loads(Path(__file__).with_name("prompt_baseline_hashes.json").read_text())
        self.assertEqual(set(baselines), set(CATALOG))
        for key, expected in baselines.items():
            with self.subTest(template=key):
                values = {name: f"样例_{name}" for name in CATALOG[key].variables}
                text = render_prompt(key, **values)
                self.assertEqual(hashlib.sha256(text.encode()).hexdigest(), expected)

    def test_missing_and_unexpected_variables_fail(self):
        for values in ({}, {"question_ir": "题目", "repair": "", "extra": "错误变量"}):
            with self.assertRaises(ValueError):
                render_prompt("generation.solution", **values)
        with self.assertRaises(KeyError):
            render_prompt("unknown")

    def test_student_content_is_inserted_once_without_template_execution(self):
        content = '${repair} $金额 {question_ir} 中文'
        text = render_prompt("generation.solution", question_ir=content, repair="修复")
        self.assertIn(content, text)

    def test_identity_is_content_hash_without_runtime_data(self):
        identity = prompt_identity("generation.solution")
        self.assertEqual(identity["contentHash"], hashlib.sha256(CATALOG[identity["id"]].text.encode()).hexdigest())
        config = build_run_config(model_run={"promptTemplates": [{**identity, "studentInput": "隐私"}]})
        self.assertEqual(config["model"]["promptTemplates"], [identity])
        stages = build_run_config(model_run={"stages": [{"name": "solution", "promptTemplates": [{**identity, "studentInput": "隐私"}], "text": "隐私"}]})
        self.assertEqual(stages["model"]["promptStages"], [{"name": "solution", "promptTemplates": [identity]}])
