"""OCR 题块边界与公式保守修复的回归测试。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from domain.questions.pipeline import (
    apply_question_quality_gate,
    normalize_model_math_text,
    write_model_prompt_artifact,
)
from domain.questions.source import (
    QUESTION_SEGMENTATION_VERSION,
    is_likely_exam_instruction,
    split_question_sources,
)


class QuestionSegmentationTests(unittest.TestCase):
    def test_ignores_numbered_exam_instructions_before_question_section(self) -> None:
        source = """
        注意事项：
        1. 本试卷共 6 页，满分 120 分。
        2. 请认真核对姓名和准考证号。
        3. 答选择题必须用铅笔涂黑。
        一、选择题（本大题共 6 小题）
        1. 根号下 9/4 的值等于（ ）
        A. 3/2 B. -3/2 C. ±3/2 D. 81/16
        2. 计算 a^3·(a^3)^2 的结果是（ ）
        A. a^8 B. a^9 C. a^12 D. a^18
        """
        blocks = split_question_sources(source)
        self.assertEqual([number for number, _, _ in blocks], ["1", "2"])
        self.assertNotIn("注意事项", blocks[0][1])
        self.assertNotIn("准考证", blocks[0][1])

    def test_handles_ocr_whitespace_and_line_breaks_in_section_heading(self) -> None:
        source = """
        注 意 事 项：本试卷共 6 页，考试时间 120 分钟。
        一 、 选
        择 题（每题只有一个正确答案）
        1．下列各数中比 1 大的是（ ）
        A．2 B．0 C．1 D．3
        """
        blocks = split_question_sources(source)
        self.assertEqual([number for number, _, _ in blocks], ["1"])
        self.assertNotIn("考试时间", blocks[0][1])
        self.assertIn("下列各数", blocks[0][1])

    def test_accepts_markdown_heading_prefix_from_ocr_export(self) -> None:
        source = """
        # 一、选择题（每题 2 分）
        1. 下列各数中比 1 大的是（ ）
        A. 2 B. 0 C. 1 D. 3
        """
        blocks = split_question_sources(source)
        self.assertEqual([number for number, _, _ in blocks], ["1"])
        self.assertNotIn("选择题", blocks[0][1])

    def test_skips_numbered_exam_instructions_when_section_heading_is_missing(self) -> None:
        # 分页 OCR 可能把“一、选择题”留在上一页。题号会重复出现，不能按数字去重，
        # 必须先做说明块语义分类，再从第一个真实题块开始切分。
        source = """
        注意事项：
        1. 本试卷共 6 页，满分 120 分，考试时间 120 分钟。
        2. 请认真核对监考教师在答题卡上所粘贴条形码的姓名、考试证号。
        3. 答选择题必须用 2B 铅笔将答题卡上的答案标号涂黑。
        4. 作图必须用 2B 铅笔作答，并请加黑加粗，描写清楚。
        1. 根号下 9/4 的值等于（ ）
        A. 3/2 B. -3/2 C. ±3/2 D. 81/16
        2. 计算 a^3·(a^3)^2 的结果是（ ）
        A. a^8 B. a^9 C. a^12 D. a^18
        """
        blocks = split_question_sources(source)
        self.assertEqual([number for number, _, _ in blocks], ["1", "2"])
        self.assertNotIn("注意事项", blocks[0][1])
        self.assertNotIn("条形码", blocks[0][1])

    def test_classifies_instruction_without_question_evidence(self) -> None:
        self.assertTrue(is_likely_exam_instruction("注意事项：请核对准考证号，答题卡涂黑后答案方可有效。"))
        self.assertFalse(is_likely_exam_instruction("1. 计算 3+4 的结果是（ ）。"))

    def test_keeps_nested_questions_and_merges_repeated_cross_page_number(self) -> None:
        source = """
