"""
定时消息发送记录

每次定时任务执行都留一条记录（成功与失败都记），供 Web 页面查看发送情况，
避免只能翻服务器日志文件。记录数量有上限，超出后丢弃最旧的。
"""

import json
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.logger import get_logger
from utils.singleton import Singleton

STATUS_SUCCESS = 'success'
STATUS_PARTIAL = 'partial'
STATUS_FAILED = 'failed'
STATUS_SKIPPED = 'skipped'

PREVIEW_LENGTH = 120


class SendRecordStore(metaclass=Singleton):

    max_records = 500

    def __init__(self):
        self.records: List[Dict[str, Any]] = []
        self.records_file = Path("data/send_records.json")
        self.logger = get_logger(__name__)

        self._load()

    def _load(self):
        if not self.records_file.exists():
            return

        try:
            with open(self.records_file, 'r', encoding='utf-8') as f:
                self.records = json.load(f)
        except Exception as e:
            self.logger.error(f"加载发送记录失败: {e}")
            self.records = []

    def _save(self):
        try:
            self.records_file.parent.mkdir(parents=True, exist_ok=True)

            with open(self.records_file, 'w', encoding='utf-8') as f:
                json.dump(self.records, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.logger.error(f"保存发送记录失败: {e}")

    def add(
        self,
        job_id: str,
        status: str,
        account_id: Optional[str] = None,
        target_id: Any = None,
        message: str = '',
        error: Optional[str] = None,
        stage: Optional[str] = None,
        targets: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """追加一条发送记录

        一次执行只记一条：群发多个目标时，targets 里带上成功/失败数与失败明细，
        避免上千个目标产生上千条记录。stage 用于区分失败发生在哪一步。
        """
        preview = (message or '').strip().replace('\n', ' ')
        if len(preview) > PREVIEW_LENGTH:
            preview = preview[:PREVIEW_LENGTH] + '...'

        record = {
            'record_id': f"rec_{datetime.now().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(3)}",
            'job_id': job_id,
            'account_id': account_id,
            'target_id': target_id,
            'status': status,
            'stage': stage,
            'error': error,
            'preview': preview,
            'targets': targets or None,
            'time': datetime.now().isoformat(timespec='seconds'),
        }

        self.records.append(record)
        if len(self.records) > self.max_records:
            self.records = self.records[-self.max_records:]

        self._save()

        return record

    def list_records(
        self,
        job_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """按时间倒序返回记录"""
        records = self.records

        if job_id:
            records = [r for r in records if r.get('job_id') == job_id]
        if status:
            records = [r for r in records if r.get('status') == status]

        return list(reversed(records))[:max(1, limit)]

    def stats(self, job_id: Optional[str] = None) -> Dict[str, int]:
        records = self.records
        if job_id:
            records = [r for r in records if r.get('job_id') == job_id]

        return {
            'total': len(records),
            'success': sum(1 for r in records if r.get('status') == STATUS_SUCCESS),
            'partial': sum(1 for r in records if r.get('status') == STATUS_PARTIAL),
            'failed': sum(1 for r in records if r.get('status') == STATUS_FAILED),
            'skipped': sum(1 for r in records if r.get('status') == STATUS_SKIPPED),
            'messages_sent': sum((r.get('targets') or {}).get('success', 1 if r.get('status') == STATUS_SUCCESS else 0)
                                 for r in records),
        }

    def clear(self, job_id: Optional[str] = None) -> int:
        """清空记录，指定 job_id 时只清该任务的"""
        before = len(self.records)

        if job_id:
            self.records = [r for r in self.records if r.get('job_id') != job_id]
        else:
            self.records = []

        removed = before - len(self.records)
        if removed:
            self._save()

        return removed
