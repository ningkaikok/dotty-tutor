#!/usr/bin/env python3
"""恢复归档课件原始草稿；先备份，默认仅校验，所有页面继续等待教师复核。"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps' / 'api'))
from application.services.chapter_courses import ChapterCourseService  # noqa: E402
from application.services.courseware import CoursewareService  # noqa: E402
from application.services.courseware_restore import (  # noqa: E402
    restore_courseware_draft,
)
from persistence.app_store import AppStore  # noqa: E402
from persistence.courseware_store import CoursewareStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--data-root', required=True, type=Path)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    entries = []
    for name, source_name, upload_name in [('english', 'english-chapter1', 'acceptance-english'), ('math-algebra', 'math-linear-equations', 'acceptance-math'), ('math-geometry', 'math-geometry', 'acceptance-math')]:
        source = json.loads((args.archive / 'source' / f'{source_name}-source.json').read_text())
        document = json.loads((args.archive / f'real-{name}-document.json').read_text())
        plan = json.loads((args.archive / f'real-{name}-plan.json').read_text())
        pdf = args.archive / 'data' / 'uploads' / upload_name / 'source.pdf'
        if pdf.is_symlink() or hashlib.sha256(pdf.read_bytes()).hexdigest() != source['sourceFileSha256']:
            raise ValueError('归档原 PDF 不匹配')
        entries.append((source, document, plan, pdf))
    results = []
    if args.apply:
        store = AppStore(data_root=args.data_root)
        try:
            service = CoursewareService(store=CoursewareStore(engine=store.engine), chapters=ChapterCourseService(store), jobs=None)
            for source, document, plan, pdf in entries:
                upload_id = hashlib.sha256(f"restored-pdf:{source['sourceFileSha256']}:{source['pageStart']}:{source['pageEnd']}".encode()).hexdigest()[:32]
                directory = store.upload_root / upload_id
                directory.mkdir(parents=True, exist_ok=True)
                target = directory / 'source.pdf'
                if target.is_symlink() or (target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != source['sourceFileSha256']):
                    raise ValueError('目标已存在不同教材；不会覆盖')
                if not target.exists():
                    shutil.copyfile(pdf, target)
                job = store.load_job(upload_id)
                if not job:
                    store.save_job({'uploadId': upload_id, 'filename': source['title'] + '.pdf', 'contentType': 'application/pdf',
                        'size': target.stat().st_size, 'chunkSize': target.stat().st_size, 'totalChunks': 1,
                        'sourceText': '\n\n'.join(f"<!-- Page {page['page']} -->\n{page['text']}" for page in source['pages']),
                        'directory': str(directory), 'status': 'complete', 'progress': 100,
                        'result': {'materialKind': 'textbook', 'extraction': {'pageCount': source['totalPdfPages']},
                                   'restoredSourceSha256': source['sourceFileSha256'], 'humanReview': 'pending'},
                        'message': '归档真实 PDF 文本；未经重新 OCR 或教师审核', 'completedAt': time.time()})
                elif job['status'] not in {'complete', 'completed', 'ready'}:
                    raise ValueError('已有恢复上传记录不可用；不会重新激活已撤回数据')
                elif job.get('result') is None:
                    # Repair only this deterministic restore upload's missing library metadata.
                    # Original OCR text and all child documents stay unchanged.
                    job['result'] = {'materialKind': 'textbook', 'extraction': {'pageCount': source['totalPdfPages']},
                                     'restoredSourceSha256': source['sourceFileSha256'], 'humanReview': 'pending'}
                    store.save_job(job)
                results.append(restore_courseware_draft(service, source, plan, document, upload_id=upload_id))
        finally:
            store.close()
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps({'applied': args.apply, 'humanReview': 'pending', 'results': results,
        'sourceHashes': [item[0]['sourceFileSha256'] for item in entries]}, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'applied': args.apply, 'drafts': len(results), 'humanReview': 'pending'}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
