"""
配置向导
简化复杂配置的设置流程
"""

import json
from typing import Dict, List, Any, Optional, Union
from dataclasses import dataclass
from enum import Enum
import time
import uuid

from models.config import (
    KeywordConfig, FileConfig, AIMonitorConfig,
    MatchType, ButtonConfig, AllMessagesConfig, MonitorMode, ImageButtonConfig,
    ReplyMode, ReplyContentType
)
from monitors import monitor_factory, AIMonitorBuilder
from services import AIService
from utils.logger import get_logger
from utils.singleton import Singleton


class WizardStepType(Enum):
    ACCOUNT_SETUP = "account_setup"
    MONITOR_TYPE = "monitor_type"
    KEYWORD_CONFIG = "keyword_config"
    FILE_CONFIG = "file_config"
    AI_CONFIG = "ai_config"
    BUTTON_CONFIG = "button_config"
    ALL_MESSAGES_CONFIG = "all_messages_config"
    NOTIFICATION_CONFIG = "notification_config"
    FORWARD_CONFIG = "forward_config"
    REPLY_CONFIG = "reply_config"
    FILTER_CONFIG = "filter_config"
    ADVANCED_CONFIG = "advanced_config"
    REVIEW_CONFIG = "review_config"


@dataclass
class WizardStep:
    step_type: WizardStepType
    title: str
    description: str
    fields: List[Dict[str, Any]]
    validation_rules: Dict[str, Any]
    next_step: Optional[WizardStepType] = None
    conditional_next: Optional[Dict[str, WizardStepType]] = None


@dataclass
class WizardSession:
    session_id: str
    current_step: WizardStepType
    collected_data: Dict[str, Any]
    completed_steps: List[WizardStepType]
    errors: List[str]
    created_at: float = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = time.time()