第 12 题 如图完成下列问题。
(1) 求 $x$ 的值。
![](images/p12-a.png)
<!-- page 2 -->
12．(2) 说明理由。
![](images/p12-b.png)
【13】计算 $3+4$。
# 参考答案与解析
12．(1) 略；(2) 略。
"""
        blocks = split_question_sources(source)
        self.assertEqual([number for number, _, _ in blocks], ["12", "13"])
        self.assertIn("(1) 求", blocks[0][1])
        self.assertIn("(2) 说明", blocks[0][1])
        self.assertEqual(blocks[0][2], ["images/p12-a.png", "images/p12-b.png"])
        self.assertNotIn("参考答案", "\n".join(block for _, block, _ in blocks))

    def test_stops_a_question_at_inline_answer_or_analysis_line(self) -> None:
        source = """7、解方程 $x+1=2$。
答案：$x=1$
8、下一题。"""
        blocks = split_question_sources(source)
        self.assertEqual([number for number, _, _ in blocks], ["7", "8"])
        self.assertNotIn("答案", blocks[0][1])

    def test_does_not_attach_all_page_images_to_an_ambiguous_visual_question(self) -> None:
        source = "<!-- page 1 -->\n![](images/figure-a.png)\n![](images/figure-b.png)\n3. 如图判断正误。\n4. 下一题。"
        blocks = split_question_sources(source)
        self.assertEqual(blocks[0][2], [])

    def test_normalizes_known_formula_damage_without_changing_numbers(self) -> None:
        normalized = normalize_model_math_text(
            r"$25℃＋3×4＝50％，\begin array {cc}a&b\end array$"
        )
        self.assertIn(r"25^{\circ}\mathrm{C}+3\times 4=50%", normalized)
        self.assertIn(r"\begin{array}{cc}a&b\end{array}", normalized)

    def test_quality_error_contains_formula_evidence(self) -> None:
        payload = {"question": {
            "questionNumber": "9",
            "prompt": r"计算 $\begin array x$。",
            "options": [],
            "imageUrls": [],
        }}
        quality = apply_question_quality_gate(payload, r"9、计算。", [])
        self.assertEqual(quality["status"], "needs_review")
        self.assertTrue(any("环境不完整" in error and "begin=" in error for error in quality["errors"]))

    def test_quality_gate_rejects_exam_instruction_source(self) -> None:
        payload = {"question": {
            "questionNumber": "1",
            "prompt": "请核对准考证号。",
            "options": [],
            "imageUrls": [],
            "contentBlocks": [{"type": "text", "text": "请核对准考证号。"}],
        }}
        quality = apply_question_quality_gate(
            payload,
            "1. 本试卷共 6 页，考试时间 120 分钟，请核对准考证号。",
            [],
        )
        self.assertEqual(quality["status"], "needs_review")
        self.assertTrue(any("考试说明" in error for error in quality["errors"]))
        self.assertEqual(quality["validatorVersion"], "p0-v5")

    def test_real_exam_notice_never_enters_prompt_artifact(self) -> None:
        """回放真实 OCR 形态，防止修复只停留在切分函数而再次污染模型提示词。"""
        source = """
