"""最小脱敏 golden fixtures；不包含教材原文、学生数据或图片文件。"""

EXAM_IR_FIXTURE = {
    "batchId": "golden-batch",
    "startPage": 3,
    "endPage": 3,
    "source": "3. 求一个数的两倍。\n![](assets/diagram-a.png)",
    "structuredContentList": [
        {"id": "mineru-q3", "page_idx": 0, "type": "text", "bbox": [1, 1, 100, 30],
         "text": "3. 求一个数的两倍。", "img_path": "assets/diagram-a.png"},
    ],
    "expect": {
        "questionCount": 1,
        "assetIds": ["diagram-a.png"],
        "sourceBlockId": "mineru-q3",
    },
}

STAGED_PIPELINE_FIXTURE = {
    "stages": {
        "extraction": {},
        "solution": {},
        "verification": {"status": "verified"},
        "tutor-script": {"lessonSteps": [{}, {}, {}, {}], "guideCards": [{}, {}, {}]},
    },
    "tutorCalled": True,
}

VERIFIER_BLOCKED_FIXTURE = {
    "stages": {
        "extraction": {},
        "solution": {},
        "verification": {"status": "needs_review"},
        "tutor-script": {"lessonSteps": [], "guideCards": []},
    },
    "tutorCalled": False,
}