class ConfigWizard(metaclass=Singleton):

    def __init__(self):
        self.logger = get_logger(__name__)
        self.sessions: Dict[str, WizardSession] = {}

        self.steps = self._define_wizard_steps()

        self._start_session_cleanup()

        self.logger.info("配置向导初始化完成")

    def _start_session_cleanup(self):
        import threading

        def cleanup_old_sessions():
            while True:
                try:
                    current_time = time.time()
                    expired_sessions = []

                    for session_id, session in self.sessions.items():
                        if current_time - session.created_at > 1800:
                            expired_sessions.append(session_id)

                    for session_id in expired_sessions:
                        del self.sessions[session_id]
                        self.logger.debug(f"清理过期会话: {session_id}")

                    if expired_sessions:
                        self.logger.debug(f"本次清理了 {len(expired_sessions)} 个过期会话")

                except Exception as e:
                    self.logger.error(f"清理会话时出错: {e}")

                time.sleep(300)

        cleanup_thread = threading.Thread(target=cleanup_old_sessions, daemon=True)
        cleanup_thread.start()

    def _define_wizard_steps(self) -> Dict[WizardStepType, WizardStep]:
        return {
            WizardStepType.ACCOUNT_SETUP: WizardStep(
                step_type=WizardStepType.ACCOUNT_SETUP,
                title="账号设置",
                description="选择要配置监控的Telegram账号",
                fields=[
                    {
                        "name": "account_id",
                        "type": "select",
                        "label": "选择账号",
                        "required": True,
                        "options": "dynamic"
                    }
                ],
                validation_rules={
                    "account_id": {"required": True}
                },
                next_step=WizardStepType.MONITOR_TYPE
            ),

            WizardStepType.MONITOR_TYPE: WizardStep(
                step_type=WizardStepType.MONITOR_TYPE,
                title="监控类型",
                description="选择要创建的监控器类型",
                fields=[
                    {
                        "name": "monitor_type",
                        "type": "radio",
                        "label": "监控类型",
                        "required": True,
                        "options": [
                            {"value": "keyword", "label": "关键词监控", "description": "监控包含特定关键词的消息"},
                            {"value": "file", "label": "文件监控", "description": "监控特定类型的文件"},
                            {"value": "button", "label": "按钮监控", "description": "监控带按钮的消息"},
                            {"value": "all_messages", "label": "全量监控", "description": "监控所有消息"},
                            {"value": "ai", "label": "AI智能监控", "description": "使用AI判断消息是否符合条件"}
                        ]
                    }
                ],
                validation_rules={
                    "monitor_type": {"required": True}
                },
                conditional_next={
                    "keyword": WizardStepType.KEYWORD_CONFIG,
                    "file": WizardStepType.FILE_CONFIG,
                    "button": WizardStepType.BUTTON_CONFIG,
                    "all_messages": WizardStepType.ALL_MESSAGES_CONFIG,
                    "ai": WizardStepType.AI_CONFIG
                }
            ),

            WizardStepType.KEYWORD_CONFIG: WizardStep(
                step_type=WizardStepType.KEYWORD_CONFIG,
                title="关键词配置",
                description="配置关键词监控参数",
                fields=[
                    {
                        "name": "keyword",
                        "type": "text",
                        "label": "关键词",
                        "required": True,
                        "placeholder": "输入要监控的关键词"
                    },
                    {
                        "name": "match_type",
                        "type": "select",
                        "label": "匹配方式",
                        "required": True,
                        "options": [
                            {"value": "exact", "label": "精确匹配"},
                            {"value": "partial", "label": "模糊匹配"},
                            {"value": "regex", "label": "正则表达式"}
                        ]
                    },
                    {
                        "name": "chats",
                        "type": "text",
                        "label": "监控群组/频道",
                        "required": True,
                        "placeholder": "输入群组ID，多个用逗号分隔",
                        "help": "可以输入群组ID或@username，支持批量添加（逗号分隔）"
                    },
                    {
                        "name": "reply_enabled",
                        "type": "checkbox",
                        "label": "启用自动回复",
                        "required": False,
                        "default": False,
                        "help": "检测到关键词时自动回复"
                    },
                    {
                        "name": "reply_type",
                        "type": "radio",
                        "label": "回复类型",
                        "required": False,
                        "default": "keyword",
                        "options": [
                            {"value": "keyword", "label": "回复匹配到的关键词"},
                            {"value": "custom", "label": "自定义回复内容"},
                            {"value": "ai", "label": "AI生成回复"}
                        ],
                        "conditional": {"reply_enabled": True},
                        "help": "选择回复内容的来源"
                    },
                    {
                        "name": "reply_texts",
                        "type": "textarea",
                        "label": "自定义回复内容",
                        "required": False,
                        "placeholder": "每行一条回复内容，程序会随机选择",
                        "conditional": {"reply_enabled": True, "reply_type": "custom"},
                        "help": "多条回复内容请换行输入"
                    },
                    {
                        "name": "ai_reply_prompt",
                        "type": "textarea",
                        "label": "AI回复提示词",
                        "required": False,
                        "placeholder": "例如：基于以下关键词生成一条友好的回复",
                        "conditional": {"reply_enabled": True, "reply_type": "ai"},
                        "help": "输入AI生成回复的提示词"
                    },
                    {
                        "name": "reply_delay_min",
                        "type": "number",
                        "label": "最小回复延迟(秒)",
                        "required": False,
                        "placeholder": "0",
                        "min": 0,
                        "conditional": {"reply_enabled": True}
                    },
                    {
                        "name": "reply_delay_max",
                        "type": "number",
                        "label": "最大回复延迟(秒)",
                        "required": False,
                        "placeholder": "5",
                        "min": 0,
                        "conditional": {"reply_enabled": True}
                    },
                    {
                        "name": "reply_mode",
                        "type": "select",
                        "label": "回复模式",
                        "required": False,
                        "default": "reply",
                        "options": [
                            {"value": "reply", "label": "回复消息（默认）"},
                            {"value": "send", "label": "直接发送消息"}
                        ],
                        "conditional": {"reply_enabled": True},
                        "help": "选择回复模式：回复消息会直接回复原消息，直接发送会发送独立消息"
                    }
                ],
                validation_rules={
                    "keyword": {"required": True, "min_length": 1},
                    "match_type": {"required": True},
                    "chats": {"required": True}
                },
                next_step=WizardStepType.NOTIFICATION_CONFIG
            ),

            WizardStepType.FILE_CONFIG: WizardStep(
                step_type=WizardStepType.FILE_CONFIG,
                title="文件监控配置",
                description="配置文件监控参数",
                fields=[
                    {
                        "name": "file_extension",
                        "type": "text",
                        "label": "文件扩展名",
                        "required": True,
                        "placeholder": "如: pdf",
                        "help": "要监控的文件扩展名（不含点号）"
                    },
                    {
                        "name": "min_size_kb",
                        "type": "number",
                        "label": "最小文件大小(KB)",
                        "required": False,
                        "placeholder": "0"
                    },
                    {
                        "name": "max_size_mb",
                        "type": "number",
                        "label": "最大文件大小(MB)",
                        "required": False,
                        "placeholder": "100"
                    },
                    {
                        "name": "chats",
                        "type": "text",
                        "label": "监控群组/频道",
                        "required": True,
                        "placeholder": "输入群组ID，多个用逗号分隔",
                        "help": "可以输入群组ID或@username，支持批量添加（逗号分隔）"
                    },
                    {
                        "name": "save_files",
                        "type": "checkbox",
                        "label": "保存文件到本地",
                        "required": False,
                        "default": False
                    },
                    {
                        "name": "save_folder",
                        "type": "text",
                        "label": "保存文件夹",
                        "required": False,
                        "placeholder": "downloads/files",
                        "conditional": {"save_files": True},
                        "help": "相对于程序根目录的路径"
                    }
                ],
                validation_rules={
                    "file_extension": {"required": True},
                    "chats": {"required": True},
                    "save_folder": {
                        "required_if": {"save_files": True},
                        "message": "启用文件保存时，必须指定保存文件夹"
                    }
                },
                next_step=WizardStepType.NOTIFICATION_CONFIG
            ),

            WizardStepType.AI_CONFIG: WizardStep(
                step_type=WizardStepType.AI_CONFIG,
                title="AI监控配置",
                description="配置AI智能监控参数",
                fields=[
                    {
                        "name": "ai_prompt",
                        "type": "textarea",
                        "label": "AI判断规则",
                        "required": True,
                        "placeholder": "描述你希望AI如何判断消息，例如：判断这条消息是否包含投资机会",
                        "help": "用自然语言描述判断条件"
                    },
                    {
                        "name": "confidence_threshold",
                        "type": "range",
                        "label": "置信度阈值",
                        "required": False,
                        "min": 0.1,
                        "max": 1.0,
                        "step": 0.1,
                        "default": 0.7,
                        "help": "AI判断的最低置信度要求"
                    },
                    {
                        "name": "chats",
                        "type": "text",
                        "label": "监控群组/频道",
                        "required": True,
                        "placeholder": "输入群组ID，多个用逗号分隔"
                    },
                    {
                        "name": "reply_enabled",
                        "type": "checkbox",
                        "label": "启用自动回复",
                        "required": False,
                        "default": False,
                        "help": "AI判断匹配后自动回复"
                    },
                    {
                        "name": "reply_type",
                        "type": "radio",
                        "label": "回复内容类型",
                        "required": False,
                        "default": "custom",
                        "options": [
                            {"value": "custom", "label": "自定义回复内容"},
                            {"value": "ai", "label": "AI生成回复内容"}
                        ],
                        "conditional": {"reply_enabled": True},
                        "help": "选择回复内容的来源"
                    },
                    {
                        "name": "reply_texts",
                        "type": "textarea",
                        "label": "回复内容列表",
                        "required": False,
                        "placeholder": "每行一条回复内容，程序会随机选择",
                        "conditional": {"reply_enabled": True, "reply_type": "custom"},
                        "help": "多条回复内容请换行输入"
                    },
                    {
                        "name": "ai_reply_prompt",
                        "type": "textarea",
                        "label": "AI回复提示词",
                        "required": False,
                        "placeholder": "例如：根据用户的消息生成一条友好、专业的回复",
                        "conditional": {"reply_enabled": True, "reply_type": "ai"},
                        "help": "输入AI生成回复内容的提示词，AI将基于此提示词和原始消息生成回复"
                    },
                    {
                        "name": "reply_delay_min",
                        "type": "number",
                        "label": "最小回复延迟(秒)",
                        "required": False,
                        "placeholder": "0",
                        "min": 0,
                        "conditional": {"reply_enabled": True}
                    },
                    {
                        "name": "reply_delay_max",
                        "type": "number",
                        "label": "最大回复延迟(秒)",
                        "required": False,
                        "placeholder": "5",
                        "min": 0,
                        "conditional": {"reply_enabled": True}
                    },
                    {
                        "name": "reply_mode",
                        "type": "select",
                        "label": "回复模式",
                        "required": False,
                        "default": "reply",
                        "options": [
                            {"value": "reply", "label": "回复消息（默认）"},
                            {"value": "send", "label": "直接发送消息"}
                        ],
                        "conditional": {"reply_enabled": True},
                        "help": "选择回复模式：回复消息会直接回复原消息，直接发送会发送独立消息"
                    },
                    {
                        "name": "ai_reply_prompt",
                        "type": "textarea",
                        "label": "AI回复提示词",
                        "required": False,
                        "placeholder": "例如：根据用户的消息生成一条友好、专业的回复",
                        "conditional": {"reply_enabled": True, "reply_mode": "ai_reply"},
                        "help": "输入AI生成回复内容的提示词，AI将基于此提示词和原始消息生成回复"
                    }
                ],
                validation_rules={
                    "ai_prompt": {"required": True, "min_length": 10},
                    "chats": {"required": True}
                },
                next_step=WizardStepType.NOTIFICATION_CONFIG
            ),

            WizardStepType.BUTTON_CONFIG: WizardStep(
                step_type=WizardStepType.BUTTON_CONFIG,
                title="按钮监控配置",
                description="配置按钮监控参数",
                fields=[
                    {
                        "name": "monitor_subtype",
                        "type": "select",
                        "label": "监控子类型",
                        "required": True,
                        "options": [
                            {"value": "button_only", "label": "仅按钮监控"},
                            {"value": "image_button", "label": "图片+按钮监控"}
                        ],
                        "default": "button_only",
                        "help": "选择监控按钮的方式"
                    },
                    {
                        "name": "mode",
                        "type": "select",
                        "label": "监控模式",
                        "required": True,
                        "options": [
                            {"value": "manual", "label": "手动模式 - 关键词匹配"},
                            {"value": "ai", "label": "AI模式 - 智能判断"}
                        ],
                        "default": "manual",
                        "conditional": {"monitor_subtype": "button_only"},
                        "help": "手动模式需要设置按钮关键词，AI模式由AI自动判断要点击的按钮"
                    },
                    {
                        "name": "button_keyword",
                        "type": "text",
                        "label": "按钮关键词",
                        "required": False,
                        "placeholder": "要点击的按钮文字",
                        "help": "手动模式下必填；AI模式下可选（用于过滤按钮）",
                        "conditional": {"monitor_subtype": "button_only"}
                    },
                    {
                        "name": "ai_prompt",
                        "type": "textarea",
                        "label": "AI提示词",
                        "required": False,
                        "placeholder": "描述AI如何选择按钮，例如：点击包含'确认'或'提交'的按钮",
                        "conditional": {"mode": "ai"},
                        "help": "AI模式下使用，描述如何选择要点击的按钮"
                    },
                    {
                        "name": "image_ai_prompt",
                        "type": "textarea",
                        "label": "图片分析提示词",
                        "required": True,
                        "placeholder": "描述如何分析图片和按钮，例如：分析图片内容，如果是验证码图片，请点击对应的按钮",
                        "conditional": {"monitor_subtype": "image_button"},
                        "help": "AI将根据此提示词分析图片和按钮"
                    },
                    {
                        "name": "button_keywords",
                        "type": "text",
                        "label": "按钮关键词过滤（可选）",
                        "required": False,
                        "placeholder": "多个关键词用逗号分隔",
                        "conditional": {"monitor_subtype": "image_button"},
                        "help": "只处理包含这些关键词的按钮，留空则处理所有按钮"
                    },
                    {
                        "name": "download_images",
                        "type": "checkbox",
                        "label": "下载图片到本地",
                        "required": False,
                        "default": True,
                        "conditional": {"monitor_subtype": "image_button"}
                    },
                    {
                        "name": "confidence_threshold",
                        "type": "range",
                        "label": "AI置信度阈值",
                        "required": False,
                        "min": 0.1,
                        "max": 1.0,
                        "step": 0.1,
                        "default": 0.7,
                        "conditional": {"monitor_subtype": "image_button"},
                        "help": "AI判断的最低置信度要求"
                    },
                    {
                        "name": "chats",
                        "type": "text",
                        "label": "监控群组/频道",
                        "required": True,
                        "placeholder": "输入群组ID，多个用逗号分隔"
                    }
                ],
                validation_rules={
                    "monitor_subtype": {"required": True},
                    "mode": {
                        "required_if": {"monitor_subtype": "button_only"},
                        "message": "普通按钮监控必须选择监控模式"
                    },
                    "button_keyword": {
                        "custom_validation": "validate_button_keyword",
                        "message": "手动模式下必须设置按钮关键词"
                    },
                    "chats": {"required": True},
                    "ai_prompt": {
                        "required_if": {"mode": "ai"},
                        "message": "AI模式下必须设置AI提示词"
                    },
                    "image_ai_prompt": {
                        "required_if": {"monitor_subtype": "image_button"},
                        "message": "图片+按钮监控必须设置分析提示词"
                    }
                },
                next_step=WizardStepType.NOTIFICATION_CONFIG
            ),

            WizardStepType.ALL_MESSAGES_CONFIG: WizardStep(
                step_type=WizardStepType.ALL_MESSAGES_CONFIG,
                title="全量监控配置",
                description="配置全量消息监控参数",
                fields=[
                    {
                        "name": "chat_id",
                        "type": "text",
                        "label": "监控群组/频道",
                        "required": True,
                        "placeholder": "输入要监控的群组ID",
                        "help": "只能监控单个群组或频道"
                    },
                    {
                        "name": "reply_enabled",
                        "type": "checkbox",
                        "label": "启用自动回复",
                        "required": False,
                        "default": False,
                        "help": "对所有消息进行自动回复"
                    },
                    {
                        "name": "reply_content_type",
                        "type": "radio",
                        "label": "回复内容类型",
                        "required": False,
                        "default": "custom",
                        "options": [
                            {"value": "custom", "label": "自定义回复内容"},
                            {"value": "ai", "label": "AI生成回复内容"}
                        ],
                        "conditional": {"reply_enabled": True},
                        "help": "选择回复内容的来源"
                    },
                    {
                        "name": "reply_texts",
                        "type": "textarea",
                        "label": "回复内容列表",
                        "required": False,
                        "placeholder": "每行一条回复内容，程序会随机选择",
                        "conditional": {"reply_enabled": True, "reply_content_type": "custom"},
                        "help": "多条回复内容请换行输入"
                    },
                    {
                        "name": "ai_reply_prompt",
                        "type": "textarea",
                        "label": "AI回复提示词",
                        "required": False,
                        "placeholder": "例如：根据用户的消息生成一条友好、专业的回复",
                        "conditional": {"reply_enabled": True, "reply_content_type": "ai"},
                        "help": "输入AI生成回复内容的提示词，AI将基于此提示词和原始消息生成回复"
                    },
                    {
                        "name": "reply_delay_min",
                        "type": "number",
                        "label": "最小回复延迟(秒)",
                        "required": False,
                        "placeholder": "0",
                        "min": 0,
                        "conditional": {"reply_enabled": True}
                    },
                    {
                        "name": "reply_delay_max",
                        "type": "number",
                        "label": "最大回复延迟(秒)",
                        "required": False,
                        "placeholder": "5",
                        "min": 0,
                        "conditional": {"reply_enabled": True}
                    },
                    {
                        "name": "reply_mode",
                        "type": "select",
                        "label": "回复模式",
                        "required": False,
                        "default": "reply",
                        "options": [
                            {"value": "reply", "label": "回复消息（默认）"},
                            {"value": "send", "label": "直接发送消息"}
                        ],
                        "conditional": {"reply_enabled": True},
                        "help": "选择回复模式：回复消息会直接回复原消息，直接发送会发送独立消息"
                    }
                ],
                validation_rules={
                    "chat_id": {"required": True}
                },
                next_step=WizardStepType.NOTIFICATION_CONFIG
            ),

            WizardStepType.FILTER_CONFIG: WizardStep(
                step_type=WizardStepType.FILTER_CONFIG,
                title="过滤配置",
                description="配置消息过滤条件，支持两种模式：精确ID过滤或黑名单过滤",
                fields=[
                    {
                        "name": "filter_mode",
                        "type": "radio",
                        "label": "过滤模式（可选）",
                        "required": False,
                        "default": "none",
                        "options": [
                            {"value": "none", "label": "不过滤", "description": "不进行任何过滤，监控所有消息"},
                            {"value": "blacklist", "label": "黑名单模式", "description": "基于用户名/ID的黑名单过滤"},
                            {"value": "specific_ids", "label": "精确ID模式", "description": "仅监控指定的Bot、频道或群组ID"}
                        ],
                        "help": "选择过滤方式：不过滤则监控所有消息；黑名单模式适合排除特定用户；精确ID模式适合精准定位特定来源"
                    },
                    {
                        "name": "blacklist_section_header",
                        "type": "section_header",
                        "label": "📋 黑名单过滤设置",
                        "conditional": {"filter_mode": "blacklist"},
                        "help": "配置要忽略的用户、频道或Bot"
                    },
                    {
                        "name": "blocked_users",
                        "type": "textarea",
                        "label": "用户黑名单",
                        "required": False,
                        "placeholder": "用户ID、@username或昵称\n每行一个，例如：\n123456789\n@spam_user\n垃圾用户",
                        "conditional": {"filter_mode": "blacklist"},
                        "help": "忽略来自这些用户的消息"
                    },
                    {
                        "name": "blocked_channels",
                        "type": "textarea",
                        "label": "频道黑名单",
                        "required": False,
                        "placeholder": "频道ID或@频道名\n每行一个，例如：\n-1001234567890\n@channel_name",
                        "conditional": {"filter_mode": "blacklist"},
                        "help": "忽略来自这些频道的消息"
                    },
                    {
                        "name": "blocked_bots",
                        "type": "textarea",
                        "label": "Bot黑名单",
                        "required": False,
                        "placeholder": "Bot ID或@Bot名\n每行一个，例如：\n123456789\n@spam_bot",
                        "conditional": {"filter_mode": "blacklist"},
                        "help": "忽略来自这些Bot的消息"
                    },
                    {
                        "name": "specific_ids_section_header",
                        "type": "section_header",
                        "label": "🎯 精确ID过滤设置",
                        "conditional": {"filter_mode": "specific_ids"},
                        "help": "仅监控来自以下指定ID的消息，所有未指定的来源都将被忽略"
                    },
                    {
                        "name": "user_ids",
                        "type": "textarea",
                        "label": "监控的用户ID",
                        "required": False,
                        "placeholder": "每行一个用户ID\n例如：\n123456789\n987654321",
                        "conditional": {"filter_mode": "specific_ids"},
                        "help": "仅处理来自这些用户ID的消息"
                    },
                    {
                        "name": "bot_ids",
                        "type": "textarea",
                        "label": "监控的Bot ID",
                        "required": False,
                        "placeholder": "每行一个Bot ID\n例如：\n123456789\n987654321",
                        "conditional": {"filter_mode": "specific_ids"},
                        "help": "仅处理来自这些Bot ID的消息"
                    },
                    {
                        "name": "channel_ids",
                        "type": "textarea",
                        "label": "监控的频道/群组ID",
                        "required": False,
                        "placeholder": "每行一个频道或群组ID\n例如：\n-1001234567890（频道）\n-123456789（群组）\n@channel_name（频道用户名）",
                        "conditional": {"filter_mode": "specific_ids"},
                        "help": "仅处理来自这些频道或群组ID的消息。频道和群组无需区分，统一在此配置"
                    }
                ],
                validation_rules={},
                next_step=WizardStepType.ADVANCED_CONFIG
            ),

            WizardStepType.ADVANCED_CONFIG: WizardStep(
                step_type=WizardStepType.ADVANCED_CONFIG,
                title="高级配置",
                description="配置高级选项（可选）",
                fields=[
                    {
                        "name": "priority",
                        "type": "range",
                        "label": "监控优先级",
                        "required": False,
                        "min": 1,
                        "max": 100,
                        "default": 50,
                        "help": "数值越小优先级越高，当多个监控器匹配同一消息时，优先级高的先执行"
                    },
                    {
                        "name": "max_executions",
                        "type": "number",
                        "label": "最大执行次数",
                        "required": False,
                        "placeholder": "留空表示无限制",
                        "help": "监控器最多执行几次后自动删除"
                    },
                    {
                        "name": "execution_mode",
                        "type": "select",
                        "label": "执行模式",
                        "required": False,
                        "options": [
                            {"value": "merge", "label": "合并执行（默认）", "description": "多个监控器匹配时，合并执行所有动作"},
                            {"value": "first_match", "label": "首次匹配停止", "description": "匹配到第一个监控器后停止"},
                            {"value": "all", "label": "全部独立执行", "description": "每个监控器独立执行所有动作"}
                        ],
                        "default": "merge",
                        "help": "当多个监控器匹配同一消息时的处理方式"
                    },
                    {
                        "name": "log_file",
                        "type": "text",
                        "label": "日志文件路径",
                        "required": False,
                        "placeholder": "logs/monitor.log",
                        "help": "记录匹配消息的日志文件"
                    }
                ],
                validation_rules={},
                next_step=WizardStepType.REVIEW_CONFIG
            ),

            WizardStepType.NOTIFICATION_CONFIG: WizardStep(
                step_type=WizardStepType.NOTIFICATION_CONFIG,
                title="通知配置",
                description="配置消息通知方式",
                fields=[
                    {
                        "name": "email_notify",
                        "type": "checkbox",
                        "label": "启用邮件通知",
                        "required": False,
                        "default": False
                    },
                    {
                        "name": "email_addresses",
                        "type": "textarea",
                        "label": "通知邮箱",
                        "required": False,
                        "placeholder": "your@email.com",
                        "help_text": "支持多个邮箱，每行一个。默认使用.env文件中配置的邮箱",
                        "conditional": {"email_notify": True},
                        "rows": 3
                    },
                    {
                        "name": "auto_forward",
                        "type": "checkbox",
                        "label": "启用自动转发",
                        "required": False,
                        "default": False
                    }
                ],
                validation_rules={
                    "email_addresses": {
                        "required_if": {"email_notify": True},
                        "custom": "validate_email_list"
                    }
                },
                next_step=WizardStepType.FILTER_CONFIG,
                conditional_next={
                    "auto_forward": WizardStepType.FORWARD_CONFIG
                }
            ),

            WizardStepType.FORWARD_CONFIG: WizardStep(
                step_type=WizardStepType.FORWARD_CONFIG,
                title="转发配置",
                description="配置消息转发参数",
                fields=[
                    {
                        "name": "forward_targets",
                        "type": "text",
                        "label": "转发目标",
                        "required": True,
                        "placeholder": "目标群组ID，多个用逗号分隔",
                        "help": "输入要转发到的群组ID，多个目标用逗号分隔"
                    },
                    {
                        "name": "enhanced_forward",
                        "type": "checkbox",
                        "label": "启用增强转发",
                        "required": False,
                        "default": False,
                        "help": "转发受限时自动下载并重发"
                    },
                    {
                        "name": "max_download_size",
                        "type": "number",
                        "label": "最大下载大小(MB)",
                        "required": False,
                        "placeholder": "50",
                        "conditional": {"enhanced_forward": True}
                    }
                ],
                validation_rules={
                    "forward_targets": {
                        "required": True,
                        "message": "必须指定转发目标群组"
                    }
                },
                next_step=WizardStepType.FILTER_CONFIG
            ),

            WizardStepType.REVIEW_CONFIG: WizardStep(
                step_type=WizardStepType.REVIEW_CONFIG,
                title="配置预览",
                description="检查配置信息并确认创建",
                fields=[
                    {
                        "name": "config_summary",
                        "type": "readonly",
                        "label": "配置摘要",
                        "value": "dynamic"
                    },
                    {
                        "name": "confirm",
                        "type": "checkbox",
                        "label": "确认创建监控器",
                        "required": True
                    }
                ],
                validation_rules={
                    "confirm": {"required": True}
                }
            )
        }

    def start_wizard(self, session_id: str) -> Dict[str, Any]:
        try:
            self.logger.debug(f"开始向导，session_id: {session_id}")
            self.logger.debug(f"当前会话数: {len(self.sessions)}")

            session = WizardSession(
                session_id=session_id,
                current_step=WizardStepType.ACCOUNT_SETUP,
                collected_data={},
                completed_steps=[],
                errors=[]
            )

            self.sessions[session_id] = session
            self.logger.info(f"会话已创建，当前会话: {list(self.sessions.keys())}")

            return self.get_step_data(session_id)

        except Exception as e:
            self.logger.error(f"启动向导失败: {e}")
            return {
                "success": False,
                "errors": [f"启动向导失败: {str(e)}"],
                "message": f"启动向导失败: {str(e)}"
            }

    def start_wizard_edit_mode(self, session_id: str, edit_key: str, edit_config: Dict[str, Any]) -> Dict[str, Any]:
        try:
            self.logger.debug(f"编辑模式启动向导，session_id: {session_id}, edit_key: {edit_key}")

            collected_data = {}
            if edit_config:
                collected_data = self._config_to_wizard_data(edit_config, edit_key)
                self.logger.debug(f"预填充数据: {collected_data}")

            monitor_type = collected_data.get('monitor_type', 'keyword')

            if monitor_type == 'keyword':
                start_step = WizardStepType.KEYWORD_CONFIG
            elif monitor_type == 'file':
                start_step = WizardStepType.FILE_CONFIG
            elif monitor_type == 'ai':
                start_step = WizardStepType.AI_CONFIG
            elif monitor_type == 'button':
                start_step = WizardStepType.BUTTON_CONFIG
            elif monitor_type == 'all_messages':
                start_step = WizardStepType.ALL_MESSAGES_CONFIG
            else:
                start_step = WizardStepType.ACCOUNT_SETUP

            session = WizardSession(
                session_id=session_id,
                current_step=start_step,
                collected_data=collected_data,
                completed_steps=[WizardStepType.ACCOUNT_SETUP, WizardStepType.MONITOR_TYPE],
                errors=[]
            )

            self.sessions[session_id] = session

            self.logger.debug(f"编辑模式会话初始化完成，会话数据: {collected_data}")
            self.logger.debug(f"其中 monitor_type={collected_data.get('monitor_type')}, account_id={collected_data.get('account_id')}")

            return self.get_step_data(session_id)

        except Exception as e:
            self.logger.error(f"编辑模式启动向导失败: {e}")
            return {
                "success": False,
                "errors": [f"编辑模式启动失败: {str(e)}"],
                "message": f"编辑模式启动失败: {str(e)}"
            }

    def _config_to_wizard_data(self, config: Dict[str, Any], edit_key: str) -> Dict[str, Any]:
        data = {}

        data['edit_key'] = edit_key

        if 'account_id' in config:
            data['account_id'] = config['account_id']

        if 'KeywordMonitor' in edit_key or edit_key.startswith('keyword_'):
            data['monitor_type'] = 'keyword'
        elif 'FileMonitor' in edit_key or edit_key.startswith('file_'):
            data['monitor_type'] = 'file'
        elif 'AIMonitor' in edit_key or edit_key.startswith('ai_'):
            data['monitor_type'] = 'ai'
        elif 'ButtonMonitor' in edit_key or edit_key.startswith('button_'):
            data['monitor_type'] = 'button'
        elif 'ImageButtonMonitor' in edit_key or edit_key.startswith('image_button_'):
            data['monitor_type'] = 'button'
            data['monitor_subtype'] = 'image_button'
        elif 'AllMessagesMonitor' in edit_key or edit_key.startswith('all_messages_'):
            data['monitor_type'] = 'all_messages'
        else:
            if 'monitor_type' in config:
                type_mapping = {
                    'KeywordMonitor': 'keyword',
                    'FileMonitor': 'file',
                    'AIMonitor': 'ai',
                    'ButtonMonitor': 'button',
                    'ImageButtonMonitor': 'image_button',
                    'AllMessagesMonitor': 'all_messages'
                }
                data['monitor_type'] = type_mapping.get(config['monitor_type'], 'keyword')
            else:
                self.logger.warning(f"无法从edit_key推断监控类型: {edit_key}")
                data['monitor_type'] = 'keyword'

        for key, value in config.items():
            if key == 'chats' and isinstance(value, list):
                data['chats'] = ', '.join(str(chat) for chat in value)
            elif key == 'forward_targets' and isinstance(value, list):
                data['forward_targets'] = ', '.join(str(target) for target in value)
            elif key == 'users' and isinstance(value, list):
                data['users'] = '\n'.join(str(user) for user in value)
                data['user_ids'] = '\n'.join(str(user) for user in value)
                if value:
                    data['filter_users'] = True
            elif key == 'blocked_users' and isinstance(value, list):
                data['blocked_users'] = '\n'.join(str(user) for user in value)
            elif key == 'blocked_channels' and isinstance(value, list):
                data['blocked_channels'] = '\n'.join(str(channel) for channel in value)
            elif key == 'blocked_bots' and isinstance(value, list):
                data['blocked_bots'] = '\n'.join(str(bot) for bot in value)
            elif key == 'bot_ids' and isinstance(value, list):
                data['bot_ids'] = '\n'.join(str(bot_id) for bot_id in value)
                if value:
                    data['filter_specific_ids'] = True
            elif key == 'channel_ids' and isinstance(value, list):
                data['channel_ids'] = '\n'.join(str(channel_id) for channel_id in value)
                if value:
                    data['filter_specific_ids'] = True
            elif key == 'group_ids' and isinstance(value, list):
                data['group_ids'] = '\n'.join(str(group_id) for group_id in value)
                if value:
                    data['filter_specific_ids'] = True
            elif key == 'reply_texts' and isinstance(value, list):
                data['reply_texts'] = '\n'.join(value) if value else ''

            elif key == 'keyword':
                data['keyword'] = str(value) if value else ''
            elif key == 'chat_id':
                data['chat_id'] = str(value) if value else ''
            elif key == 'ai_prompt':
                data['ai_prompt'] = str(value) if value else ''
            elif key == 'ai_reply_prompt':
                data['ai_reply_prompt'] = str(value) if value else ''
            elif key == 'button_keyword':
                data['button_keyword'] = str(value) if value else ''
            elif key == 'file_extension':
                data['file_extension'] = str(value) if value else ''
            elif key == 'save_folder':
                data['save_folder'] = str(value) if value else ''
                if value:
                    data['save_files'] = True
            elif key == 'log_file':
                data['log_file'] = str(value) if value else ''
                if value:
                    data['log_to_file'] = True
            elif key == 'execution_mode':
                data['execution_mode'] = str(value) if value else 'merge'
            elif key == 'ai_model':
                data['ai_model'] = str(value) if value else 'gpt-4o'

            elif key == 'reply_delay_min':
                data['reply_delay_min'] = float(value) if value is not None else 0
            elif key == 'reply_delay_max':
                data['reply_delay_max'] = float(value) if value is not None else 0
            elif key == 'confidence_threshold':
                data['confidence_threshold'] = float(value) if value is not None else 0.7
            elif key == 'min_size':
                data['min_size'] = str(value) if value else ''
            elif key == 'max_size':
                data['max_size'] = str(value) if value else ''
            elif key == 'max_download_size_mb':
                data['max_download_size_mb'] = str(value) if value else ''
            elif key == 'max_executions':
                data['max_executions'] = str(value) if value else ''
            elif key == 'priority':
                data['priority'] = int(value) if value is not None else 50

            elif key == 'reply_enabled':
                data['reply_enabled'] = bool(value)
            elif key == 'email_notify':
                data['email_notify'] = bool(value)
            elif key == 'auto_forward':
                data['auto_forward'] = bool(value)
            elif key == 'enhanced_forward':
                data['enhanced_forward'] = bool(value)
            elif key == 'active':
                data['active'] = bool(value)

            elif key == 'match_type':
                if hasattr(value, 'value'):
                    data['match_type'] = value.value
                else:
                    data['match_type'] = str(value) if value else 'partial'
            elif key == 'reply_mode':
                if hasattr(value, 'value'):
                    data['reply_mode'] = value.value
                else:
                    data['reply_mode'] = str(value) if value else 'reply'
            elif key == 'reply_content_type':
                content_type = value.value if hasattr(value, 'value') else (str(value) if value else 'custom')
                data['reply_type'] = content_type
                data['reply_content_type'] = content_type
            elif key == 'mode':
                if hasattr(value, 'value'):
                    data['mode'] = value.value
                else:
                    data['mode'] = str(value) if value else 'manual'

            elif key not in ['monitor_type', 'type', 'execution_count']:
                data[key] = value

        has_specific_ids = bool(config.get('bot_ids')) or \
                          bool(config.get('channel_ids')) or \
                          bool(config.get('group_ids')) or \
                          (bool(config.get('users')) and config.get('user_option') == '1')

        if not has_specific_ids and not config.get('users'):
            data['filter_mode'] = 'no_filter'
        elif has_specific_ids:
            data['filter_mode'] = 'specific_ids'
            data['filter_specific_ids'] = True

        return data

    def get_step_data(self, session_id: str) -> Dict[str, Any]:
        import copy

        if session_id not in self.sessions:
            raise ValueError("会话不存在")

        session = self.sessions[session_id]
        step = self.steps[session.current_step]

        fields = self._process_dynamic_fields(step.fields, session)

        result = {
            "session_id": str(session_id),
            "step": {
                "type": str(step.step_type.value),
                "title": str(step.title),
                "description": str(step.description),
                "fields": fields
            },
            "progress": {
                "current": int(len(session.completed_steps) + 1),
                "total": int(len(self.steps)),
                "percentage": float(((len(session.completed_steps) + 1) / len(self.steps)) * 100)
            },
            "collected_data": copy.deepcopy(session.collected_data),
            "errors": list(session.errors) if session.errors else []
        }

        return result

    def _process_dynamic_fields(self, fields: List[Dict[str, Any]], session: WizardSession) -> List[Dict[str, Any]]:
        import copy
        processed_fields = []

        for field in fields:
            field_copy = copy.deepcopy(field)

            if field.get("options") == "dynamic":
                if field["name"] == "account_id":
                    from core import AccountManager
                    account_manager = AccountManager()
                    accounts = account_manager.list_accounts()
                    field_copy["options"] = [
                        {"value": str(acc.account_id), "label": acc.config.phone or str(acc.account_id)}
                        for acc in accounts
                    ]

            if "conditional" in field:
                condition = field["conditional"]
                field_copy["conditional"] = condition

                should_show = True
                for key, value in condition.items():
                    collected_value = session.collected_data.get(key)
                    if isinstance(value, bool) and value:
                        should_show = collected_value in (True, "on", "true", "1")
                    elif isinstance(value, bool) and not value:
                        should_show = collected_value not in (True, "on", "true", "1")
                    else:
                        should_show = collected_value == value

                    if not should_show:
                        break

                field_copy["show"] = should_show
            else:
                field_copy["show"] = True

            if field.get("value") == "dynamic":
                if field["name"] == "config_summary":
                    field_copy["value"] = self._generate_config_summary(session)

            field_name = field["name"]
            if field_name in session.collected_data:
                field_copy["value"] = session.collected_data[field_name]
                self.logger.debug(f"恢复字段 {field_name} 的值: {field_copy['value']}")

            if field_name == "email_addresses":
                current_value = field_copy.get("value", "")

                if field_name in session.collected_data and session.collected_data[field_name]:
                    field_copy["value"] = session.collected_data[field_name]
                elif not current_value or current_value.strip() == "":
                    try:
                        from utils.config import config as env_config
                        default_email = getattr(env_config, 'EMAIL_TO', None) or getattr(env_config, 'email_to', None)
                        if default_email and default_email.strip():
                            field_copy["value"] = str(default_email).strip()
                        else:
                            field_copy["value"] = ""
                    except Exception as e:
                        self.logger.error(f"读取默认邮箱失败: {e}")
                        field_copy["value"] = ""

            processed_fields.append(field_copy)

        return processed_fields

    def _generate_config_summary(self, session: WizardSession) -> str:
        data = session.collected_data
        summary_parts = []

        if "account_id" in data:
            summary_parts.append(f"账号: {data['account_id']}")

        if "monitor_type" in data:
            type_map = {
                "keyword": "关键词监控",
                "file": "文件监控",
                "ai": "AI智能监控"
            }
            summary_parts.append(f"类型: {type_map.get(data['monitor_type'], data['monitor_type'])}")

        if data.get("monitor_type") == "keyword":
            if "keyword" in data:
                summary_parts.append(f"关键词: {data['keyword']}")
            if "match_type" in data:
                summary_parts.append(f"匹配方式: {data['match_type']}")
        elif data.get("monitor_type") == "file":
            if "file_extension" in data:
                summary_parts.append(f"文件类型: {data['file_extension']}")
        elif data.get("monitor_type") == "ai":
            if "ai_prompt" in data:
                summary_parts.append(f"AI规则: {data['ai_prompt'][:50]}...")

        if "chats" in data:
            summary_parts.append(f"监控群组: {data['chats']}")

        if data.get("email_notify"):
            summary_parts.append("✓ 邮件通知")
        if data.get("auto_forward"):
            summary_parts.append("✓ 自动转发")
            if data.get("enhanced_forward"):
                summary_parts.append("✓ 增强转发")

        return "\n".join(summary_parts)

    def process_step(self, session_id: str, step_data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            self.logger.debug(f"处理步骤，session_id: {session_id}")

            if session_id not in self.sessions:
                self.logger.warning(f"会话 {session_id} 不存在")
                return {
                    "success": False,
                    "errors": ["会话已过期，请重新开始配置"],
                    "message": "会话已过期，请重新开始配置"
                }

            session = self.sessions[session_id]
            step = self.steps[session.current_step]

            errors = self._validate_step_data(step, step_data)
            session.errors = errors

            if errors:
                return {
                    "success": False,
                    "errors": errors,
                    "step_data": self.get_step_data(session_id)
                }


            monitor_type = session.collected_data.get('monitor_type')
            account_id = session.collected_data.get('account_id')
            edit_key = session.collected_data.get('edit_key')

            session.collected_data.update(step_data)

            if monitor_type and 'monitor_type' not in step_data:
                session.collected_data['monitor_type'] = monitor_type
            if account_id and 'account_id' not in step_data:
                session.collected_data['account_id'] = account_id
            if edit_key and 'edit_key' not in step_data:
                session.collected_data['edit_key'] = edit_key

            session.completed_steps.append(session.current_step)

            next_step = self._get_next_step(step, step_data)

            if next_step:
                session.current_step = next_step
                return {
                    "success": True,
                    "next_step": self.get_step_data(session_id)
                }
            else:
                result = self._complete_configuration(session)
                self._cleanup_session(session_id)
                return result

        except Exception as e:
            self.logger.error(f"处理向导步骤失败: {e}")
            return {
                "success": False,
                "errors": [f"处理失败: {str(e)}"],
                "message": f"处理失败: {str(e)}"
            }

    def go_to_previous_step(self, session_id: str) -> Dict[str, Any]:
        try:
            if session_id not in self.sessions:
                return {
                    "success": False,
                    "errors": ["会话已过期"],
                    "message": "会话已过期"
                }

            session = self.sessions[session_id]

            if session.completed_steps:
                last_step = session.completed_steps.pop()
                session.current_step = last_step

                return {
                    "success": True,
                    "step_data": self.get_step_data(session_id)
                }
            else:
                return {
                    "success": False,
                    "errors": ["已经是第一步"],
                    "message": "已经是第一步"
                }

        except Exception as e:
            self.logger.error(f"返回上一步失败: {e}")
            return {
                "success": False,
                "errors": [f"操作失败: {str(e)}"],
                "message": f"操作失败: {str(e)}"
            }

    def _validate_step_data(self, step: WizardStep, data: Dict[str, Any]) -> List[str]:
        errors = []

        if not step.validation_rules:
            return errors

        for field_name, rules in step.validation_rules.items():
            value = data.get(field_name)

            if rules.get("required") and not value:
                error_msg = rules.get("message") or f"{field_name}是必填项"
                errors.append(error_msg)
                continue

            if "required_if" in rules:
                condition = rules["required_if"]
                should_require = all(
                    (data.get(k) in (True, "on", "true", "1") if isinstance(v, bool) and v else data.get(k) == v)
                    for k, v in condition.items()
                )
                if should_require and not value:
                    error_msg = rules.get("message") or f"在当前配置下{field_name}是必填项"
                    errors.append(error_msg)
                    continue

            if value:
                if "min_length" in rules and len(str(value)) < rules["min_length"]:
                    errors.append(f"{field_name}长度不能少于{rules['min_length']}个字符")

                if rules.get("email_format"):
                    import re
                    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
                    if not re.match(email_pattern, value):
                        errors.append(f"{field_name}邮箱格式不正确")

        return errors

    def _get_next_step(self, step: WizardStep, step_data: Dict[str, Any]) -> Optional[WizardStepType]:
        if step.conditional_next:
            if "monitor_type" in step_data:
                monitor_type = step_data.get("monitor_type")
                if monitor_type in step.conditional_next:
                    return step.conditional_next[monitor_type]

            if "auto_forward" in step_data:
                auto_forward = step_data.get("auto_forward")
                if auto_forward in (True, "on", "true", "1") and "auto_forward" in step.conditional_next:
                    return step.conditional_next["auto_forward"]

        return step.next_step

    def _complete_configuration(self, session: WizardSession) -> Dict[str, Any]:
        try:
            data = session.collected_data

            monitor_type = data.get("monitor_type")
            account_id = data.get("account_id")

            if not monitor_type or not account_id:
                self.logger.error(f"缺少必要的配置信息: monitor_type={monitor_type}, account_id={account_id}")
                self.logger.error(f"当前会话数据: {data}")
                self.logger.error(f"完成的步骤: {session.completed_steps}")
                return {
                    "success": False,
                    "errors": ["缺少必要的配置信息，请确保已选择账号和监控类型"],
                    "message": "缺少必要的配置信息，请确保已选择账号和监控类型"
                }

            edit_key = data.get("edit_key")
            if edit_key:
                from core import MonitorEngine
                monitor_engine = MonitorEngine()
                monitor_engine.remove_monitor(account_id, edit_key)
                self.logger.info(f"编辑模式：已删除旧配置 {edit_key}")

            if monitor_type == "keyword":
                config = self._create_keyword_config(data)
                monitor = monitor_factory.create_monitor(config)
                monitor_key = f"keyword_{data['keyword']}"

            elif monitor_type == "file":
                extensions_str = data.get("file_extension", "")
                extensions = [ext.strip() for ext in extensions_str.split(",") if ext.strip()]

                if not extensions:
                    return {
                        "success": False,
                        "errors": ["请至少指定一个文件扩展名"],
                        "message": "请至少指定一个文件扩展名"
                    }

                created_monitors = []
                for ext in extensions:
                    file_data = data.copy()
                    file_data['file_extension'] = ext

                    config = self._create_file_config(file_data)
                    monitor = monitor_factory.create_monitor(config)
                    monitor_key = f"file_{ext}"

                    from core import MonitorEngine
                    monitor_engine = MonitorEngine()
                    monitor_engine.add_monitor(account_id, monitor, monitor_key)
                    created_monitors.append(ext)

                return {
                    "success": True,
                    "message": f"成功创建 {len(created_monitors)} 个文件监控器: {', '.join(created_monitors)}",
                    "monitor_keys": [f"file_{ext}" for ext in created_monitors],
                    "config_summary": self._generate_config_summary(session)
                }

            elif monitor_type == "button":
                if data.get("monitor_subtype") == "image_button":
                    config = self._create_image_button_config(data)
                    monitor = monitor_factory.create_monitor(config)
                    monitor_key = f"image_button_{data.get('image_ai_prompt', '')[:20]}..."
                else:
                    config = self._create_button_config(data)
                    monitor = monitor_factory.create_monitor(config)
                    monitor_key = f"button_{data['button_keyword']}"

            elif monitor_type == "all_messages":
                config = self._create_all_messages_config(data)
                monitor = monitor_factory.create_monitor(config)
                monitor_key = f"all_messages_{data['chat_id']}"

            elif monitor_type == "ai":
                ai_monitor = self._create_ai_monitor(data)
                monitor = ai_monitor
                monitor_key = f"ai_{data['ai_prompt'][:20]}..."

            else:
                return {
                    "success": False,
                    "errors": [f"不支持的监控类型: {monitor_type}"],
                    "message": f"不支持的监控类型: {monitor_type}"
                }

            from core import MonitorEngine
            monitor_engine = MonitorEngine()
            monitor_engine.add_monitor(account_id, monitor, monitor_key)

            return {
                "success": True,
                "message": "监控器创建成功！",
                "monitor_key": monitor_key,
                "config_summary": self._generate_config_summary(session)
            }

        except Exception as e:
            self.logger.error(f"完成配置失败: {e}")
            return {
                "success": False,
                "message": f"配置失败: {str(e)}"
            }

    def _create_keyword_config(self, data: Dict[str, Any]) -> KeywordConfig:
        chats = []
        chats_str = data.get('chats', '')
        if chats_str:
            for chat in chats_str.split(','):
                chat = chat.strip()
                if chat:
                    try:
                        chats.append(int(chat))
                    except ValueError:
                        chats.append(chat)

        forward_targets = []
        if data.get('auto_forward'):
            targets_str = data.get('forward_targets', '')
            if targets_str:
                for target in targets_str.split(','):
                    target = target.strip()
                    if target:
                        try:
                            forward_targets.append(int(target))
                        except ValueError:
                            forward_targets.append(target)

        reply_texts = []
        if data.get('reply_enabled'):
            texts_str = data.get('reply_texts', '')
            if texts_str:
                reply_texts = [text.strip() for text in texts_str.split('\n') if text.strip()]

        reply_type = data.get('reply_type', 'keyword')
        if reply_type == 'keyword':
            reply_content_type = 'custom'
        else:
            reply_content_type = reply_type

        config = KeywordConfig(
            keyword=data.get('keyword', ''),
            match_type=MatchType(data.get('match_type', 'partial')),
            chats=chats,
            email_notify=data.get('email_notify', False),
            auto_forward=data.get('auto_forward', False),
            forward_targets=forward_targets,
            enhanced_forward=data.get('enhanced_forward', False),
            max_download_size_mb=float(data.get('max_download_size_mb')) if data.get('max_download_size_mb') and data.get('max_download_size_mb').strip() else None,
            log_file=data.get('log_file') if data.get('log_file') else None,
            max_executions=int(data.get('max_executions')) if data.get('max_executions') else None,
            priority=int(data.get('priority', 50)),
            execution_mode=data.get('execution_mode', 'merge'),
            reply_enabled=data.get('reply_enabled', False),
            reply_texts=reply_texts,
            reply_delay_min=float(data.get('reply_delay_min', 0)) if data.get('reply_delay_min') and str(data.get('reply_delay_min')).strip() else 0,
            reply_delay_max=float(data.get('reply_delay_max', 0)) if data.get('reply_delay_max') and str(data.get('reply_delay_max')).strip() else 0,
            reply_mode=data.get('reply_mode', 'reply'),
            reply_content_type=reply_content_type,
            ai_reply_prompt=data.get('ai_reply_prompt', '')
        )

        if data.get('filter_users'):
            users = []
            users_str = data.get('users', '')
            if users_str:
                for user in users_str.split('\n'):
                    user = user.strip()
                    if user:
                        users.append(user)
            config.users = users

        blocked_users = []
        blocked_users_str = data.get('blocked_users', '')
        if blocked_users_str:
            for user in blocked_users_str.split('\n'):
                user = user.strip()
                if user:
                    blocked_users.append(user)
        config.blocked_users = blocked_users

        blocked_channels = []
        blocked_channels_str = data.get('blocked_channels', '')
        if blocked_channels_str:
            for channel in blocked_channels_str.split('\n'):
                channel = channel.strip()
                if channel:
                    try:
                        blocked_channels.append(int(channel))
                    except ValueError:
                        pass
        config.blocked_channels = blocked_channels

        blocked_bots = []
        blocked_bots_str = data.get('blocked_bots', '')
        if blocked_bots_str:
            for bot in blocked_bots_str.split('\n'):
                bot = bot.strip()
                if bot:
                    try:
                        blocked_bots.append(int(bot))
                    except ValueError:
                        pass

        filter_mode = data.get("filter_mode", "blacklist")

        if filter_mode == "specific_ids":
            user_ids = []
            if data.get("user_ids"):
                for line in data["user_ids"].split("\n"):
                    line = line.strip()
                    if line:
                        try:
                            user_ids.append(int(line))
                        except ValueError:
                            user_ids.append(line)
            config.users = user_ids
            config.user_option = '1'

            bot_ids = []
            if data.get("bot_ids"):
                for line in data["bot_ids"].split("\n"):
                    line = line.strip()
                    if line:
                        try:
                            bot_ids.append(int(line))
                        except ValueError:
                            pass
            config.bot_ids = bot_ids

            channel_ids = []
            if data.get("channel_ids"):
                for line in data["channel_ids"].split("\n"):
                    line = line.strip()
                    if line:
                        try:
                            if line.startswith('@'):
                                pass
                            else:
                                parsed_id = int(line)
                                channel_ids.append(parsed_id)
                                self.logger.debug(f"解析频道ID: {line} -> {parsed_id}")
                        except ValueError as e:
                            self.logger.warning(f"无效的频道ID格式: {line}, 错误: {e}")

            if data.get("group_ids"):
                for line in data["group_ids"].split("\n"):
                    line = line.strip()
                    if line and line not in [str(cid) for cid in channel_ids]:
                        try:
                            if line.startswith('@'):
                                pass
                            else:
                                parsed_id = int(line)
                                channel_ids.append(parsed_id)
                                self.logger.debug(f"解析群组ID: {line} -> {parsed_id}")
                        except ValueError as e:
                            self.logger.warning(f"无效的群组ID格式: {line}, 错误: {e}")

            config.channel_ids = channel_ids
            config.group_ids = []

            self.logger.info(f"✅ [精确ID过滤配置] 用户IDs: {config.users}, Bot IDs: {config.bot_ids}, 频道/群组 IDs: {config.channel_ids}")
        else:
            config.bot_ids = []
            config.channel_ids = []
            config.group_ids = []

        return config

    def _create_file_config(self, data: Dict[str, Any]) -> FileConfig:
        chats_str = data.get("chats", "")
        chat_ids = []

        if chats_str:
            for chat in chats_str.split(","):
                chat = chat.strip()
                if chat:
                    try:
                        chat_ids.append(int(chat))
                    except ValueError:
                        pass

        extensions_str = data.get("file_extension", "")
        extensions = [ext.strip() for ext in extensions_str.split(",") if ext.strip()]

        auto_forward = data.get("auto_forward") in (True, "on", "true", "1")
        email_notify = data.get("email_notify") in (True, "on", "true", "1")
        enhanced_forward = data.get("enhanced_forward") in (True, "on", "true", "1")
        save_files = data.get("save_files") in (True, "on", "true", "1")
        filter_users = data.get("filter_users") in (True, "on", "true", "1")
        log_to_file = data.get("log_to_file") in (True, "on", "true", "1")
        filter_specific_ids = data.get("filter_specific_ids") in (True, "on", "true", "1")
        filter_mode = data.get("filter_mode", "blacklist")

        users = []
        if filter_users and data.get("users"):
            for line in data["users"].split("\n"):
                line = line.strip()
                if line:
                    try:
                        users.append(int(line))
                    except ValueError:
                        users.append(line)

        if filter_mode == "specific_ids" and data.get("user_ids"):
            for line in data["user_ids"].split("\n"):
                line = line.strip()
                if line:
                    try:
                        users.append(int(line))
                    except ValueError:
                        users.append(line)

        blocked_users = [line.strip() for line in data.get("blocked_users", "").split("\n") if line.strip()]
        blocked_channels = []
        if data.get("blocked_channels"):
            for line in data["blocked_channels"].split("\n"):
                line = line.strip()
                if line:
                    try:
                        blocked_channels.append(int(line))
                    except ValueError:
                        pass

        blocked_bots = []
        if data.get("blocked_bots"):
            for line in data["blocked_bots"].split("\n"):
                line = line.strip()
                if line:
                    try:
                        blocked_bots.append(int(line))
                    except ValueError:
                        pass

        bot_ids = []
        if filter_specific_ids and data.get("bot_ids"):
            for line in data["bot_ids"].split("\n"):
                line = line.strip()
                if line:
                    try:
                        bot_ids.append(int(line))
                    except ValueError:
                        pass

        channel_ids = []
        if filter_specific_ids and data.get("channel_ids"):
            for line in data["channel_ids"].split("\n"):
                line = line.strip()
                if line:
                    try:
                        channel_ids.append(int(line))
                    except ValueError:
                        pass

        group_ids = []
        if filter_specific_ids and data.get("group_ids"):
            for line in data["group_ids"].split("\n"):
                line = line.strip()
                if line:
                    try:
                        group_ids.append(int(line))
                    except ValueError:
                        pass

        forward_targets = []
        if auto_forward and data.get("forward_targets"):
            targets_str = data.get("forward_targets", "")
            for target in targets_str.split(","):
                target = target.strip()
                if target:
                    try:
                        forward_targets.append(int(target))
                    except ValueError:
                        pass

        min_size = None
        if data.get("min_size_kb") and str(data.get("min_size_kb")).strip():
            try:
                min_size = float(data["min_size_kb"]) / 1024
            except (ValueError, TypeError):
                min_size = None

        max_size = None
        if data.get("max_size_mb") and str(data.get("max_size_mb")).strip():
            try:
                max_size = float(data["max_size_mb"])
            except (ValueError, TypeError):
                max_size = None

        max_download_size = None
        if data.get("max_download_size"):
            try:
                max_download_size = float(data["max_download_size"])
            except (ValueError, TypeError):
                max_download_size = None

        max_executions = None
        if data.get("max_executions") and str(data.get("max_executions")).strip():
            try:
                max_executions = int(data["max_executions"])
            except (ValueError, TypeError):
                max_executions = None

        configs = []
        for ext in extensions:
            config = FileConfig(
                file_extension=ext,
                chats=chat_ids,
                users=users,
                blocked_users=blocked_users,
                blocked_channels=blocked_channels,
                blocked_bots=blocked_bots,
                bot_ids=bot_ids,
                channel_ids=channel_ids,
                group_ids=group_ids,
                save_folder=data.get("save_folder") if save_files else None,
                min_size=min_size,
                max_size=max_size,
                email_notify=email_notify,
                auto_forward=auto_forward,
                forward_targets=forward_targets,
                enhanced_forward=enhanced_forward,
                max_download_size_mb=max_download_size,
                max_executions=max_executions,
                priority=int(data.get('priority', 50)),
                execution_mode=data.get('execution_mode', 'merge'),
                log_file=data.get("log_file") if log_to_file else None
            )
            configs.append(config)

        return configs[0] if configs else FileConfig()

    def _create_ai_monitor(self, data: Dict[str, Any]):
        chats_str = data.get("chats", "")
        chat_ids = []

        if chats_str:
            for chat in chats_str.split(","):
                chat = chat.strip()
                if chat:
                    try:
                        chat_ids.append(int(chat))
                    except ValueError:
                        pass

        auto_forward = data.get("auto_forward") in (True, "on", "true", "1")
        email_notify = data.get("email_notify") in (True, "on", "true", "1")
        enhanced_forward = data.get("enhanced_forward") in (True, "on", "true", "1")
        reply_enabled = data.get("reply_enabled") in (True, "on", "true", "1")

        forward_targets = []
        if auto_forward and data.get("forward_targets"):
            targets_str = data.get("forward_targets", "")
            for target in targets_str.split(","):
                target = target.strip()
                if target:
                    try:
                        forward_targets.append(int(target))
                    except ValueError:
                        pass

        confidence_threshold = 0.7
        if data.get("confidence_threshold"):
            try:
                confidence_threshold = float(data["confidence_threshold"])
            except (ValueError, TypeError):
                confidence_threshold = 0.7

        builder = AIMonitorBuilder()
        builder.with_prompt(data.get("ai_prompt", ""))
        builder.with_chats(chat_ids)
        builder.with_confidence_threshold(confidence_threshold)

        if email_notify:
            builder.with_email_notify(True)

        if auto_forward:
            builder.with_auto_forward(True, forward_targets)

        if enhanced_forward:
            max_size = None
            if data.get("max_download_size"):
                try:
                    max_size = float(data["max_download_size"])
                except (ValueError, TypeError):
                    max_size = None
            builder.with_enhanced_forward(True, max_size)

        if reply_enabled:
            reply_texts = []
            if data.get("reply_texts"):
                reply_texts = [line.strip() for line in data["reply_texts"].split("\n") if line.strip()]

            reply_delay_min = 0
            reply_delay_max = 5
            try:
                reply_delay_min = float(data.get("reply_delay_min", 0))
                reply_delay_max = float(data.get("reply_delay_max", 5))
            except (ValueError, TypeError):
                reply_delay_min = 0
                reply_delay_max = 5

            reply_mode = data.get("reply_mode", "reply")
            builder.with_reply(True, reply_texts, reply_delay_min, reply_delay_max, reply_mode)

        builder.with_priority(int(data.get('priority', 50)))
        builder.with_execution_mode(data.get('execution_mode', 'merge'))

        return builder.build()

    def _create_button_config(self, data: Dict[str, Any]) -> ButtonConfig:
        chats_str = data.get("chats", "")
        chat_ids = []

        if chats_str:
            for chat in chats_str.split(","):
                chat = chat.strip()
                if chat:
                    try:
                        chat_ids.append(int(chat))
                    except ValueError:
                        pass

        auto_forward = data.get("auto_forward") in (True, "on", "true", "1")
        email_notify = data.get("email_notify") in (True, "on", "true", "1")
        enhanced_forward = data.get("enhanced_forward") in (True, "on", "true", "1")
        filter_users = data.get("filter_users") in (True, "on", "true", "1")
        log_to_file = data.get("log_to_file") in (True, "on", "true", "1")
        filter_specific_ids = data.get("filter_specific_ids") in (True, "on", "true", "1")

        users = []
        if filter_users and data.get("users"):
            for line in data["users"].split("\n"):
                line = line.strip()
                if line:
                    try:
                        users.append(int(line))
                    except ValueError:
                        users.append(line)

        blocked_users = [line.strip() for line in data.get("blocked_users", "").split("\n") if line.strip()]
        blocked_channels = []
        if data.get("blocked_channels"):
            for line in data["blocked_channels"].split("\n"):
                line = line.strip()
                if line:
                    try:
                        blocked_channels.append(int(line))
                    except ValueError:
                        pass

        blocked_bots = []
        if data.get("blocked_bots"):
            for line in data["blocked_bots"].split("\n"):
                line = line.strip()
                if line:
                    try:
                        blocked_bots.append(int(line))
                    except ValueError:
                        pass

        bot_ids = []
        if filter_specific_ids and data.get("bot_ids"):
            for line in data["bot_ids"].split("\n"):
                line = line.strip()
                if line:
                    try:
                        bot_ids.append(int(line))
                    except ValueError:
                        pass

        channel_ids = []
        if filter_specific_ids and data.get("channel_ids"):
            for line in data["channel_ids"].split("\n"):
                line = line.strip()
                if line:
                    try:
                        channel_ids.append(int(line))
                    except ValueError:
                        pass

        group_ids = []
        if filter_specific_ids and data.get("group_ids"):
            for line in data["group_ids"].split("\n"):
                line = line.strip()
                if line:
                    try:
                        group_ids.append(int(line))
                    except ValueError:
                        pass

        forward_targets = []
        if auto_forward and data.get("forward_targets"):
            targets_str = data.get("forward_targets", "")
            for target in targets_str.split(","):
                target = target.strip()
                if target:
                    try:
                        forward_targets.append(int(target))
                    except ValueError:
                        pass

        max_executions = None
        if data.get("max_executions"):
            try:
                max_executions = int(data["max_executions"])
            except (ValueError, TypeError):
                max_executions = None

        max_download_size = None
        if data.get("max_download_size"):
            try:
                max_download_size = float(data["max_download_size"])
            except (ValueError, TypeError):
                max_download_size = None

        return ButtonConfig(
            button_keyword=data.get("button_keyword", ""),
            mode=MonitorMode(data.get("mode", "manual")),
            ai_prompt=data.get("ai_prompt", ""),
            chats=chat_ids,
            users=users,
            blocked_users=blocked_users,
            blocked_channels=blocked_channels,
            blocked_bots=blocked_bots,
            bot_ids=bot_ids,
            channel_ids=channel_ids,
            group_ids=group_ids,
            email_notify=email_notify,
            auto_forward=auto_forward,
            forward_targets=forward_targets,
            enhanced_forward=enhanced_forward,
            max_download_size_mb=max_download_size,
            max_executions=max_executions,
            priority=int(data.get('priority', 50)),
            execution_mode=data.get('execution_mode', 'merge'),
            log_file=data.get("log_file") if log_to_file else None
        )

    def _create_image_button_config(self, data: Dict[str, Any]):
        chats_str = data.get("chats", "")
        chat_ids = []

        if chats_str:
            for chat in chats_str.split(","):
                chat = chat.strip()
                if chat:
                    try:
                        chat_ids.append(int(chat))
                    except ValueError:
                        pass

        auto_forward = data.get("auto_forward") in (True, "on", "true", "1")
        email_notify = data.get("email_notify") in (True, "on", "true", "1")
        enhanced_forward = data.get("enhanced_forward") in (True, "on", "true", "1")
        download_images = data.get("download_images") in (True, "on", "true", "1")
        filter_users = data.get("filter_users") in (True, "on", "true", "1")
        log_to_file = data.get("log_to_file") in (True, "on", "true", "1")
        filter_specific_ids = data.get("filter_specific_ids") in (True, "on", "true", "1")

        button_keywords = []
        if data.get("button_keywords"):
            button_keywords = [kw.strip() for kw in data["button_keywords"].split(",") if kw.strip()]

        confidence_threshold = 0.7
        if data.get("confidence_threshold"):
            try:
                confidence_threshold = float(data["confidence_threshold"])
            except (ValueError, TypeError):
                confidence_threshold = 0.7

        users = []
        if filter_users and data.get("users"):
            for line in data["users"].split("\n"):
                line = line.strip()
                if line:
                    try:
                        users.append(int(line))
                    except ValueError:
                        users.append(line)

        blocked_users = [line.strip() for line in data.get("blocked_users", "").split("\n") if line.strip()]
        blocked_channels = []
        if data.get("blocked_channels"):
            for line in data["blocked_channels"].split("\n"):
                line = line.strip()
                if line:
                    try:
                        blocked_channels.append(int(line))
                    except ValueError:
                        pass

        blocked_bots = []
        if data.get("blocked_bots"):
            for line in data["blocked_bots"].split("\n"):
                line = line.strip()
                if line:
                    try:
                        blocked_bots.append(int(line))
                    except ValueError:
                        pass

        bot_ids = []
        if filter_specific_ids and data.get("bot_ids"):
            for line in data["bot_ids"].split("\n"):
                line = line.strip()
                if line:
                    try:
                        bot_ids.append(int(line))
                    except ValueError:
                        pass

        channel_ids = []
        if filter_specific_ids and data.get("channel_ids"):
            for line in data["channel_ids"].split("\n"):
                line = line.strip()
                if line:
                    try:
                        channel_ids.append(int(line))
                    except ValueError:
                        pass

        group_ids = []
        if filter_specific_ids and data.get("group_ids"):
            for line in data["group_ids"].split("\n"):
                line = line.strip()
                if line:
                    try:
                        group_ids.append(int(line))
                    except ValueError:
                        pass

        forward_targets = []
        if auto_forward and data.get("forward_targets"):
            targets_str = data.get("forward_targets", "")
            for target in targets_str.split(","):
                target = target.strip()
                if target:
                    try:
                        forward_targets.append(int(target))
                    except ValueError:
                        pass

        max_executions = None
        if data.get("max_executions"):
            try:
                max_executions = int(data["max_executions"])
            except (ValueError, TypeError):
                max_executions = None

        max_download_size = None
        if data.get("max_download_size"):
            try:
                max_download_size = float(data["max_download_size"])
            except (ValueError, TypeError):
                max_download_size = None

        return ImageButtonConfig(
            ai_prompt=data.get("image_ai_prompt", "分析图片和按钮内容，判断是否需要点击某个按钮"),
            button_keywords=button_keywords,
            download_images=download_images,
            confidence_threshold=confidence_threshold,
            chats=chat_ids,
            users=users,
            blocked_users=blocked_users,
            blocked_channels=blocked_channels,
            blocked_bots=blocked_bots,
            bot_ids=bot_ids,
            channel_ids=channel_ids,
            group_ids=group_ids,
            email_notify=email_notify,
            auto_forward=auto_forward,
            forward_targets=forward_targets,
            enhanced_forward=enhanced_forward,
            max_download_size_mb=max_download_size,
            max_executions=max_executions,
            priority=int(data.get('priority', 50)),
            execution_mode=data.get('execution_mode', 'merge'),
            log_file=data.get("log_file") if log_to_file else None
        )

    def _create_all_messages_config(self, data: Dict[str, Any]) -> AllMessagesConfig:
        chat_id = 0
        if data.get("chat_id"):
            try:
                chat_id = int(data["chat_id"])
            except ValueError:
                pass

        auto_forward = data.get("auto_forward") in (True, "on", "true", "1")
        email_notify = data.get("email_notify") in (True, "on", "true", "1")
        enhanced_forward = data.get("enhanced_forward") in (True, "on", "true", "1")
        reply_enabled = data.get("reply_enabled") in (True, "on", "true", "1")
        filter_users = data.get("filter_users") in (True, "on", "true", "1")
        log_to_file = data.get("log_to_file") in (True, "on", "true", "1")

        reply_texts = []
        if reply_enabled and data.get("reply_texts"):
            reply_texts = [line.strip() for line in data["reply_texts"].split("\n") if line.strip()]

        users = []
        if filter_users and data.get("users"):
            for line in data["users"].split("\n"):
                line = line.strip()
                if line:
                    try:
                        users.append(int(line))
                    except ValueError:
                        users.append(line)

        blocked_users = [line.strip() for line in data.get("blocked_users", "").split("\n") if line.strip()]
        blocked_channels = []
        if data.get("blocked_channels"):
            for line in data["blocked_channels"].split("\n"):
                line = line.strip()
                if line:
                    try:
                        blocked_channels.append(int(line))
                    except ValueError:
                        pass

        blocked_bots = []
        if data.get("blocked_bots"):
            for line in data["blocked_bots"].split("\n"):
                line = line.strip()
                if line:
                    try:
                        blocked_bots.append(int(line))
                    except ValueError:
                        pass

        forward_targets = []
        if auto_forward and data.get("forward_targets"):
            targets_str = data.get("forward_targets", "")
            for target in targets_str.split(","):
                target = target.strip()
                if target:
                    try:
                        forward_targets.append(int(target))
                    except ValueError:
                        pass

        max_executions = None
        if data.get("max_executions"):
            try:
                max_executions = int(data["max_executions"])
            except (ValueError, TypeError):
                max_executions = None

        max_download_size = None
        if data.get("max_download_size"):
            try:
                max_download_size = float(data["max_download_size"])
            except (ValueError, TypeError):
                max_download_size = None

        reply_delay_min = 0
        reply_delay_max = 0
        if reply_enabled:
            try:
                reply_delay_min = float(data.get("reply_delay_min", 0))
                reply_delay_max = float(data.get("reply_delay_max", 5))
            except (ValueError, TypeError):
                reply_delay_min = 0
                reply_delay_max = 5

        return AllMessagesConfig(
            chat_id=chat_id,
            chats=[chat_id] if chat_id else [],
            users=users,
            blocked_users=blocked_users,
            blocked_channels=blocked_channels,
            blocked_bots=blocked_bots,
            email_notify=email_notify,
            auto_forward=auto_forward,
            forward_targets=forward_targets,
            enhanced_forward=enhanced_forward,
            max_download_size_mb=max_download_size,
            reply_enabled=reply_enabled,
            reply_texts=reply_texts,
            reply_delay_min=reply_delay_min,
            reply_delay_max=reply_delay_max,
            reply_mode=ReplyMode(data.get('reply_mode', 'reply')),
            reply_content_type=ReplyContentType(data.get('reply_content_type', 'custom')),
            ai_reply_prompt=data.get('ai_reply_prompt', ''),
            max_executions=max_executions,
            priority=int(data.get('priority', 50)),
            execution_mode=data.get('execution_mode', 'merge'),
            log_file=data.get("log_file") if log_to_file else None
        )

    def _cleanup_session(self, session_id: str):
        if session_id in self.sessions:
            del self.sessions[session_id]

    def force_new_session(self, session_id: str) -> Dict[str, Any]:
        self._cleanup_session(session_id)
        self.logger.info(f"强制创建新会话: {session_id}")

        return self.start_wizard(session_id)

    def get_available_accounts(self) -> List[Dict[str, str]]:
        from core import AccountManager
        account_manager = AccountManager()
        accounts = account_manager.list_accounts()

        return [
            {
                "id": acc.account_id,
                "name": acc.config.phone or str(acc.account_id),
                "phone": acc.config.phone,
                "connected": acc.is_connected()
            }
            for acc in accounts
        ]

    def validate_email_list(self, email_text: str) -> Dict[str, Any]:
        import re
        
        if not email_text or not email_text.strip():
            return {"valid": False, "message": "请输入至少一个邮箱地址"}
        
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        emails = [email.strip() for email in email_text.strip().split('\n') if email.strip()]
        
        invalid_emails = []
        for email in emails:
            if not re.match(email_pattern, email):
                invalid_emails.append(email)
        
        if invalid_emails:
            return {
                "valid": False, 
                "message": f"以下邮箱地址格式不正确: {', '.join(invalid_emails)}"
            }
        
        return {"valid": True, "emails": emails}                                  