# 南京市2018年初中毕业生学业考试
## 注意事项：
1. 本试卷共6页.全卷满分 120分.考试时间为120分钟.考生答题全部答在答题卡上，答在本试卷上无效.
2. 请认真核对监考教师在答题卡上所粘贴条形码的姓名、考试证号是否与本人相符合，再将自己的姓名、考试证号用0.5毫米黑色墨水签字笔填写在答题卡及本试卷上.
3. 答选择题必须用2B铅笔将答题卡上对应的答案标号涂黑.
## 一、选择题（本大题共6小题）
1. $\\sqrt{9/4}$ 的值等于 A. $3/2$ B. $-3/2$ C. $\\pm3/2$ D. $81/16$
"""
        blocks = split_question_sources(source)
        self.assertEqual([number for number, _, _ in blocks], ["1"])
        self.assertNotIn("监考教师", blocks[0][1])
        with TemporaryDirectory() as directory:
            artifact = write_model_prompt_artifact(Path(directory), blocks)
            prompt = artifact.read_text(encoding="utf-8")
            self.assertIn(QUESTION_SEGMENTATION_VERSION, prompt)
            self.assertNotIn("请认真核对监考教师", prompt)


# 真实 OCR 原文摘录（教材 4ce09635dafb42ada0343477f6424441，batch-001，第 1-17 题）。
# 第 8 题最后一个选项末尾没有换行就直接粘着"9."，同样的事对"10."又发生了一次，
# 8/9/10 被合并成一个巨大区块；随后紧跟的四张图分别标注"第5题图""第6题图"
# "第9题图""第10题图"，其实分属另外几道题，却被当成了合并区块（题号"8"）的图片。
# 第 11、16 题之间隔着"## 二、填空题"分节标题，两张图分别标注"第11题图""第16题图"，
# 却都被按文本位置分给了第 11 题。
_REAL_CAPTION_ATTRIBUTION_OCR_EXCERPT = r"""## 一、选择题（每小题 3分，共30分)

1. 《九章算术》中注有“今两算得失相反，要令正负以名之”，意思是：今有两数若其意义相反，则分别叫作正数与负数．若气温为零上 10℃记作+ $1 0 \%$ ，则-3℃表示气温为( ）
A. 零上 3℃ B. 零下 3℃ C. 零上 7℃ D. 零下 7℃

2. 不等式 4-2x>0 的解集在数轴上表示为( ）

![](images/670f763f0fb5a03a28245aedfdc8aeef8e34c69ec368fa5966ae12461453facd.jpg)

A

![](images/68fa95e493b5363bd7d3ca5fc0171124ee52f2130ac46142dc0e098d6b901e14.jpg)

B

![](images/cfc3e7f481db6b5810376482ab9ab0b5a12c8d1f4177b092e3618e5c60f592e2.jpg)

C

![](images/a69c7062ea6804a43a450c41caf21f7a7731b3403b3f2c637593aa56c1ec6a3f.jpg)
D

3. 下列运算正确的是 A. 3 m−2m= 1 B. $\begin{array} { l } { { \tt \overrightarrow { E } ^ { \alpha } ( \alpha ) ~ } } \\ { { ( \alpha ^ { 3 } ) ~ ^ { 2 } = \alpha ^ { 6 } } } \end{array}$
C. $( - 2 \mathsf { m } ) ^ { 3 } = - 2 \mathsf { m } ^ { 3 }$ D. ${ \mathfrak { m } } ^ { 2 } + { \mathfrak { m } } ^ { 2 } = { \mathfrak { m } }$

4. 如图所示的几何体的俯视图为( )

![](images/da7b9ae38ec3b597cd376d76a450f4c32146c5646547a0a7afe22f49310bbda9.jpg)
主视方向

![](images/82085fe29ee250085ac28f6ac89c5e972138c262b2701915dcabbe9c7d70dea9.jpg)

A

![](images/f73ce0b05b5d4eaad1fd1d9a60c009804104acc4dcefe69646618b65dbfc9577.jpg)

B

![](images/805e585018f5f9595bc3f2bb1c11271bf1b4942a22f4a9408619b762fdfc3bf9.jpg)

C

![](images/a92e2cb8c97fa90e2859dd42514ca45670a6d12a41591ceb00de5bbba80cefa7.jpg)

D

5. 某校举行“汉字听写比赛”，5个班级代表队的正确答题数如图．这 5个正确
答题数所组成的一组数据的中位数和众数分别是( ）
A. 10, 15 B. 13, 15 C. 13, 20 D. 15, 15
6. 如图，在？ABCD中，连接 $A G \angle A B G = \angle C A D = 4 5 ^ { \circ }$ $A B = 2$ ，则 BC的长是
（）
A. $\sqrt { 2 }$ B. 2 C. $2 \sqrt { 2 }$ D. 4
7. 若△ ABQ的每条边长增加各自的 10%得 $\triangle \sf { A } ^ { \prime } \sf { B } ^ { \prime } \ c ^ { \prime }$ ，则 $\angle \mathsf { B } ^ { \prime }$ 的度数与其对应
角∠B的度数相比(
A. 增加了 10% B. 减少了 10%
C. 增加了 $( 1 + 1 0 \% )$ D. 没有改变
8. 如果点 A( x1，y1)和点 B( $\mathsf { X } _ { 2 9 } \mathrm { ~ \ } \mathsf { y } _ { 2 } )$ 是直线 $\mathsf { y } = \mathsf { k } \mathsf { x } - \mathsf { b }$ 上的两点，且当 $\mathsf { X } _ { 1 } < \mathsf { X } _ { 2 }$ 时，
$\mathsf { y } _ { 1 } < \mathsf { y } _ { 2 }$ ，那么函数 $\ y = \frac { k } { x }$ 的图象位于( ）
A. 一、四象限 B. 二、四象限
C. 三、四象限 D. 一、三象限9. 如图，在 $\mathsf { R t \triangle A B C P }$ $\angle A C B = 9 0 ^ { \circ }$ $\angle A = 5 6 ^ { \circ }$ . 以 BC为直径的 O交 AB于点D.E是⊙O上一点，且 $C E = C D$ 连接 OE过点 E作 EF⊥OE 交 AC的延长线于点F，则∠F的度数为(）
A. 92° B. 108° C. 112° D. $1 2 4 ^ { \circ }$ 10. 如图，抛物线 $y _ { 1 } = \frac { 1 } { 2 } ( x + 1 ) ^ { 2 } + 1$ 与 $y _ { 2 } = a ( x - 4 ) ^ { 2 } - 3$ 交于点 A(1，3)，过点A作×轴的平行线，分别交两条抛物线于B、C两点，且D、E分别为顶点．则下列结论：① $\mathsf { a } = \frac { 2 } { 3 } ;$ ② $\mathsf { A C } \equiv \mathsf { A E }$ ③△ ABD是等腰直角三角形；④当 x >1 时， $y _ { 1 } > y _ { 2 }$ 其中正确结论的个数是( ）
A. 1 个 B. 2 个 C. 3 个 D. 4 个

![](images/547a74e3345f6b60c9a10d2801e2a69d036e7f200357915dfd4b4818a7871bbe.jpg)
第5题图

![](images/b90c7684d2d22c333b87981d53745f28d4c5dbd8f899553bc67979e72ad9d5dc.jpg)
第6题图

![](images/a7ef059df3190d7016965d2ac0a69c365703839563753a4e86c225319dcb5d36.jpg)
第9题图

![](images/cec687dd5cf3306d9206444693a62eb3b5ca431a588d93c301f28eacc02eeb57.jpg)
第10题图

## 二、填空题（每小题3分，共24分)

11. 如图所示，在 Rt△ABO中，∠ B=

![](images/7a8d6eb93bdb7c0ffe43f4d3fe5d58e0969fbe854259664aef6d64dbb386af81.jpg)
第11题图

![](images/765e1ec9e5d47ebc51c073517fd8207820821b959527457f6eac454c91ed991c.jpg)
第16题图

12.《“一带一路”贸易合作大数据报告(2017)》以“一带一路”贸易合作现状分析和趋势预测为核心，采集调用了8000多个种类，总计1.2亿条全球进出口贸易基础数据…，1.2 亿用科学记数法表示为

13. 化简： $\frac { x } { ( x - 3 } + \frac { 2 } { 3 - x } ) \cdot \frac { x - 3 } { x - 2 } =$

14. 当x= 时，二次函数 $y = x ^ { 2 } - 2 x + 6$ 有最小值

15. 方程 $3 \mathsf { x } ( \mathsf { x } - 1 ) = 2 ( \mathsf { x } - 1 )$ 的解为

16. 如图，B 在 AC上，D在 CE上， $A D = B D = B G \angle A C E = 2 5 ^ { \circ }$ ，则 $\angle A D E =$

17. 从 − 1，2， 3， - 6 这四个数中任选两数，分别记作 m n，那么点( m n) 在函数 $\mathsf { y } = \frac { 6 } { \mathsf { x } }$ 图象上的概率是
"""


class CaptionBasedImageAttributionTests(unittest.TestCase):
    """回归证据 C（也顺带验证证据 B 里的错误图片绑定得到缓解）。"""

    def test_explicit_caption_wins_over_text_position_for_adjacent_questions(self) -> None:
        blocks = split_question_sources(_REAL_CAPTION_ATTRIBUTION_OCR_EXCERPT)
        images_by_number = {number: images for number, _block, images in blocks}
        # 第 11 题只保留自己的图（7a8d6eb9...），不再包含"第16题图"那张（765e1ec9...）；
        # 纯文本位置逻辑会把两张图都分给第 11 题，因为第 16 题的正文出现在它们之后。
        self.assertEqual(
            images_by_number["11"],
            ["images/7a8d6eb93bdb7c0ffe43f4d3fe5d58e0969fbe854259664aef6d64dbb386af81.jpg"],
        )
        # 第 16 题拿到明确标注属于自己的那张图。
        self.assertEqual(
            images_by_number["16"],
            ["images/765e1ec9e5d47ebc51c073517fd8207820821b959527457f6eac454c91ed991c.jpg"],
        )

    def test_removes_captioned_images_that_belong_to_a_question_never_split_out(self) -> None:
        blocks = split_question_sources(_REAL_CAPTION_ATTRIBUTION_OCR_EXCERPT)
        images_by_number = {number: images for number, _block, images in blocks}
        # 第 8 题末尾没有换行就直接粘着"9."，同样的事对"10."又发生了一次；OCR 切分
        # 没有把 9、10 识别成独立块，所以它们不在 images_by_number 里。
        self.assertNotIn("9", images_by_number)
        self.assertNotIn("10", images_by_number)
        # 修复前，紧跟在合并区块后面的四张图（标注第5/6/9/10题图）全部被分给了第 8 题。
        # 第 5、6 题号本身是独立块，图注命中后会被正确移动过去；9、10 题号没有对应的
        # 独立块，没有安全的落点，只做移除，不猜测归属。
        self.assertNotIn(
            "images/a7ef059df3190d7016965d2ac0a69c365703839563753a4e86c225319dcb5d36.jpg",
            images_by_number["8"],
        )
        self.assertNotIn(
            "images/cec687dd5cf3306d9206444693a62eb3b5ca431a588d93c301f28eacc02eeb57.jpg",
            images_by_number["8"],
        )
        self.assertEqual(images_by_number["8"], [])
        self.assertEqual(
            images_by_number["5"],
            ["images/547a74e3345f6b60c9a10d2801e2a69d036e7f200357915dfd4b4818a7871bbe.jpg"],
        )
        self.assertEqual(
            images_by_number["6"],
            ["images/b90c7684d2d22c333b87981d53745f28d4c5dbd8f899553bc67979e72ad9d5dc.jpg"],
        )


# 真实 MinerU content_list.json 片段（同一本教材 4ce09635dafb42ada0343477f6424441，
# 用本机 .mineru-venv 对 source.pdf 第 1-5 页实际解析后摘取，未编造）。只保留
# image/chart 类型的块，字段名和取值都是原样保留：image_caption/chart_caption
# 是列表，"第5题图""第6题图""第9题图""第10题图""第11题图""第16题图"
# 是 MinerU 自己识别出的图注文字，img_path 和 _REAL_CAPTION_ATTRIBUTION_OCR_EXCERPT
# 里的 Markdown 图片路径一一对应。
_REAL_CONTENT_LIST_JSON_EXCERPT = [
    {
        "type": "image",
        "img_path": "images/670f763f0fb5a03a28245aedfdc8aeef8e34c69ec368fa5966ae12461453facd.jpg",
        "image_caption": [],
        "image_footnote": [],
        "bbox": [144, 310, 276, 336],
        "page_idx": 0,
    },
    {
        "type": "chart",
        "img_path": "images/547a74e3345f6b60c9a10d2801e2a69d036e7f200357915dfd4b4818a7871bbe.jpg",
        "content": "",
        "chart_caption": ["第5题图"],
        "chart_footnote": [],
        "bbox": [148, 623, 355, 691],
        "page_idx": 0,
    },
    {
        "type": "image",
        "img_path": "images/b90c7684d2d22c333b87981d53745f28d4c5dbd8f899553bc67979e72ad9d5dc.jpg",
        "image_caption": ["第6题图"],
        "image_footnote": [],
        "bbox": [476, 642, 666, 691],
        "page_idx": 0,
    },
    {
        "type": "image",
        "img_path": "images/a7ef059df3190d7016965d2ac0a69c365703839563753a4e86c225319dcb5d36.jpg",
        "image_caption": ["第9题图"],
        "image_footnote": [],
        "bbox": [149, 314, 265, 397],
        "page_idx": 1,
    },
    {
        "type": "image",
        "img_path": "images/cec687dd5cf3306d9206444693a62eb3b5ca431a588d93c301f28eacc02eeb57.jpg",
        "image_caption": ["第10题图"],
        "image_footnote": [],
        "bbox": [322, 313, 453, 395],
        "page_idx": 1,
    },
    {
        "type": "image",
        "img_path": "images/7a8d6eb93bdb7c0ffe43f4d3fe5d58e0969fbe854259664aef6d64dbb386af81.jpg",
        "image_caption": ["第11题图"],
        "image_footnote": [],
        "bbox": [147, 660, 220, 735],
        "page_idx": 1,
    },
    {
        "type": "image",
        "img_path": "images/765e1ec9e5d47ebc51c073517fd8207820821b959527457f6eac454c91ed991c.jpg",
        "image_caption": ["第16题图"],
        "image_footnote": [],
        "bbox": [317, 671, 535, 734],
        "page_idx": 1,
    },
]


class StructuredCaptionAttributionTests(unittest.TestCase):
    """PR B：优先用 content_list.json 的结构化图注字段做归属，而不是纯正则猜。"""

    def _write_content_list(self, directory: Path, payload) -> None:
        (directory / "source.content_list.json").write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )

    def test_structured_content_list_matches_regex_result_for_known_bad_samples(self) -> None:
        """结构化路径对本文档已验证过的真实坏样本得到和纯正则一致的结果。

        9、10 题号本身因为题号粘连没有被切分成独立块（这是 PR C 要修的问题，不是
        本 PR 的范围），所以即使结构化数据里写明了"第9题图""第10题图"，这两张图
        依然没有安全的落点，只能被移除——这和纯正则路径的行为完全一致。
        """
        with TemporaryDirectory() as directory:
            asset_dir = Path(directory)
            self._write_content_list(asset_dir, _REAL_CONTENT_LIST_JSON_EXCERPT)
            blocks = split_question_sources(
                _REAL_CAPTION_ATTRIBUTION_OCR_EXCERPT, asset_dir=asset_dir
            )
        images_by_number = {number: images for number, _block, images in blocks}
        self.assertEqual(
            images_by_number["5"],
            ["images/547a74e3345f6b60c9a10d2801e2a69d036e7f200357915dfd4b4818a7871bbe.jpg"],
        )
        self.assertEqual(
            images_by_number["6"],
            ["images/b90c7684d2d22c333b87981d53745f28d4c5dbd8f899553bc67979e72ad9d5dc.jpg"],
        )
        self.assertEqual(images_by_number["8"], [])
        self.assertNotIn("9", images_by_number)
        self.assertNotIn("10", images_by_number)
        self.assertEqual(
            images_by_number["11"],
            ["images/7a8d6eb93bdb7c0ffe43f4d3fe5d58e0969fbe854259664aef6d64dbb386af81.jpg"],
        )
        self.assertEqual(
            images_by_number["16"],
            ["images/765e1ec9e5d47ebc51c073517fd8207820821b959527457f6eac454c91ed991c.jpg"],
        )

    def test_structured_caption_rescues_attribution_when_flat_text_has_no_caption_at_all(self) -> None:
        """结构化字段能修正纯文本位置逻辑完全无法感知的错误归属。

        这里构造的场景是：图片在扁平文本里紧跟第 1 题（按文本位置会被分给第 1 题），
        但扁平化过程中图注文字本身丢失了（现实中常见：图注被 OCR 识别成独立的页面
        元素，没有跟随图片一起进入扁平 Markdown）。纯正则完全看不到"这张图其实是
        第2题的"这个信号——content_list.json 仍然保留着这个字段，只有结构化路径
        才能纠正。
        """
        source = "1. 第一题干。\n\n![](images/pic-a.jpg)\n\n2. 第二题干。\n"
        content_list = [
            {
                "type": "image",
                "img_path": "images/pic-a.jpg",
                "image_caption": ["第2题图"],
                "image_footnote": [],
                "bbox": [0, 0, 1, 1],
                "page_idx": 0,
            }
        ]
        without_structured = split_question_sources(source)
        images_without = {number: images for number, _block, images in without_structured}
        # 没有结构化数据时，纯文本位置逻辑把图片留在第 1 题——这正是要被纠正的错误。
        self.assertEqual(images_without["1"], ["images/pic-a.jpg"])
        self.assertEqual(images_without["2"], [])

        with TemporaryDirectory() as directory:
            asset_dir = Path(directory)
            self._write_content_list(asset_dir, content_list)
            with_structured = split_question_sources(source, asset_dir=asset_dir)
        images_with = {number: images for number, _block, images in with_structured}
        self.assertEqual(images_with["1"], [])
        self.assertEqual(images_with["2"], ["images/pic-a.jpg"])

    def test_missing_content_list_json_falls_back_to_regex_behavior(self) -> None:
        """asset_dir 下没有 content_list.json 时，行为和不传 asset_dir 完全一致。"""
        with TemporaryDirectory() as directory:
            asset_dir = Path(directory)  # 目录存在，但没有写入 source.content_list.json
            with_missing_file = split_question_sources(
                _REAL_CAPTION_ATTRIBUTION_OCR_EXCERPT, asset_dir=asset_dir
            )
        without_asset_dir = split_question_sources(_REAL_CAPTION_ATTRIBUTION_OCR_EXCERPT)
        self.assertEqual(with_missing_file, without_asset_dir)

    def test_empty_content_list_json_falls_back_to_regex_behavior(self) -> None:
        """content_list.json 存在但是空列表时，同样完全回退到纯正则逻辑。"""
        with TemporaryDirectory() as directory:
            asset_dir = Path(directory)
            self._write_content_list(asset_dir, [])
            with_empty_file = split_question_sources(
                _REAL_CAPTION_ATTRIBUTION_OCR_EXCERPT, asset_dir=asset_dir
            )
        without_asset_dir = split_question_sources(_REAL_CAPTION_ATTRIBUTION_OCR_EXCERPT)
        self.assertEqual(with_empty_file, without_asset_dir)

    def test_no_asset_dir_argument_still_defaults_to_regex_only_behavior(self) -> None:
        """完全不传 asset_dir（现有调用方尚未升级时）行为不变，是最基本的兼容性保证。"""
        blocks = split_question_sources(_REAL_CAPTION_ATTRIBUTION_OCR_EXCERPT)
        images_by_number = {number: images for number, _block, images in blocks}
        self.assertEqual(
            images_by_number["5"],
            ["images/547a74e3345f6b60c9a10d2801e2a69d036e7f200357915dfd4b4818a7871bbe.jpg"],
        )
        self.assertEqual(images_by_number["8"], [])


if __name__ == "__main__":
    unittest.main()
