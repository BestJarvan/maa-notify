#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MAA 运行状态监控。

只读 MAA 的日志/任务配置/进程状态，不启动、不停止、不修改 MAA。
判定完成后通过飞书自定义机器人 Webhook 推送卡片。

兼容 Python 3.9+，只使用标准库。
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import hmac
import json
import logging
import logging.handlers
import os
import plistlib
import re
import signal
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

APP_NAME = "MaaNotify"
APP_SUPPORT = Path.home() / "Library" / "Application Support" / APP_NAME
LOG_DIR = Path.home() / "Library" / "Logs" / APP_NAME
STATE_PATH = APP_SUPPORT / "state.json"
HISTORY_PATH = APP_SUPPORT / "runs.jsonl"
LOG_PATH = LOG_DIR / "maa-notify.log"
DEFAULT_CONFIG_PATH = Path(__file__).resolve().with_name("config.json")

MAA_BUNDLE_ID = "com.hguandl.MeoAsstMac"
MAA_CONTAINER = Path.home() / "Library" / "Containers" / MAA_BUNDLE_ID / "Data"
MAA_DEBUG = MAA_CONTAINER / "Library" / "Application Support" / "debug"
MAA_GUI_LOG = MAA_DEBUG / "gui.log"
MAA_ASST_LOG = MAA_DEBUG / "asst.log"
MAA_ASST_BAK = MAA_DEBUG / "asst.bak.log"
MAA_INTERFACE_DIR = MAA_DEBUG / "interface"
MAA_DAILY_PLIST = (
    MAA_CONTAINER / "Library" / "Application Support" / "DailyTasks" / "Default.plist"
)
MAA_APP_PATH = Path("/Applications/MAA.app")

TASK_LABEL_BY_KEY: Dict[str, str] = {
    "startup": "开始唤醒",
    "recruit": "自动公招",
    "infrast": "基建换班",
    "fight": "刷理智",
    "mall": "收取信用及购物",
    "award": "领取奖励",
    "switchTheme": "更换主题",
    "closedown": "关闭游戏",
    "copilot": "自动抄作业",
    "roguelike": "集成战略",
    "reclamation": "生息演算",
    "custom": "自定义",
}

# gui.log 里可能出现中文名，也可能出现 MaaCore 的英文 taskchain 名。
TASK_KEY_BY_ALIAS: Dict[str, str] = {
    "StartUp": "startup",
    "开始唤醒": "startup",
    "Recruit": "recruit",
    "自动公招": "recruit",
    "Infrast": "infrast",
    "基建换班": "infrast",
    "Fight": "fight",
    "刷理智": "fight",
    "Mall": "mall",
    "收取信用及购物": "mall",
    "领取信用及购物": "mall",
    "Award": "award",
    "领取奖励": "award",
    "SwitchTheme": "switchTheme",
    "更换主题": "switchTheme",
    "CloseDown": "closedown",
    "关闭游戏": "closedown",
    "Copilot": "copilot",
    "自动抄作业": "copilot",
    "Roguelike": "roguelike",
    "集成战略": "roguelike",
    "Reclamation": "reclamation",
    "生息演算": "reclamation",
    "Custom": "custom",
    "自定义": "custom",
}

STATUS_META: Dict[str, Tuple[str, str, str]] = {
    "success": ("✅", "运行成功", "green"),
    "partial": ("⚠️", "部分任务失败", "orange"),
    "failed": ("❌", "运行失败", "red"),
    "cancelled": ("🛑", "已取消", "grey"),
    "timeout": ("⏳", "超时/卡死", "red"),
    "missed": ("❓", "未检测到运行", "grey"),
}

REASON_PATTERNS: List[Tuple[str, str]] = [
    ("StageResource", "关卡识别失败"),
    ("UnsupportedLevel", "关卡不支持"),
    ("connectFailed", "连接模拟器失败"),
    ("appendTaskFailed", "任务添加失败"),
    ("截图失败", "截图失败"),
    ("识别错误", "图像识别错误"),
    ("战斗出错", "战斗出错"),
    ("掉落识别错误", "掉落识别错误"),
    ("TLS", "网络/TLS 错误"),
    ("Timeout", "连接超时"),
]

DEFAULT_CONFIG: Dict[str, Any] = {
    "feishu": {
        # 兼容旧配置；新配置请使用 notify。
        "webhook": "",
        "secret": "",
        "keyword": "MAA",
        "notify_on_success": True,
        "notify_on_partial": True,
        "notify_on_missed": True,
    },
    "notify": {
        "provider": "feishu",
        "webhook": "",
        "secret": "",
        "keyword": "MAA",
        "headers": {},
        "body_template": "",
        "notify_on_success": True,
        "notify_on_partial": True,
        "notify_on_missed": True,
    },
    "maa": {
        "app_path": str(MAA_APP_PATH),
        "gui_log": str(MAA_GUI_LOG),
        "asst_log": str(MAA_ASST_LOG),
        "asst_bak_log": str(MAA_ASST_BAK),
        "interface_dir": str(MAA_INTERFACE_DIR),
        "daily_tasks_plist": str(MAA_DAILY_PLIST),
        "schedule_domain": MAA_BUNDLE_ID,
        "schedule_key": "MAAScheduledDailyTaskTimer",
        "schedule_plist": "",
        "schedule_times": [],
        "fallback_times": ["07:02", "19:01"],
    },
    "policy": {
        "grace_minutes": 30,
        "idle_timeout_minutes": 15,
        "hard_timeout_minutes": 180,
        "poll_seconds": 2,
        "notify_manual_runs": False,
        "optional_tasks": [],
        "include_closedown": False,
        "auto_start_maa": False,
        "auto_quit_maa": False,
        "auto_start_lead_minutes": 5,
        "auto_quit_delay_seconds": 60,
    },
    "runtime": {"dry_run": False},
}

GUI_RE = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:\s+[+-]\d{4})?\]\[([^\]]+)\](.*)$"
)
ASST_ERR_RE = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d{3}\]\[ERR\]\[[^\]]+\]\[[^\]]+\]\s*(.*)$"
)
SCREENSHOT_RE = re.compile(
    r"^(\d{4})\.(\d{2})\.(\d{2})-(\d{2})\.(\d{2})\.(\d{2})\.\d{3}_raw\.png$"
)

log = logging.getLogger("maa-notify")


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------


def expand_path(value: Any) -> Path:
    return Path(os.path.expanduser(str(value)))


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: Path) -> Dict[str, Any]:
    path = expand_path(path)
    if not path.exists():
        raise FileNotFoundError(
            "找不到配置文件 %s。请复制 config.example.json 为 config.json 并填写飞书 Webhook。" % path
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    # 旧配置迁移：feishu -> notify，保留旧用户的开关和密钥。
    if isinstance(raw, dict) and "notify" not in raw and isinstance(raw.get("feishu"), dict):
        legacy = raw["feishu"]
        raw["notify"] = {
            "provider": "feishu",
            "webhook": legacy.get("webhook", ""),
            "secret": legacy.get("secret", ""),
            "keyword": legacy.get("keyword", "MAA"),
            "notify_on_success": legacy.get("notify_on_success", True),
            "notify_on_partial": legacy.get("notify_on_partial", True),
            "notify_on_missed": legacy.get("notify_on_missed", True),
        }
    return deep_merge(DEFAULT_CONFIG, raw)


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False),
        encoding="utf-8",
    )
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.strftime("%Y-%m-%dT%H:%M:%S") if dt else None


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def fmt_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return "%ds" % seconds
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return "%dm%02ds" % (minutes, sec)
    hours, minutes = divmod(minutes, 60)
    return "%dh%02dm" % (hours, minutes)


def fmt_clock(dt: Optional[datetime]) -> str:
    return dt.strftime("%H:%M:%S") if dt else "--:--:--"


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    handlers: List[logging.Handler] = []
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            str(LOG_PATH), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        handlers.append(fh)
    except OSError:
        pass
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    handlers.append(sh)
    root = logging.getLogger()
    root.handlers[:] = handlers
    root.setLevel(level)


# ---------------------------------------------------------------------------
# MAA 任务清单
# ---------------------------------------------------------------------------


@dataclass
class ExpectedTask:
    key: str
    base_label: str
    display: str
    detail: str = ""
    optional: bool = False


def _plist_element_to_obj(elem: ET.Element) -> Any:
    tag = elem.tag
    children = list(elem)
    if tag == "dict":
        out: Dict[str, Any] = {}
        for i in range(0, len(children), 2):
            if i + 1 >= len(children):
                break
            key_elem, value_elem = children[i], children[i + 1]
            out[key_elem.text or ""] = _plist_element_to_obj(value_elem)
        return out
    if tag == "array":
        return [_plist_element_to_obj(child) for child in children]
    if tag == "true":
        return True
    if tag == "false":
        return False
    if tag == "integer":
        try:
            return int((elem.text or "0").strip())
        except ValueError:
            return 0
    if tag == "real":
        try:
            return float((elem.text or "0").strip())
        except ValueError:
            return 0.0
    if tag == "date":
        # 这个 plist 里有 0000-12-30 这种日期，plistlib 会溢出；按字符串处理即可。
        return elem.text or ""
    if tag == "data":
        return elem.text or ""
    return elem.text or ""


def _bplist_length(data: bytes, pos: int, low: int) -> Tuple[int, int]:
    if low != 0xF:
        return low, pos
    marker = data[pos]
    pos += 1
    if marker >> 4 != 0x1:
        raise ValueError("invalid binary plist length marker")
    size = 1 << (marker & 0xF)
    return int.from_bytes(data[pos : pos + size], "big"), pos + size


def parse_binary_plist(data: bytes) -> Any:
    """解析 binary plist，忽略异常日期（MAA 里有 0000-12-30）。

    不用 plutil 子进程：launchd 下给 Python 授权后，子进程不一定继承权限。
    """
    if not data.startswith(b"bplist00") or len(data) < 40:
        raise ValueError("not a binary plist")
    trailer = data[-32:]
    offset_size = trailer[6]
    ref_size = trailer[7]
    num_objects = int.from_bytes(trailer[8:16], "big")
    top_object = int.from_bytes(trailer[16:24], "big")
    table_offset = int.from_bytes(trailer[24:32], "big")
    if offset_size == 0 or ref_size == 0:
        raise ValueError("invalid binary plist trailer")
    offsets = [
        int.from_bytes(
            data[
                table_offset + index * offset_size : table_offset
                + (index + 1) * offset_size
            ],
            "big",
        )
        for index in range(num_objects)
    ]
    cache: Dict[int, Any] = {}

    def read_ref(ref_pos: int) -> int:
        return int.from_bytes(data[ref_pos : ref_pos + ref_size], "big")

    def parse_object(index: int) -> Any:
        if index in cache:
            return cache[index]
        pos = offsets[index]
        marker = data[pos]
        pos += 1
        obj_type = marker >> 4
        low = marker & 0xF

        if obj_type == 0x0:
            value = {0x00: None, 0x08: False, 0x09: True}.get(low)
        elif obj_type == 0x1:
            size = 1 << low
            value = int.from_bytes(data[pos : pos + size], "big", signed=False)
        elif obj_type == 0x2:
            size = 1 << low
            if size == 4:
                value = struct.unpack(">f", data[pos : pos + 4])[0]
            elif size == 8:
                value = struct.unpack(">d", data[pos : pos + 8])[0]
            else:
                raise ValueError("invalid real size")
        elif obj_type == 0x3:
            # 日期对本工具无意义；直接返回占位字符串，避免 datetime 溢出。
            value = "DATE"
        elif obj_type == 0x4:
            length, pos = _bplist_length(data, pos, low)
            value = data[pos : pos + length]
        elif obj_type == 0x5:
            length, pos = _bplist_length(data, pos, low)
            value = data[pos : pos + length].decode("ascii", errors="replace")
        elif obj_type == 0x6:
            length, pos = _bplist_length(data, pos, low)
            value = data[pos : pos + 2 * length].decode("utf-16-be", errors="replace")
        elif obj_type == 0x8:
            value = int.from_bytes(data[pos : pos + low + 1], "big")
        elif obj_type in (0xA, 0xC):
            length, pos = _bplist_length(data, pos, low)
            refs = [read_ref(pos + i * ref_size) for i in range(length)]
            value = [parse_object(ref) for ref in refs]
        elif obj_type == 0xD:
            length, pos = _bplist_length(data, pos, low)
            key_refs = [read_ref(pos + i * ref_size) for i in range(length)]
            value_start = pos + length * ref_size
            value_refs = [
                read_ref(value_start + i * ref_size) for i in range(length)
            ]
            value = {
                parse_object(key): parse_object(val)
                for key, val in zip(key_refs, value_refs)
            }
        else:
            raise ValueError("unsupported binary plist type 0x%x" % obj_type)

        cache[index] = value
        return value

    return parse_object(top_object)


def read_plist(path: Path) -> Any:
    """读取 plist，二进制格式用内置解析器（绕开异常日期和 plutil 权限）。"""
    path = expand_path(path)
    if not path.exists():
        return None
    try:
        data = path.read_bytes()
    except OSError as exc:
        log.warning("读取 plist 失败 %s: %s", path, exc)
        return None
    try:
        return plistlib.loads(data)
    except Exception:
        pass
    try:
        return parse_binary_plist(data)
    except Exception as exc:  # noqa: BLE001
        log.warning("解析 plist 失败 %s: %s", path, exc)
        return None


def _task_detail(key: str, value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    row = value.get("_0")
    if not isinstance(row, dict):
        for candidate in value.values():
            if isinstance(candidate, dict):
                row = candidate
                break
    if not isinstance(row, dict):
        return ""
    if key == "fight":
        stage = row.get("stage")
        return str(stage) if stage else ""
    if key == "switchTheme":
        themes = row.get("themes") or []
        if isinstance(themes, list) and themes:
            return "、".join(str(x) for x in themes[:2])
    return ""


def check_maa_access(cfg: Dict[str, Any]) -> Tuple[bool, str, str]:
    """检查能否读取 MAA 容器。launchd 下通常需要给 Python 授予完全磁盘访问权限。"""
    candidates = [
        expand_path(cfg["maa"]["gui_log"]),
        expand_path(cfg["maa"]["asst_log"]),
        expand_path(cfg["maa"]["daily_tasks_plist"]),
    ]
    for path in candidates:
        try:
            with path.open("rb") as fh:
                fh.read(1)
        except FileNotFoundError:
            continue
        except PermissionError as exc:
            return False, str(path), str(exc)
        except OSError as exc:
            return False, str(path), str(exc)
    return True, "", ""


def python_app_path() -> str:
    exe = str(Path(sys.executable).resolve())
    marker = "/Python.app/"
    if marker in exe:
        return exe.split(marker, 1)[0] + "/Python.app"
    for base in (sys.prefix, getattr(sys, "base_prefix", "")):
        if not base:
            continue
        candidate = Path(base) / "Resources" / "Python.app"
        if candidate.exists():
            return str(candidate)
    return sys.executable


def access_help_text() -> str:
    exe = sys.executable
    app = python_app_path()
    return (
        "launchd 无法读取 MAA 的 sandbox 容器（Operation not permitted）。\n"
        "请给 Python 授予一次“完全磁盘访问权限”：\n"
        "  1. 打开：系统设置 → 隐私与安全性 → 完全磁盘访问权限\n"
        "  2. 点 +，按 Cmd+Shift+G，选择 Python.app：\n"
        "     %s\n"
        "     （或选择 Python 可执行文件：%s）\n"
        "  3. 打开开关，然后执行：\n"
        "     launchctl kickstart -k gui/$(id -u)/com.jarvan.maa-notify\n"
        "  4. 用 doctor 验证：python3 maa_notify.py --config config.json doctor\n"
        % (app, exe)
    )


def load_expected_tasks(cfg: Dict[str, Any]) -> List[ExpectedTask]:
    obj = read_plist(expand_path(cfg["maa"]["daily_tasks_plist"]))
    if not isinstance(obj, list):
        log.warning("未能读取 MAA 每日任务清单，将只根据日志中的任务名判定")
        return []
    out: List[ExpectedTask] = []
    for item in obj:
        if not isinstance(item, dict) or not item.get("enabled"):
            continue
        task = item.get("task")
        if not isinstance(task, dict):
            continue
        for raw_key, value in task.items():
            key = raw_key if raw_key in TASK_LABEL_BY_KEY else str(raw_key)
            if key == "closedown" and not cfg["policy"].get("include_closedown", False):
                # 当前 GUI 版本不打印 CloseDown 的任务日志，默认不纳入完成率。
                continue
            base = TASK_LABEL_BY_KEY.get(key, str(raw_key))
            detail = _task_detail(key, value)
            display = base + ("（%s）" % detail if detail else "")
            out.append(
                ExpectedTask(key=key, base_label=base, display=display, detail=detail)
            )
    return out


def normalize_label(label: str) -> Tuple[str, str]:
    """返回 (base_key, base_label)。未知任务名保留原文。"""
    label = (label or "").strip()
    key = TASK_KEY_BY_ALIAS.get(label)
    if key:
        return key, TASK_LABEL_BY_KEY.get(key, label)
    return label, label


def _schedule_items_to_slots(items: Any) -> List[Tuple[int, int]]:
    slots: List[Tuple[int, int]] = []
    if not isinstance(items, list):
        return slots
    for item in items:
        if not isinstance(item, dict) or not item.get("isEnabled", True):
            continue
        try:
            slots.append((int(item["hour"]), int(item["minute"])))
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(set(slots))


def parse_schedule_times(values: Any) -> List[Tuple[int, int]]:
    slots: List[Tuple[int, int]] = []
    if not isinstance(values, list):
        return slots
    for value in values:
        try:
            hour, minute = str(value).strip().split(":", 1)
            hour_i, minute_i = int(hour), int(minute)
            if 0 <= hour_i <= 23 and 0 <= minute_i <= 59:
                slots.append((hour_i, minute_i))
        except (ValueError, TypeError):
            continue
    return sorted(set(slots))


def read_schedule(cfg: Dict[str, Any]) -> List[Tuple[int, int]]:
    domain = cfg["maa"]["schedule_domain"]
    key = cfg["maa"]["schedule_key"]

    # 优先使用软件自己的定时数组；为空时再读 MAA。
    slots = parse_schedule_times(cfg["maa"].get("schedule_times") or [])
    if slots:
        return slots

    # 菜单栏 App 会把 MAA 的 Preferences plist 镜像到 feed，优先从那里读，
    # 避免 Python 子进程去碰受 TCC 保护的 MAA 容器。
    schedule_plist = str(cfg["maa"].get("schedule_plist") or "").strip()
    if schedule_plist:
        obj = read_plist(expand_path(schedule_plist))
        if isinstance(obj, dict):
            value = obj.get(key)
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except ValueError:
                    value = None
            slots = _schedule_items_to_slots(value)
            if slots:
                return slots

    try:
        proc = subprocess.run(
            ["/usr/bin/defaults", "read", domain, key],
            capture_output=True,
            text=True,
            timeout=8,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            items = json.loads(proc.stdout)
            slots = _schedule_items_to_slots(items)
            if slots:
                return slots
    except Exception as exc:  # noqa: BLE001
        log.warning("读取 MAA 定时失败，使用 fallback_times：%s", exc)

    slots = []
    for item in cfg["maa"].get("fallback_times", []):
        try:
            hour, minute = str(item).split(":", 1)
            slots.append((int(hour), int(minute)))
        except ValueError:
            continue
    return sorted(set(slots))


def auto_start_due(
    now: datetime, schedule: List[Tuple[int, int]], lead_minutes: int
) -> Optional[Tuple[datetime, str]]:
    """在 slot 前 lead_minutes 的窗口内返回要启动 MAA 的 slot。"""
    for hour, minute in schedule:
        slot = datetime(now.year, now.month, now.day, hour, minute)
        start_at = slot - timedelta(minutes=max(0, int(lead_minutes)))
        if start_at <= now < slot:
            return slot, slot.strftime("%Y-%m-%dT%H:%M")
    return None


def maa_process_running(cfg: Dict[str, Any]) -> Optional[bool]:
    app_path = str(cfg["maa"].get("app_path") or MAA_APP_PATH)
    try:
        proc = subprocess.run(
            ["/usr/bin/pgrep", "-f", app_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return proc.returncode == 0
    except Exception:  # noqa: BLE001
        return None


def current_maa_schedule(cfg: Dict[str, Any]) -> Any:
    domain = cfg["maa"]["schedule_domain"]
    key = cfg["maa"]["schedule_key"]
    try:
        proc = subprocess.run(
            ["/usr/bin/defaults", "read", domain, key],
            capture_output=True,
            text=True,
            timeout=8,
        )
        if proc.returncode != 0:
            return None
        return json.loads(proc.stdout.strip())
    except Exception:  # noqa: BLE001
        return None


def write_maa_schedule(
    cfg: Dict[str, Any], slots: List[Tuple[int, int]], restart: bool = True
) -> List[Dict[str, Any]]:
    """写入 MAA 的 MAAScheduledDailyTaskTimer。

    会先备份当前值；如果 MAA 正在运行，先退出，写入后再启动。
    这是控制 MAA 的操作，CLI/UI 必须让用户确认。
    """
    slots = sorted(set((int(h), int(m)) for h, m in slots))
    if not slots:
        raise ValueError("定时列表不能为空")

    domain = cfg["maa"]["schedule_domain"]
    key = cfg["maa"]["schedule_key"]
    app_path = str(cfg["maa"].get("app_path") or MAA_APP_PATH)
    old_value = None
    try:
        old_value = subprocess.check_output(
            ["/usr/bin/defaults", "read", domain, key],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=8,
        ).strip()
    except Exception:  # noqa: BLE001
        old_value = None

    backup_path = APP_SUPPORT / "maa-schedule-backup.json"
    write_json_atomic(
        backup_path,
        {
            "domain": domain,
            "key": key,
            "value": old_value,
            "backed_up_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
    )

    was_running = maa_process_running(cfg) is True
    if was_running:
        quit_proc = subprocess.run(
            [
                "/usr/bin/osascript",
                "-e",
                'tell application id "com.hguandl.MeoAsstMac" to quit',
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if quit_proc.returncode != 0:
            raise RuntimeError("无法退出 MAA，未修改定时：" + quit_proc.stderr[:160])
        deadline = time.time() + 10
        while time.time() < deadline:
            if maa_process_running(cfg) is False:
                break
            time.sleep(0.5)
        if maa_process_running(cfg) is not False:
            raise RuntimeError("MAA 未在 10 秒内退出，未修改定时")

    items = [
        {
            "hour": hour,
            "minute": minute,
            "isEnabled": True,
            "id": str(uuid.uuid4()).upper(),
        }
        for hour, minute in slots
    ]
    json_text = json.dumps(items, ensure_ascii=False, separators=(",", ":"))

    try:
        subprocess.run(
            ["/usr/bin/defaults", "write", domain, key, "-string", json_text],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=10,
        )
        read_back = subprocess.check_output(
            ["/usr/bin/defaults", "read", domain, key],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=8,
        ).strip()
        parsed = json.loads(read_back)
        got = parse_schedule_times(
            [
                "%02d:%02d" % (int(item["hour"]), int(item["minute"]))
                for item in parsed
                if item.get("isEnabled", True)
            ]
        )
        if got != slots:
            raise RuntimeError("写入后校验失败：%s" % (got,))
    except Exception:
        # 尽量恢复旧值
        if old_value:
            try:
                subprocess.run(
                    ["/usr/bin/defaults", "write", domain, key, "-string", old_value],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                )
            except Exception:  # noqa: BLE001
                pass
        if was_running and restart:
            try:
                subprocess.run(
                    ["/usr/bin/open", "-a", app_path],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=15,
                )
            except Exception:  # noqa: BLE001
                pass
        raise

    if was_running and restart:
        subprocess.run(
            ["/usr/bin/open", "-a", app_path],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )

    log.info("已写入 MAA 定时：%s", ", ".join("%02d:%02d" % item for item in slots))
    return items


# ---------------------------------------------------------------------------
# 日志解析
# ---------------------------------------------------------------------------


@dataclass
class GuiEvent:
    ts: datetime
    level: str
    message: str


def parse_gui_line(line: str) -> Optional[GuiEvent]:
    match = GUI_RE.match(line)
    if not match:
        return None
    try:
        ts = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return GuiEvent(ts=ts, level=match.group(2), message=match.group(3).strip())


def event_label(message: str, prefix: str) -> str:
    rest = message[len(prefix) :].strip()
    # "完成任务：刷理智 理智：1/208" 只取任务名
    return rest.split()[0] if rest else ""


def is_run_start_message(message: str) -> bool:
    """真正的运行开始信号；资源更新/连接准备都不能开启一场运行。"""
    return (
        message.startswith("开始任务：")
        or "StartTasksFailed:" in message
        or "连接失败" in message
        or "不支持此版本PlayCover" in message
    )


def read_all_lines(path: Path) -> List[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def read_tail_lines(path: Path, max_bytes: int = 8_000_000) -> List[str]:
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - max_bytes))
            data = fh.read()
        return data.decode("utf-8", errors="replace").splitlines()
    except OSError:
        return []


class GuiTail:
    """增量读取 gui.log，处理文件截断/轮转。"""

    def __init__(self, path: Path):
        self.path = path
        self.offset = 0
        self.ident: Optional[Tuple[int, int]] = None
        self.partial = b""

    def seek_end(self) -> None:
        try:
            st = self.path.stat()
            self.offset = st.st_size
            self.ident = (st.st_dev, st.st_ino)
            self.partial = b""
        except OSError:
            self.offset = 0
            self.ident = None
            self.partial = b""

    def read_new(self) -> List[str]:
        try:
            st = self.path.stat()
        except OSError:
            return []
        ident = (st.st_dev, st.st_ino)
        if self.ident != ident or st.st_size < self.offset:
            log.info("gui.log 发生轮转/截断，从头读取")
            self.ident = ident
            self.offset = 0
            self.partial = b""
        try:
            with self.path.open("rb") as fh:
                fh.seek(self.offset)
                data = fh.read()
                self.offset = fh.tell()
        except OSError:
            return []
        if not data:
            return []
        data = self.partial + data
        raw_lines = data.split(b"\n")
        self.partial = raw_lines.pop()
        return [item.decode("utf-8", errors="replace") for item in raw_lines]


class ReasonFinder:
    def __init__(self, cfg: Dict[str, Any]):
        self.asst = expand_path(cfg["maa"]["asst_log"])
        self.asst_bak = expand_path(cfg["maa"]["asst_bak_log"])

    def find(self, ts: datetime) -> str:
        candidates: List[Tuple[datetime, str]] = []
        for path in (self.asst, self.asst_bak):
            for line in read_tail_lines(path):
                match = ASST_ERR_RE.match(line)
                if not match:
                    continue
                try:
                    line_ts = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                delta = (ts - line_ts).total_seconds()
                if -2 <= delta <= 30:
                    candidates.append((line_ts, match.group(2).strip()))
        if not candidates:
            return ""
        candidates.sort(key=lambda item: item[0])
        raw = candidates[-1][1]
        for needle, label in REASON_PATTERNS:
            if needle.lower() in raw.lower():
                return label
        raw = re.sub(r"^Sub error and not ignored\s+", "", raw)
        raw = raw.strip()
        if len(raw) > 100:
            raw = raw[:97] + "..."
        return raw or "任务出错"


def find_failure_screenshot(cfg: Dict[str, Any], ts: datetime) -> str:
    directory = expand_path(cfg["maa"]["interface_dir"])
    if not directory.exists():
        return ""
    best: Optional[Tuple[float, Path]] = None
    try:
        entries = list(directory.iterdir())
    except OSError:
        return ""
    for path in entries:
        if path.suffix.lower() != ".png":
            continue
        match = SCREENSHOT_RE.match(path.name)
        if not match:
            continue
        try:
            shot_ts = datetime.strptime(
                "%s-%s-%s %s:%s:%s" % match.groups()[:6], "%Y-%m-%d %H:%M:%S"
            )
        except ValueError:
            continue
        delta = (ts - shot_ts).total_seconds()
        if -2 <= delta <= 15:
            if best is None or abs(delta) < abs(best[0]):
                best = (delta, path)
    return str(best[1]) if best else ""


# ---------------------------------------------------------------------------
# 运行状态
# ---------------------------------------------------------------------------


@dataclass
class Attempt:
    start: datetime
    end: Optional[datetime] = None
    state: str = "running"  # running / completed / error
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "start": iso(self.start),
            "end": iso(self.end),
            "state": self.state,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Attempt":
        return cls(
            start=parse_iso(data.get("start")) or datetime.now(),
            end=parse_iso(data.get("end")),
            state=data.get("state", "running"),
            reason=data.get("reason", ""),
        )


@dataclass
class TaskState:
    key: str
    base_label: str
    display: str
    detail: str = ""
    expected: bool = True
    optional: bool = False
    state: str = "pending"  # pending / running / completed / error
    attempts: List[Attempt] = field(default_factory=list)
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    reason: str = ""

    @property
    def had_error(self) -> bool:
        return any(item.state == "error" for item in self.attempts)

    def begin(self, ts: datetime) -> None:
        self.state = "running"
        self.start = ts
        self.end = None
        self.reason = ""
        self.attempts.append(Attempt(start=ts))

    def complete(self, ts: datetime) -> None:
        if not self.attempts:
            self.attempts.append(Attempt(start=self.start or ts))
        attempt = self.attempts[-1]
        attempt.end = ts
        attempt.state = "completed"
        self.state = "completed"
        self.end = ts

    def fail(self, ts: datetime, reason: str) -> None:
        if not self.attempts:
            self.attempts.append(Attempt(start=self.start or ts))
        attempt = self.attempts[-1]
        attempt.end = ts
        attempt.state = "error"
        attempt.reason = reason
        self.state = "error"
        self.end = ts
        self.reason = reason

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "base_label": self.base_label,
            "display": self.display,
            "detail": self.detail,
            "expected": self.expected,
            "optional": self.optional,
            "state": self.state,
            "attempts": [item.to_dict() for item in self.attempts],
            "start": iso(self.start),
            "end": iso(self.end),
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskState":
        return cls(
            key=data.get("key", ""),
            base_label=data.get("base_label", ""),
            display=data.get("display", ""),
            detail=data.get("detail", ""),
            expected=bool(data.get("expected", True)),
            optional=bool(data.get("optional", False)),
            state=data.get("state", "pending"),
            attempts=[Attempt.from_dict(item) for item in data.get("attempts", [])],
            start=parse_iso(data.get("start")),
            end=parse_iso(data.get("end")),
            reason=data.get("reason", ""),
        )


@dataclass
class Run:
    slot_key: str
    slot_time: datetime
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    status: str = "running"
    tasks: List[TaskState] = field(default_factory=list)
    connection_failed: bool = False
    start_failed_reason: str = ""
    stopped: bool = False
    all_done: bool = False
    late: bool = False
    timed_out: bool = False
    interrupted_reason: str = ""
    last_activity: Optional[datetime] = None
    end_pending_since: Optional[datetime] = None
    idle_suspect_since: Optional[datetime] = None
    failure_screenshot: str = ""
    failure_times: List[datetime] = field(default_factory=list)
    sanity_start: str = ""
    sanity_end: str = ""
    last_event_ts: Optional[datetime] = None

    @classmethod
    def create(
        cls,
        slot_key: str,
        slot_time: datetime,
        expected: List[ExpectedTask],
        optional_labels: Optional[List[str]] = None,
        late: bool = False,
    ) -> "Run":
        optional_labels = optional_labels or []
        tasks = []
        for item in expected:
            optional = (
                item.display in optional_labels
                or item.base_label in optional_labels
                or item.key in optional_labels
            )
            tasks.append(
                TaskState(
                    key=item.key,
                    base_label=item.base_label,
                    display=item.display,
                    detail=item.detail,
                    expected=True,
                    optional=optional,
                )
            )
        return cls(slot_key=slot_key, slot_time=slot_time, tasks=tasks, late=late)

    def _any_task_started(self) -> bool:
        return any(item.attempts for item in self.tasks)

    def _pick_task_for_start(self, base_label: str) -> TaskState:
        pending = [
            item
            for item in self.tasks
            if item.expected and item.base_label == base_label and item.state == "pending"
        ]
        if pending:
            return pending[0]

        candidates = [item for item in self.tasks if item.base_label == base_label]
        if len(candidates) == 1:
            return candidates[0]
        errored = [item for item in candidates if item.state == "error"]
        if errored:
            return errored[-1]

        # 不在配置里的任务（例如手动开了集成战略）也记录，便于排查。
        extra = TaskState(
            key=base_label,
            base_label=base_label,
            display=base_label,
            expected=False,
        )
        self.tasks.append(extra)
        return extra

    def _start_task(self, label: str, ts: datetime) -> None:
        _, base_label = normalize_label(label)
        task = self._pick_task_for_start(base_label)
        task.begin(ts)
        self.end_pending_since = None
        self.connection_failed = False
        self.start_failed_reason = ""

    def _complete_task(self, label: str, ts: datetime) -> None:
        _, base_label = normalize_label(label)
        running = [
            item
            for item in self.tasks
            if item.base_label == base_label and item.state == "running"
        ]
        if not running:
            # "完成任务：访问好友" 这类子任务，没有对应的顶层实例，忽略。
            return
        running.sort(key=lambda item: item.start or ts)
        running[0].complete(ts)

    def _error_task(self, label: str, ts: datetime, reason: str) -> None:
        _, base_label = normalize_label(label)
        running = [
            item
            for item in self.tasks
            if item.base_label == base_label and item.state == "running"
        ]
        if not running:
            return
        running.sort(key=lambda item: item.start or ts)
        running[0].fail(ts, reason or "任务出错")
        self.failure_times.append(ts)

    def handle_event(self, event: GuiEvent, reason_finder: Optional[ReasonFinder] = None) -> None:
        ts = event.ts
        message = event.message
        if self.start_time is None:
            self.start_time = ts
        self.last_activity = ts
        self.last_event_ts = ts

        sanity_match = re.search(r"理智：(\d+)/(\d+)", message)
        if sanity_match:
            sanity = "%s/%s" % (sanity_match.group(1), sanity_match.group(2))
            if not self.sanity_start:
                self.sanity_start = sanity
            self.sanity_end = sanity

        if message.startswith("正在连接") or message.startswith("正在运行中"):
            return
        if message.startswith("开始任务："):
            self._start_task(event_label(message, "开始任务："), ts)
            return
        if message.startswith("完成任务："):
            self._complete_task(event_label(message, "完成任务："), ts)
            return
        if message.startswith("任务出错："):
            label = event_label(message, "任务出错：")
            reason = reason_finder.find(ts) if reason_finder else ""
            self._error_task(label, ts, reason)
            return
        if message.startswith("任务已全部完成"):
            # 当前版本的 GUI 不一定会为 CloseDown 单独打印“开始任务/完成任务”，
            # 但出现“任务已全部完成”时核心已经执行了关闭游戏。
            for task in self.tasks:
                if task.expected and task.key == "closedown" and task.state == "pending":
                    task.begin(ts)
                    task.complete(ts)
            self.all_done = True
            self.end_time = ts
            self.end_pending_since = ts
            return
        if message.strip().startswith("已停止"):
            # “任务已全部完成”之后 GUI 也会补一行“已停止”，那只是收尾，不是取消。
            if not self.all_done:
                self.stopped = True
                self.end_time = ts
                self.end_pending_since = ts
            return
        if "连接失败" in message and not self._any_task_started():
            self.connection_failed = True
            self.start_failed_reason = "连接模拟器失败"
            self.end_time = ts
            self.end_pending_since = ts
            return
        if "StartTasksFailed:" in message:
            code = message.split("StartTasksFailed:", 1)[1].strip()
            self.connection_failed = True
            if "connect" in code.lower():
                self.start_failed_reason = "连接模拟器失败"
            else:
                self.start_failed_reason = "启动任务失败（%s）" % code
            self.end_time = ts
            self.end_pending_since = ts
            return
        if "不支持此版本PlayCover" in message:
            self.connection_failed = True
            self.start_failed_reason = "不支持此版本 PlayCover"
            self.end_time = ts
            self.end_pending_since = ts
            return

    def classify(self) -> str:
        expected = [item for item in self.tasks if item.expected]
        observed = [item for item in self.tasks if not item.expected]

        if self.interrupted_reason:
            return "failed"
        if self.connection_failed and not any(
            item.state == "completed" for item in expected
        ):
            return "failed"
        if self.stopped:
            return "cancelled"
        if self.timed_out:
            return "timeout"

        pool = expected or observed
        completed = [item for item in pool if item.state == "completed"]
        failed = [
            item
            for item in pool
            if item.state == "error" and (not item.expected or not item.optional)
        ]
        not_run = [item for item in expected if item.state == "pending"]
        running = [item for item in pool if item.state == "running"]

        if failed or not_run or running:
            return "partial" if completed else "failed"
        if completed:
            return "success"
        return "failed"

    def counts(self) -> Dict[str, int]:
        expected = [item for item in self.tasks if item.expected]
        return {
            "total": len(expected),
            "completed": len([item for item in expected if item.state == "completed"]),
            "failed": len(
                [
                    item
                    for item in expected
                    if item.state == "error" and not item.optional
                ]
            ),
            "not_run": len([item for item in expected if item.state == "pending"]),
            "optional_failed": len(
                [
                    item
                    for item in expected
                    if item.state == "error" and item.optional
                ]
            ),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "slot_key": self.slot_key,
            "slot_time": iso(self.slot_time),
            "start_time": iso(self.start_time),
            "end_time": iso(self.end_time),
            "status": self.status,
            "tasks": [item.to_dict() for item in self.tasks],
            "connection_failed": self.connection_failed,
            "start_failed_reason": self.start_failed_reason,
            "stopped": self.stopped,
            "all_done": self.all_done,
            "late": self.late,
            "timed_out": self.timed_out,
            "interrupted_reason": self.interrupted_reason,
            "last_activity": iso(self.last_activity),
            "end_pending_since": iso(self.end_pending_since),
            "idle_suspect_since": iso(self.idle_suspect_since),
            "failure_screenshot": self.failure_screenshot,
            "failure_times": [iso(item) for item in self.failure_times],
            "sanity_start": self.sanity_start,
            "sanity_end": self.sanity_end,
            "last_event_ts": iso(self.last_event_ts),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Run":
        return cls(
            slot_key=data.get("slot_key", ""),
            slot_time=parse_iso(data.get("slot_time")) or datetime.now(),
            start_time=parse_iso(data.get("start_time")),
            end_time=parse_iso(data.get("end_time")),
            status=data.get("status", "running"),
            tasks=[TaskState.from_dict(item) for item in data.get("tasks", [])],
            connection_failed=bool(data.get("connection_failed", False)),
            start_failed_reason=data.get("start_failed_reason", ""),
            stopped=bool(data.get("stopped", False)),
            all_done=bool(data.get("all_done", False)),
            late=bool(data.get("late", False)),
            timed_out=bool(data.get("timed_out", False)),
            interrupted_reason=data.get("interrupted_reason", ""),
            last_activity=parse_iso(data.get("last_activity")),
            end_pending_since=parse_iso(data.get("end_pending_since")),
            idle_suspect_since=parse_iso(data.get("idle_suspect_since")),
            failure_screenshot=data.get("failure_screenshot", ""),
            failure_times=[
                item
                for item in (parse_iso(x) for x in data.get("failure_times", []))
                if item is not None
            ],
            sanity_start=data.get("sanity_start", ""),
            sanity_end=data.get("sanity_end", ""),
            last_event_ts=parse_iso(data.get("last_event_ts")),
        )


def build_report(run: Run, cfg: Dict[str, Any]) -> Dict[str, Any]:
    status = run.status if run.status != "running" else run.classify()
    counts = run.counts()
    start = run.start_time or run.slot_time
    end = run.end_time or run.last_activity or start
    duration = int((end - start).total_seconds()) if end and start else 0

    tasks = []
    for item in run.tasks:
        duration_seconds = 0
        if item.start and item.end:
            duration_seconds = int((item.end - item.start).total_seconds())
        tasks.append(
            {
                "display": item.display,
                "state": item.state,
                "reason": item.reason,
                "duration_seconds": duration_seconds,
                "attempts": len(item.attempts),
                "expected": item.expected,
                "optional": item.optional,
                "had_error": item.had_error,
            }
        )

    failed_displays = [
        item["display"]
        for item in tasks
        if item["expected"]
        and item["state"] == "error"
        and not item["optional"]
    ]
    not_run_displays = [
        item["display"]
        for item in tasks
        if item["expected"] and item["state"] == "pending"
    ]
    retried_displays = [
        item["display"]
        for item in tasks
        if item["expected"] and item["state"] == "completed" and item["had_error"]
    ]

    return {
        "slot_key": run.slot_key,
        "slot": run.slot_time.strftime("%H:%M"),
        "date": run.slot_time.strftime("%Y-%m-%d"),
        "status": status,
        "late": run.late,
        "start": start.strftime("%Y-%m-%d %H:%M:%S") if start else "",
        "end": end.strftime("%Y-%m-%d %H:%M:%S") if end else "",
        "start_clock": fmt_clock(start),
        "end_clock": fmt_clock(end),
        "duration_seconds": duration,
        "tasks": tasks,
        "counts": counts,
        "failed_displays": failed_displays,
        "not_run_displays": not_run_displays,
        "retried_displays": retried_displays,
        "start_failed_reason": run.start_failed_reason,
        "interrupted_reason": run.interrupted_reason,
        "timed_out": run.timed_out,
        "sanity": (
            "%s → %s" % (run.sanity_start, run.sanity_end)
            if run.sanity_start and run.sanity_end
            else (run.sanity_end or "")
        ),
        "failure_screenshot": run.failure_screenshot,
        "log_path": str(expand_path(cfg["maa"]["gui_log"])),
    }


def task_icon(item: Dict[str, Any]) -> str:
    if item["state"] == "completed":
        return "⚠️" if item["had_error"] else "✅"
    if item["state"] == "error":
        return "❌"
    if item["state"] == "pending":
        return "⏭️"
    if item["state"] == "running":
        return "⏳"
    return "•"


def report_to_text(report: Dict[str, Any]) -> str:
    icon, title, _ = STATUS_META.get(report["status"], ("•", report["status"], "grey"))
    lines = ["%s MAA %s %s" % (icon, report["slot"], title)]
    lines.append(
        "%s – %s（%s）"
        % (report["start_clock"], report["end_clock"], fmt_duration(report["duration_seconds"]))
    )
    counts = report["counts"]
    lines.append("任务：%d/%d 完成" % (counts["completed"], counts["total"]))
    for item in report["tasks"]:
        if not item["expected"] and item["state"] != "error":
            continue
        line = "%s %s" % (task_icon(item), item["display"])
        if item["state"] == "error":
            line += "：%s" % (item["reason"] or "任务出错")
        elif item["state"] == "pending":
            line += "：未执行"
        elif item["state"] == "running":
            line += "：未完成"
        elif item["duration_seconds"]:
            line += "　%s" % fmt_duration(item["duration_seconds"])
        lines.append(line)
    if report.get("start_failed_reason"):
        lines.append("原因：%s" % report["start_failed_reason"])
    if report.get("interrupted_reason"):
        lines.append("中断：%s" % report["interrupted_reason"])
    if report["sanity"]:
        lines.append("理智：%s" % report["sanity"])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 通知（飞书 / 通用 Webhook / Bark / 钉钉 / 企业微信）
# ---------------------------------------------------------------------------


class NotifyError(RuntimeError):
    pass


# 兼容旧代码/旧调用方
FeishuError = NotifyError


def get_notify(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """把新 notify 配置和旧 feishu 配置归一化。"""
    notify = cfg.get("notify") or {}
    feishu = cfg.get("feishu") or {}
    provider = str(notify.get("provider") or "feishu").strip().lower()
    aliases = {
        "lark": "feishu",
        "飞书": "feishu",
        "webhook": "generic",
        "通用": "generic",
        "bark": "bark",
        "dingtalk": "dingtalk",
        "钉钉": "dingtalk",
        "wecom": "wecom",
        "wechat": "wecom",
        "企业微信": "wecom",
    }
    provider = aliases.get(provider, provider)

    def pick(key: str, default: Any = "") -> Any:
        value = notify.get(key)
        if value not in (None, "", {}):
            return value
        return feishu.get(key, default)

    toggle_source = notify if any(
        key in notify
        for key in ("notify_on_success", "notify_on_partial", "notify_on_missed")
    ) else feishu

    return {
        "provider": provider,
        "webhook": str(pick("webhook") or "").strip(),
        "secret": str(pick("secret") or "").strip(),
        "keyword": str(pick("keyword") or "MAA").strip(),
        "headers": pick("headers", {}) if isinstance(pick("headers", {}), dict) else {},
        "body_template": str(notify.get("body_template") or ""),
        "notify_on_success": bool(toggle_source.get("notify_on_success", True)),
        "notify_on_partial": bool(toggle_source.get("notify_on_partial", True)),
        "notify_on_missed": bool(toggle_source.get("notify_on_missed", True)),
    }


def notify_configured(cfg: Dict[str, Any]) -> bool:
    webhook = get_notify(cfg)["webhook"]
    return bool(webhook) and "REPLACE_ME" not in webhook


# 兼容旧名字
def feishu_configured(cfg: Dict[str, Any]) -> bool:
    return notify_configured(cfg)


def report_headline(report: Dict[str, Any]) -> str:
    icon, title_text, _ = STATUS_META.get(
        report["status"], ("•", report["status"], "grey")
    )
    title = "%s MAA %s %s" % (icon, report["slot"], title_text)
    if report.get("late"):
        title += "（补跑）"
    return title


def build_text_report(report: Dict[str, Any]) -> str:
    return report_to_text(report)


def build_feishu_payload(report: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    """构建当前 provider 的消息体（函数名保留以兼容旧调用）。"""
    notify = get_notify(cfg)
    provider = notify["provider"]
    title = report_headline(report)
    text = build_text_report(report)

    if provider == "feishu":
        icon, title_text, template = STATUS_META.get(
            report["status"], ("•", report["status"], "grey")
        )
        keyword = str(notify["keyword"] or "").strip()

        if report["status"] == "missed":
            missed = report.get("missed_body") or "没有检测到 MAA 运行日志。"
            content = "**时间**：%s\n**原因**：%s" % (report["slot"], missed)
        else:
            lines = [
                "**时间**：%s – %s（%s）"
                % (
                    report["start_clock"],
                    report["end_clock"],
                    fmt_duration(report["duration_seconds"]),
                ),
                "**任务**：%d/%d 完成"
                % (report["counts"]["completed"], report["counts"]["total"]),
                "",
            ]
            for item in report["tasks"]:
                if not item["expected"] and item["state"] != "error":
                    continue
                line = "%s %s" % (task_icon(item), item["display"])
                if item["state"] == "error":
                    line += "：%s" % (item["reason"] or "任务出错")
                elif item["state"] == "pending":
                    line += "：未执行"
                elif item["state"] == "running":
                    line += "：未完成"
                elif item["duration_seconds"]:
                    line += "　%s" % fmt_duration(item["duration_seconds"])
                lines.append(line)

            if report.get("failed_displays"):
                lines.append("")
                lines.append("**失败任务**：%s" % "、".join(report["failed_displays"]))
            if report.get("not_run_displays"):
                lines.append("**未执行**：%s" % "、".join(report["not_run_displays"]))
            if report.get("retried_displays"):
                lines.append("**重试后成功**：%s" % "、".join(report["retried_displays"]))
            if report.get("start_failed_reason"):
                lines.append("**原因**：%s" % report["start_failed_reason"])
            if report.get("interrupted_reason"):
                lines.append("**中断**：%s" % report["interrupted_reason"])
            if report.get("timed_out"):
                lines.append("**超时**：运行超过限制仍未结束")
            if report.get("sanity"):
                lines.append("**理智**：%s" % report["sanity"])
            if report.get("failure_screenshot"):
                lines.append("**失败截图**：%s" % report["failure_screenshot"])
            content = "\n".join(lines)

        if keyword and keyword not in content and keyword not in title:
            content += "\n\n" + keyword

        note = "MAA 状态监控 · %s" % datetime.now().strftime("%Y-%m-%d %H:%M")
        if report.get("log_path"):
            note += " · " + report["log_path"]

        return {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {
                    "template": template,
                    "title": {"tag": "plain_text", "content": title},
                },
                "elements": [
                    {"tag": "div", "text": {"tag": "lark_md", "content": content}},
                    {"tag": "hr"},
                    {
                        "tag": "note",
                        "elements": [{"tag": "plain_text", "content": note}],
                    },
                ],
            },
        }

    if provider == "bark":
        return {"title": title, "body": text, "group": "MAA"}

    if provider == "dingtalk":
        return {
            "msgtype": "markdown",
            "markdown": {"title": title, "text": text},
        }

    if provider == "wecom":
        return {"msgtype": "markdown", "markdown": {"content": text}}

    # generic：默认 JSON，可用 body_template 自定义
    template = str(notify.get("body_template") or "").strip()
    if template:
        rendered = (
            template.replace("{{title}}", json.dumps(title, ensure_ascii=False)[1:-1])
            .replace("{{content}}", json.dumps(text, ensure_ascii=False)[1:-1])
            .replace("{{status}}", report["status"])
            .replace("{{slot}}", report["slot"])
            .replace("{{report_json}}", json.dumps(report, ensure_ascii=False))
        )
        try:
            parsed = json.loads(rendered)
            if isinstance(parsed, dict):
                return parsed
        except ValueError:
            log.warning("notify.body_template 不是合法 JSON，回退到默认 JSON")

    return {
        "title": title,
        "content": text,
        "status": report["status"],
        "slot": report["slot"],
        "date": report["date"],
        "report": report,
    }


def send_notification(
    payload: Dict[str, Any], cfg: Dict[str, Any], dry_run: bool = False
) -> bool:
    notify = get_notify(cfg)
    provider = notify["provider"]
    webhook = notify["webhook"]
    if dry_run:
        log.info("dry-run 消息 payload：%s", json.dumps(payload, ensure_ascii=False))
        return True
    if not webhook or "REPLACE_ME" in webhook:
        raise NotifyError("尚未配置消息机器人 Webhook，请编辑 config.json 的 notify.webhook")
    if not webhook.startswith("https://") and not webhook.startswith("http://"):
        raise NotifyError("Webhook 格式不正确")

    body = copy.deepcopy(payload)
    secret = notify["secret"]
    headers = {"Content-Type": "application/json; charset=utf-8"}
    headers.update(notify["headers"])

    if provider == "feishu" and secret:
        timestamp = str(int(time.time()))
        string_to_sign = "%s\n%s" % (timestamp, secret)
        digest = hmac.new(
            string_to_sign.encode("utf-8"), digestmod=hashlib.sha256
        ).digest()
        body["timestamp"] = timestamp
        body["sign"] = base64.b64encode(digest).decode("utf-8")

    if provider == "dingtalk" and secret:
        timestamp = str(int(time.time() * 1000))
        string_to_sign = "%s\n%s" % (timestamp, secret)
        digest = hmac.new(
            secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(digest).decode("utf-8"))
        webhook += ("&" if "?" in webhook else "?") + "timestamp=%s&sign=%s" % (
            timestamp,
            sign,
        )

    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    last_error: Optional[Exception] = None
    for attempt in range(1, 4):
        try:
            request = urllib.request.Request(
                webhook, data=data, headers=headers, method="POST"
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                raw = response.read().decode("utf-8", errors="replace")
            try:
                result = json.loads(raw) if raw.strip() else {}
            except ValueError:
                result = {}
            code = result.get(
                "code", result.get("errcode", result.get("StatusCode", 0))
            )
            try:
                code = int(code) if code is not None else 0
            except (TypeError, ValueError):
                pass
            if code not in (0, 200, None):
                raise NotifyError(
                    "%s 返回错误 code=%s msg=%s"
                    % (
                        provider,
                        code,
                        result.get("msg", result.get("errmsg", raw[:200])),
                    )
                )
            log.info("消息推送成功（%s）", provider)
            return True
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            log.warning("消息推送失败（第 %d/3 次）：%s", attempt, exc)
            if attempt < 3:
                time.sleep(2 if attempt == 1 else 5)
    raise NotifyError("消息推送失败：%s" % last_error)


# 兼容旧函数名
def send_feishu(payload: Dict[str, Any], cfg: Dict[str, Any], dry_run: bool = False) -> bool:
    return send_notification(payload, cfg, dry_run=dry_run)


# ---------------------------------------------------------------------------
# 状态持久化
# ---------------------------------------------------------------------------


def default_state(now: datetime) -> Dict[str, Any]:
    return {
        "version": 1,
        "installed_at": iso(now),
        "gui_offset": 0,
        "gui_ident": None,
        "handled_slots": {},
        "active": None,
        "pending": [],
        "last_run": None,
    }


def load_state(path: Path, now: datetime) -> Dict[str, Any]:
    if not path.exists():
        return default_state(now)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        log.warning("state.json 读取失败，使用新状态：%s", exc)
        return default_state(now)
    base = default_state(now)
    base.update(data if isinstance(data, dict) else {})
    return base


def save_state(path: Path, state: Dict[str, Any], run: Optional[Run], tail: GuiTail) -> None:
    state["active"] = run.to_dict() if run else None
    state["gui_offset"] = tail.offset
    state["gui_ident"] = list(tail.ident) if tail.ident else None
    write_json_atomic(path, state)


def append_history(report: Dict[str, Any]) -> None:
    try:
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with HISTORY_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(report, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("写入历史失败：%s", exc)


# ---------------------------------------------------------------------------
# 监控主循环
# ---------------------------------------------------------------------------


class Watcher:
    def __init__(self, cfg: Dict[str, Any], config_path: Path, dry_run: bool = False):
        self.cfg = cfg
        self.config_path = config_path
        self.feishu_ready = feishu_configured(cfg)
        self.dry_run = (
            dry_run
            or bool(cfg.get("runtime", {}).get("dry_run"))
            or not self.feishu_ready
        )
        self.expected = load_expected_tasks(cfg)
        self.schedule = read_schedule(cfg)
        self.state = load_state(STATE_PATH, datetime.now())
        self.installed_at = parse_iso(self.state.get("installed_at")) or datetime.now()
        self.tail = GuiTail(expand_path(cfg["maa"]["gui_log"]))
        self.reason_finder = ReasonFinder(cfg)
        self.access_ok, self.access_path, self.access_error = check_maa_access(cfg)
        self.last_access_check = 0.0
        self.active: Optional[Run] = None
        self.last_process_check: Optional[datetime] = None
        self.maa_running: Optional[bool] = None
        self.last_save = 0.0
        self.last_schedule_refresh: Optional[datetime] = None
        self.last_pending_flush = 0.0
        self.pending_quit_at: Optional[datetime] = parse_iso(
            self.state.get("pending_quit_at")
        )
        self.pending_quit_slot: str = str(self.state.get("pending_quit_slot") or "")
        self.last_auto_check = 0.0
        self._stop = False

    # -- 生命周期 ----------------------------------------------------------

    def setup(self) -> None:
        APP_SUPPORT.mkdir(parents=True, exist_ok=True)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        if not self.access_ok:
            log.error("无法读取 MAA 容器：%s (%s)", self.access_path, self.access_error)
            log.error(access_help_text())
        now = datetime.now()
        if not STATE_PATH.exists():
            self.state = default_state(now)
            self.installed_at = now
            self._mark_past_slots_ignored(now)
            self.tail.seek_end()
            save_state(STATE_PATH, self.state, None, self.tail)
            log.info("首次启动，已跳过今天此前的时段")
            return

        active_data = self.state.get("active")
        if active_data:
            try:
                self.active = Run.from_dict(active_data)
                self.tail.offset = int(self.state.get("gui_offset") or 0)
                ident = self.state.get("gui_ident")
                if isinstance(ident, list) and len(ident) == 2:
                    self.tail.ident = (int(ident[0]), int(ident[1]))
                log.info("恢复未完成的运行：%s", self.active.slot_key)
            except Exception as exc:  # noqa: BLE001
                log.warning("恢复 active 失败：%s", exc)
                self.active = None

        if self.active is None:
            self.active = self._bootstrap_active(now)
        if self.active is None:
            self.tail.seek_end()
        else:
            log.info("从日志接管运行中的任务：%s", self.active.slot_key)
        save_state(STATE_PATH, self.state, self.active, self.tail)

    def _mark_past_slots_ignored(self, now: datetime) -> None:
        handled = self.state.setdefault("handled_slots", {})
        for hour, minute in self.schedule:
            slot = datetime(now.year, now.month, now.day, hour, minute)
            if slot + timedelta(minutes=self._grace()) < now:
                handled[slot.strftime("%Y-%m-%dT%H:%M")] = "ignored"

    def _bootstrap_active(self, now: datetime) -> Optional[Run]:
        lines = read_all_lines(expand_path(self.cfg["maa"]["gui_log"]))
        events = [event for event in (parse_gui_line(line) for line in lines) if event]
        if not events:
            return None

        start_index = None
        for index, event in enumerate(events):
            if event.message.startswith("开始任务：") or event.message.startswith("正在连接"):
                start_index = index
        if start_index is None:
            return None

        for event in events[start_index + 1 :]:
            if (
                event.message.startswith("任务已全部完成")
                or event.message.strip().startswith("已停止")
                or "StartTasksFailed:" in event.message
            ):
                return None

        first = events[start_index].ts
        if first < self.installed_at:
            return None
        slot_info = self._slot_for_event(first)
        if not slot_info:
            return None
        slot_time, slot_key, late = slot_info
        run = Run.create(
            slot_key,
            slot_time,
            self.expected,
            self._optional_labels(),
            late=late,
        )
        run.start_time = first
        for event in events[start_index:]:
            run.handle_event(event, self.reason_finder)
        if run.end_pending_since:
            return None
        if now - (run.last_activity or first) > timedelta(minutes=self._hard_timeout()):
            return None
        return run

    def stop(self, *_args: Any) -> None:
        self._stop = True

    def run_forever(self) -> None:
        self.setup()
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        log.info(
            "开始监控：定时 %s，配置文件 %s，dry_run=%s，auto_start=%s，auto_quit=%s%s",
            ", ".join("%02d:%02d" % item for item in self.schedule),
            self.config_path,
            self.dry_run,
            bool(self.cfg["policy"].get("auto_start_maa", False)),
            bool(self.cfg["policy"].get("auto_quit_maa", False)),
            "" if self.feishu_ready else "（未配置消息机器人 Webhook，仅写日志）",
        )
        while not self._stop:
            try:
                self.tick(datetime.now())
            except Exception:  # noqa: BLE001
                log.exception("主循环异常")
            time.sleep(max(1, int(self.cfg["policy"].get("poll_seconds", 2))))
        log.info("收到退出信号，已停止")
        save_state(STATE_PATH, self.state, self.active, self.tail)

    # -- 策略参数 ----------------------------------------------------------

    def _grace(self) -> int:
        return int(self.cfg["policy"].get("grace_minutes", 30))

    def _idle_timeout(self) -> int:
        return int(self.cfg["policy"].get("idle_timeout_minutes", 15))

    def _hard_timeout(self) -> int:
        return int(self.cfg["policy"].get("hard_timeout_minutes", 180))

    def _optional_labels(self) -> List[str]:
        labels = self.cfg["policy"].get("optional_tasks") or []
        return [str(item) for item in labels]

    def _auto_start_lead(self) -> int:
        return int(self.cfg["policy"].get("auto_start_lead_minutes", 5))

    def _auto_quit_delay(self) -> int:
        return int(self.cfg["policy"].get("auto_quit_delay_seconds", 60))

    def _auto_controls_enabled(self) -> bool:
        # dry-run 阶段不实际启停 MAA，避免影响现有监控。
        return not self.dry_run

    # -- 定时与 slot -------------------------------------------------------

    def _refresh_schedule(self, now: datetime) -> None:
        if self.last_schedule_refresh and now - self.last_schedule_refresh < timedelta(
            minutes=10
        ):
            return
        self.last_schedule_refresh = now
        try:
            self.schedule = read_schedule(self.cfg)
        except Exception as exc:  # noqa: BLE001
            log.warning("刷新定时失败：%s", exc)

    def _slot_for_event(
        self, ts: datetime
    ) -> Optional[Tuple[datetime, str, bool]]:
        for hour, minute in self.schedule:
            slot = datetime(ts.year, ts.month, ts.day, hour, minute)
            key = slot.strftime("%Y-%m-%dT%H:%M")
            handled = self.state.setdefault("handled_slots", {}).get(key)
            if handled in ("sent", "ignored", "pending", "dry_run"):
                continue
            if slot - timedelta(minutes=5) <= ts <= slot + timedelta(
                minutes=self._hard_timeout()
            ):
                late = ts > slot + timedelta(minutes=self._grace())
                return slot, key, late
        return None

    # -- 事件处理 ----------------------------------------------------------

    def tick(self, now: datetime) -> None:
        self._refresh_schedule(now)
        self._check_auto_start(now)
        if not self.access_ok:
            if now.timestamp() - self.last_access_check >= 60:
                self.last_access_check = now.timestamp()
                self.access_ok, self.access_path, self.access_error = check_maa_access(self.cfg)
                if self.access_ok:
                    log.info("MAA 容器读取权限已恢复")
                    self.expected = load_expected_tasks(self.cfg)
                else:
                    log.warning("仍无法读取 MAA 容器：%s (%s)", self.access_path, self.access_error)
            self._save_if_due(now)
            return

        lines = self.tail.read_new()
        for line in lines:
            event = parse_gui_line(line)
            if event:
                self._dispatch(event, now)

        self._check_active(now)
        self._check_pending_quit(now)
        self._check_missed(now)
        self._flush_pending(now)
        self._save_if_due(now)

    def _dispatch(self, event: GuiEvent, now: datetime) -> None:
        if self.active is None:
            if not is_run_start_message(event.message):
                return
            slot_info = self._slot_for_event(event.ts)
            if not slot_info:
                return
            slot_time, slot_key, late = slot_info
            self.active = Run.create(
                slot_key,
                slot_time,
                self.expected,
                self._optional_labels(),
                late=late,
            )
            self.state.setdefault("handled_slots", {})[slot_key] = "running"
            log.info("检测到运行开始：%s", slot_key)

        self.active.handle_event(event, self.reason_finder)

    def _heartbeat(self) -> Optional[datetime]:
        latest: Optional[datetime] = None
        for key in ("gui_log", "asst_log", "asst_bak_log"):
            try:
                path = expand_path(self.cfg["maa"][key])
                mtime = datetime.fromtimestamp(path.stat().st_mtime)
                if latest is None or mtime > latest:
                    latest = mtime
            except OSError:
                continue
        return latest

    def _check_active(self, now: datetime) -> None:
        run = self.active
        if run is None:
            return

        heartbeat = self._heartbeat()
        if heartbeat and (run.last_activity is None or heartbeat > run.last_activity):
            run.last_activity = heartbeat

        if run.end_pending_since:
            if (now - run.end_pending_since).total_seconds() >= 2:
                self._finalize(run, now)
            return

        if run.start_time is None:
            run.start_time = now

        idle = (now - (run.last_activity or run.start_time or now)).total_seconds()
        if idle > self._idle_timeout() * 60:
            if run.idle_suspect_since is None:
                run.idle_suspect_since = now
                log.warning("运行 %s 超过 %d 分钟没有日志", run.slot_key, self._idle_timeout())
            elif (now - run.idle_suspect_since).total_seconds() >= 60:
                run.timed_out = True
                run.end_time = run.last_activity or now
                log.warning("运行 %s 判定为卡死", run.slot_key)
                self._finalize(run, now)
                return
        else:
            run.idle_suspect_since = None

        elapsed = (now - (run.start_time or now)).total_seconds()
        if elapsed > self._hard_timeout() * 60:
            run.timed_out = True
            run.end_time = run.last_activity or now
            log.warning("运行 %s 超过硬超时", run.slot_key)
            self._finalize(run, now)
            return

        if (
            self.last_process_check is None
            or (now - self.last_process_check).total_seconds() >= 30
        ):
            self.last_process_check = now
            self.maa_running = self._maa_running()
            if (
                self.maa_running is False
                and (now - (run.last_activity or now)).total_seconds() > 60
            ):
                run.interrupted_reason = "MAA 进程已退出"
                run.end_time = run.last_activity or now
                log.warning("运行 %s 期间 MAA 进程退出", run.slot_key)
                self._finalize(run, now)

    # -- 自动启停 MAA ------------------------------------------------------

    def _check_auto_start(self, now: datetime) -> None:
        if not self._auto_controls_enabled():
            return
        if not bool(self.cfg["policy"].get("auto_start_maa", False)):
            return
        if now.timestamp() - self.last_auto_check < 10:
            return
        self.last_auto_check = now.timestamp()

        due = auto_start_due(now, self.schedule, self._auto_start_lead())
        if not due:
            return
        _, key = due
        auto_started = self.state.setdefault("auto_started", {})
        if key in auto_started:
            last_attempt = parse_iso(auto_started.get(key))
            if last_attempt and now - last_attempt < timedelta(seconds=60):
                return
        if self._maa_running() is False:
            log.info("MAA 未运行，自动启动（%s）", key)
            if self._open_maa():
                auto_started[key] = iso(now)
                # 只保留最近 30 条
                for old_key in sorted(auto_started.keys())[:-30]:
                    auto_started.pop(old_key, None)

    def _open_maa(self) -> bool:
        app_path = str(self.cfg["maa"].get("app_path") or MAA_APP_PATH)
        try:
            proc = subprocess.run(
                ["/usr/bin/open", "-a", app_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
            )
            if proc.returncode == 0:
                log.info("已请求启动 MAA：%s", app_path)
                return True
            log.warning("启动 MAA 失败，open 返回 %s", proc.returncode)
        except Exception as exc:  # noqa: BLE001
            log.warning("启动 MAA 异常：%s", exc)
        return False

    def _check_pending_quit(self, now: datetime) -> None:
        if self.pending_quit_at is None:
            return
        if not self._auto_controls_enabled() or not bool(
            self.cfg["policy"].get("auto_quit_maa", False)
        ):
            self._clear_pending_quit()
            return
        if self.active is not None:
            log.info("检测到新的运行，取消自动退出 MAA")
            self._clear_pending_quit()
            return
        if now < self.pending_quit_at:
            return
        if self._maa_running() is False:
            self._clear_pending_quit()
            return

        log.info("运行已结束，自动退出 MAA（%s）", self.pending_quit_slot)
        self._quit_maa()
        self._clear_pending_quit()

    def _quit_maa(self) -> None:
        app_path = str(self.cfg["maa"].get("app_path") or MAA_APP_PATH)
        script = 'tell application id "com.hguandl.MeoAsstMac" to quit'
        try:
            proc = subprocess.run(
                ["/usr/bin/osascript", "-e", script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=20,
                text=True,
            )
            if proc.returncode == 0:
                log.info("已请求 MAA 退出")
                self.maa_running = False
                self.last_process_check = None
                return
            log.warning(
                "AppleScript 退出 MAA 失败：%s",
                (proc.stderr or "").strip()[:200],
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("AppleScript 退出 MAA 异常：%s", exc)

        # 兜底：SIGTERM。只在 AppleScript 失败时使用。
        try:
            subprocess.run(
                ["/usr/bin/pkill", "-TERM", "-f", app_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            log.info("已发送 SIGTERM 给 MAA")
            self.maa_running = False
            self.last_process_check = None
        except Exception as exc:  # noqa: BLE001
            log.warning("发送 SIGTERM 给 MAA 失败：%s", exc)

    def _clear_pending_quit(self) -> None:
        self.pending_quit_at = None
        self.pending_quit_slot = ""
        self.state.pop("pending_quit_at", None)
        self.state.pop("pending_quit_slot", None)

    def _maa_running(self) -> Optional[bool]:
        app_path = str(self.cfg["maa"].get("app_path") or MAA_APP_PATH)
        try:
            proc = subprocess.run(
                ["/usr/bin/pgrep", "-f", app_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            return proc.returncode == 0
        except Exception:  # noqa: BLE001
            return None

    # -- 结束与推送 --------------------------------------------------------

    def _finalize(self, run: Run, now: datetime) -> None:
        if run.end_time is None:
            run.end_time = run.last_activity or now
        run.status = run.classify()
        report = build_report(run, self.cfg)
        key = run.slot_key

        if not run.failure_screenshot and run.status in ("partial", "failed"):
            candidates = list(run.failure_times)
            if run.end_time:
                candidates.append(run.end_time)
            for candidate in candidates:
                screenshot = find_failure_screenshot(self.cfg, candidate)
                if screenshot:
                    run.failure_screenshot = screenshot
                    break
        if run.failure_screenshot:
            report["failure_screenshot"] = run.failure_screenshot

        log.info(
            "运行结束：%s status=%s %d/%d 完成",
            key,
            run.status,
            report["counts"]["completed"],
            report["counts"]["total"],
        )

        if self._should_notify(report):
            payload = build_feishu_payload(report, self.cfg)
            if self.dry_run:
                log.info("dry-run：%s", json.dumps(payload, ensure_ascii=False))
                self.state.setdefault("handled_slots", {})[key] = "dry_run"
            else:
                try:
                    send_feishu(payload, self.cfg)
                    self.state.setdefault("handled_slots", {})[key] = "sent"
                except Exception as exc:  # noqa: BLE001
                    log.warning("推送失败，进入待发送队列：%s", exc)
                    self._enqueue_pending(key, payload)
                    self.state.setdefault("handled_slots", {})[key] = "pending"
        else:
            self.state.setdefault("handled_slots", {})[key] = "ignored"

        self.state["last_run"] = report
        append_history(report)
        self.active = None

        if self._auto_controls_enabled() and bool(
            self.cfg["policy"].get("auto_quit_maa", False)
        ):
            delay = max(0, self._auto_quit_delay())
            self.pending_quit_at = datetime.now() + timedelta(seconds=delay)
            self.pending_quit_slot = key
            self.state["pending_quit_at"] = iso(self.pending_quit_at)
            self.state["pending_quit_slot"] = key
            log.info("将在 %ss 后自动退出 MAA", delay)
        else:
            self._clear_pending_quit()

        save_state(STATE_PATH, self.state, None, self.tail)

    def _should_notify(self, report: Dict[str, Any]) -> bool:
        notify = get_notify(self.cfg)
        if report["status"] == "success":
            return bool(notify.get("notify_on_success", True))
        if report["status"] == "partial":
            return bool(notify.get("notify_on_partial", True))
        if report["status"] == "missed":
            return bool(notify.get("notify_on_missed", True))
        return True

    def _enqueue_pending(self, key: str, payload: Dict[str, Any]) -> None:
        pending = self.state.setdefault("pending", [])
        pending.append(
            {
                "slot_key": key,
                "created": iso(datetime.now()),
                "payload": payload,
            }
        )
        del pending[:-20]

    def _flush_pending(self, now: datetime) -> None:
        if self.dry_run:
            return
        if (now - datetime.fromtimestamp(self.last_pending_flush)).total_seconds() < 60:
            return
        self.last_pending_flush = now.timestamp()
        pending = self.state.get("pending") or []
        if not pending:
            return
        remaining = []
        for item in pending:
            try:
                send_feishu(item["payload"], self.cfg)
                key = item.get("slot_key", "")
                if key:
                    self.state.setdefault("handled_slots", {})[key] = "sent"
                log.info("补发待发送消息成功：%s", key)
            except Exception as exc:  # noqa: BLE001
                log.warning("补发失败，保留在队列：%s", exc)
                remaining.append(item)
        self.state["pending"] = remaining

    # -- 漏跑检测 ----------------------------------------------------------

    def _check_missed(self, now: datetime) -> None:
        handled = self.state.setdefault("handled_slots", {})
        for hour, minute in self.schedule:
            slot = datetime(now.year, now.month, now.day, hour, minute)
            key = slot.strftime("%Y-%m-%dT%H:%M")
            if key in handled:
                continue
            if self.active and self.active.slot_key == key:
                continue
            if now < slot + timedelta(minutes=self._grace()):
                continue
            if slot < self.installed_at:
                handled[key] = "ignored"
                continue

            reason = (
                "MAA 进程未运行"
                if self._maa_running() is False
                else "无日志活动（可能卡在连接，或电脑处于休眠）"
            )
            report = {
                "slot_key": key,
                "slot": slot.strftime("%H:%M"),
                "date": slot.strftime("%Y-%m-%d"),
                "status": "missed",
                "late": False,
                "start": slot.strftime("%Y-%m-%d %H:%M:%S"),
                "end": (slot + timedelta(minutes=self._grace())).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "start_clock": slot.strftime("%H:%M:%S"),
                "end_clock": (slot + timedelta(minutes=self._grace())).strftime(
                    "%H:%M:%S"
                ),
                "duration_seconds": self._grace() * 60,
                "tasks": [],
                "counts": {
                    "total": 0,
                    "completed": 0,
                    "failed": 0,
                    "not_run": 0,
                    "optional_failed": 0,
                },
                "failed_displays": [],
                "not_run_displays": [],
                "retried_displays": [],
                "start_failed_reason": "",
                "interrupted_reason": "",
                "timed_out": False,
                "sanity": "",
                "failure_screenshot": "",
                "log_path": str(expand_path(self.cfg["maa"]["gui_log"])),
                "missed_body": reason,
            }
            log.warning("未检测到运行：%s（%s）", key, reason)
            if self._should_notify(report):
                payload = build_feishu_payload(report, self.cfg)
                if self.dry_run:
                    log.info("dry-run missed：%s", json.dumps(payload, ensure_ascii=False))
                    handled[key] = "dry_run"
                else:
                    try:
                        send_feishu(payload, self.cfg)
                        handled[key] = "sent"
                    except Exception as exc:  # noqa: BLE001
                        log.warning("漏跑推送失败：%s", exc)
                        self._enqueue_pending(key, payload)
                        handled[key] = "pending"
            else:
                handled[key] = "ignored"
            append_history(report)
            self.state["last_run"] = report

    # -- 保存 --------------------------------------------------------------

    def _save_if_due(self, now: datetime) -> None:
        if time.time() - self.last_save < 10:
            return
        self.last_save = time.time()
        save_state(STATE_PATH, self.state, self.active, self.tail)


# ---------------------------------------------------------------------------
# 离线回放 / 状态 / 测试
# ---------------------------------------------------------------------------


def parse_datetime_arg(value: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise argparse.ArgumentTypeError("时间格式应为 YYYY-MM-DD HH:MM")


def replay_run(
    cfg: Dict[str, Any],
    start: datetime,
    end: datetime,
    slot_label: str,
) -> Optional[Dict[str, Any]]:
    expected = load_expected_tasks(cfg)
    run = Run.create(
        slot_key=start.strftime("%Y-%m-%dT%H:%M"),
        slot_time=start,
        expected=expected,
        optional_labels=[str(x) for x in cfg["policy"].get("optional_tasks") or []],
    )
    run.start_time = start
    reason_finder = ReasonFinder(cfg)
    events = []
    for line in read_all_lines(expand_path(cfg["maa"]["gui_log"])):
        event = parse_gui_line(line)
        if event and start <= event.ts <= end:
            events.append(event)
    if not events:
        return None
    for event in events:
        run.handle_event(event, reason_finder)
    if not run.end_pending_since:
        run.timed_out = True
    if run.end_time is None:
        run.end_time = run.last_activity or end
    run.status = run.classify()
    report = build_report(run, cfg)
    report["slot"] = slot_label
    return report


def cmd_replay(args: argparse.Namespace, cfg: Dict[str, Any]) -> int:
    if args.from_time:
        start = parse_datetime_arg(args.from_time)
    else:
        day = datetime.strptime(args.date, "%Y-%m-%d")
        hour, minute = [int(x) for x in args.slot.split(":", 1)]
        start = datetime(day.year, day.month, day.day, hour, minute)
    if args.to_time:
        end = parse_datetime_arg(args.to_time)
    else:
        end = start + timedelta(minutes=int(cfg["policy"].get("hard_timeout_minutes", 180)))
        next_slot = None
        for hour, minute in read_schedule(cfg):
            candidate = datetime(start.year, start.month, start.day, hour, minute)
            if candidate > start:
                next_slot = candidate
                break
        if next_slot and next_slot < end:
            end = next_slot

    report = replay_run(cfg, start, end, start.strftime("%H:%M"))
    if report is None:
        print("指定时间范围内没有找到 MAA 日志。")
        return 1
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(report_to_text(report))
        if args.verbose:
            print("\n--- JSON ---")
            print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.send:
        payload = build_feishu_payload(report, cfg)
        send_feishu(payload, cfg, dry_run=args.dry_run)
        print("已发送飞书卡片。")
    return 0


def cmd_test(args: argparse.Namespace, cfg: Dict[str, Any]) -> int:
    now = datetime.now()
    run = Run.create(
        now.strftime("%Y-%m-%dT%H:%M"),
        now,
        [
            ExpectedTask("startup", "开始唤醒", "开始唤醒"),
            ExpectedTask("recruit", "自动公招", "自动公招"),
            ExpectedTask("infrast", "基建换班", "基建换班"),
            ExpectedTask("fight", "刷理智", "刷理智（1-7）", "1-7"),
            ExpectedTask("mall", "收取信用及购物", "收取信用及购物"),
            ExpectedTask("award", "领取奖励", "领取奖励"),
            ExpectedTask("switchTheme", "更换主题", "更换主题"),
        ],
    )
    run.start_time = now - timedelta(minutes=38, seconds=24)
    run.end_time = now
    run.all_done = True
    for index, task in enumerate(run.tasks):
        task.begin(run.start_time + timedelta(minutes=index))
        task.complete(run.start_time + timedelta(minutes=index + 1))
    report = build_report(run, cfg)
    report["sanity"] = "101 → 4"
    payload = build_feishu_payload(report, cfg)
    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    send_feishu(payload, cfg)
    print("测试消息已发送。")
    return 0


def _pid_for_maa(cfg: Dict[str, Any]) -> Optional[int]:
    app_path = str(cfg["maa"].get("app_path") or MAA_APP_PATH)
    try:
        proc = subprocess.run(
            ["/usr/bin/pgrep", "-f", app_path],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode != 0:
            return None
        return int(proc.stdout.split()[0])
    except Exception:  # noqa: BLE001
        return None


def cmd_doctor(args: argparse.Namespace, cfg: Dict[str, Any]) -> int:
    if getattr(args, "launchd", False):
        fd, out_path = tempfile.mkstemp(prefix="maa-doctor-", suffix=".out")
        os.close(fd)
        label = "com.jarvan.maa-notify.doctor.%d" % os.getpid()
        try:
            subprocess.run(
                [
                    "/bin/launchctl",
                    "submit",
                    "-l",
                    label,
                    "-o",
                    out_path,
                    "--",
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--config",
                    str(Path(args.config).resolve()),
                    "doctor",
                ],
                check=False,
            )
            time.sleep(3)
            text = Path(out_path).read_text(encoding="utf-8", errors="replace")
            print(text)
            launchd_ok = "MAA 日志权限：OK" in text
        finally:
            subprocess.run(
                ["/bin/launchctl", "remove", label],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            try:
                os.unlink(out_path)
            except OSError:
                pass
        return 0 if launchd_ok else 1

    ok, path, error = check_maa_access(cfg)
    print("Python：%s" % sys.executable)
    print("Python.app：%s" % python_app_path())
    print("MAA 容器：%s" % MAA_CONTAINER)
    if ok:
        print("MAA 日志权限：OK")
    else:
        print("MAA 日志权限：DENIED")
        print("  路径：%s" % path)
        print("  错误：%s" % error)
        print()
        print(access_help_text())
        if args.open_settings:
            subprocess.run(
                [
                    "/usr/bin/open",
                    "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles",
                ],
                check=False,
            )
    if feishu_configured(cfg):
        print("飞书 Webhook：已配置")
    else:
        print("飞书 Webhook：未配置（当前只会写日志，不会发消息）")
    print("定时：%s" % ", ".join("%02d:%02d" % item for item in read_schedule(cfg)))
    print("状态文件：%s" % STATE_PATH)
    return 0 if ok else 1


def cmd_status(args: argparse.Namespace, cfg: Dict[str, Any]) -> int:
    state = load_state(STATE_PATH, datetime.now())
    pid = _pid_for_maa(cfg)
    schedule = read_schedule(cfg)
    active = Run.from_dict(state["active"]) if state.get("active") else None
    last = state.get("last_run")
    pending = state.get("pending") or []

    if args.json:
        print(
            json.dumps(
                {
                    "maa_pid": pid,
                    "maa_running": pid is not None,
                    "schedule": ["%02d:%02d" % item for item in schedule],
                    "active": active.to_dict() if active else None,
                    "last_run": last,
                    "pending": len(pending),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    access_ok, access_path, _ = check_maa_access(cfg)
    print("MAA 进程：%s" % ("运行中 (PID %s)" % pid if pid else "未运行"))
    print("MAA 日志权限：%s" % ("OK" if access_ok else "DENIED (%s)" % access_path))
    print("MAA 定时：%s" % ", ".join("%02d:%02d" % item for item in schedule))
    auto_start = bool(cfg["policy"].get("auto_start_maa", False))
    auto_quit = bool(cfg["policy"].get("auto_quit_maa", False))
    print(
        "自动启停 MAA：%s（提前 %s 分钟启动，结束后 %s 秒退出）"
        % (
            "开启" if auto_start and auto_quit else "关闭",
            cfg["policy"].get("auto_start_lead_minutes", 5),
            cfg["policy"].get("auto_quit_delay_seconds", 60),
        )
    )
    if active:
        counts = active.counts()
        print(
            "当前运行：%s，已开始 %s，任务 %d/%d"
            % (
                active.slot_key,
                fmt_duration((datetime.now() - (active.start_time or datetime.now())).total_seconds()),
                counts["completed"],
                counts["total"],
            )
        )
    else:
        print("当前运行：空闲")
    if last:
        print(
            "上次结果：%s %s，%d/%d 完成"
            % (
                last.get("date", ""),
                last.get("slot", ""),
                last.get("counts", {}).get("completed", 0),
                last.get("counts", {}).get("total", 0),
            )
        )
        for item in last.get("failed_displays", []):
            print("  失败：%s" % item)
    else:
        print("上次结果：暂无")
    print("待发送：%d" % len(pending))
    print("状态文件：%s" % STATE_PATH)
    print("配置文件：%s" % args.config)
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MAA 运行状态监控（只读日志，飞书推送）"
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="配置文件路径（默认与脚本同目录的 config.json）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只打印，不实际发送飞书")
    parser.add_argument("--verbose", action="store_true", help="输出 debug 日志")
    parser.add_argument(
        "--state-dir",
        default="",
        help="状态/历史/日志目录（默认 ~/Library/Application Support/MaaNotify）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--dry-run",
        action="store_true",
        default=argparse.SUPPRESS,
        help="只打印，不实际发送飞书",
    )

    sub.add_parser("watch", parents=[common], help="常驻监控（launchd 使用）")
    status = sub.add_parser("status", parents=[common], help="查看当前状态")
    status.add_argument("--json", action="store_true")

    replay = sub.add_parser("replay", parents=[common], help="回放历史日志")
    replay.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    replay.add_argument("--slot", default="07:02")
    replay.add_argument("--from", dest="from_time", default="")
    replay.add_argument("--to", dest="to_time", default="")
    replay.add_argument("--json", action="store_true")
    replay.add_argument("--send", action="store_true")

    report = sub.add_parser("report", parents=[common], help="回放并（可选）重发某场结果")
    report.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    report.add_argument("--slot", default="07:02")
    report.add_argument("--from", dest="from_time", default="")
    report.add_argument("--to", dest="to_time", default="")
    report.add_argument("--json", action="store_true")
    report.add_argument("--send", action="store_true")

    sub.add_parser("schedule", parents=[common], help="输出当前生效的定时（JSON）")

    sync_schedule = sub.add_parser(
        "sync-maa-schedule",
        parents=[common],
        help="把定时数组写入 MAA（会退出/重启 MAA）",
    )
    sync_schedule.add_argument(
        "--times",
        required=True,
        help='逗号分隔的定时，例如 "07:02,19:01"',
    )
    sync_schedule.add_argument(
        "--no-restart",
        action="store_true",
        help="写入后不重新启动 MAA",
    )

    sub.add_parser("test", parents=[common], help="发送一张测试卡片")

    doctor = sub.add_parser("doctor", parents=[common], help="检查日志权限和配置")
    doctor.add_argument("--open", dest="open_settings", action="store_true", help="打开完全磁盘访问设置")
    doctor.add_argument("--launchd", action="store_true", help="在 launchd 上下文中实测一次权限")
    return parser


def cmd_schedule(args: argparse.Namespace, cfg: Dict[str, Any]) -> int:
    slots = read_schedule(cfg)
    print(json.dumps(["%02d:%02d" % item for item in slots], ensure_ascii=False))
    return 0


def cmd_sync_maa_schedule(args: argparse.Namespace, cfg: Dict[str, Any]) -> int:
    values = [item.strip() for item in str(args.times).split(",") if item.strip()]
    slots = parse_schedule_times(values)
    if not slots:
        print("定时格式不正确，应为 HH:MM,HH:MM", file=sys.stderr)
        return 2
    try:
        items = write_maa_schedule(cfg, slots, restart=not args.no_restart)
    except Exception as exc:  # noqa: BLE001
        print("写入 MAA 定时失败：%s" % exc, file=sys.stderr)
        return 1
    print(
        json.dumps(
            ["%02d:%02d" % (item["hour"], item["minute"]) for item in items],
            ensure_ascii=False,
        )
    )
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.state_dir:
        global APP_SUPPORT, STATE_PATH, HISTORY_PATH, LOG_DIR, LOG_PATH
        APP_SUPPORT = expand_path(args.state_dir)
        STATE_PATH = APP_SUPPORT / "state.json"
        HISTORY_PATH = APP_SUPPORT / "runs.jsonl"
        LOG_DIR = APP_SUPPORT / "logs"
        LOG_PATH = LOG_DIR / "maa-notify.log"
    setup_logging(args.verbose)
    config_path = expand_path(args.config)
    try:
        cfg = load_config(config_path)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print("配置文件不是合法 JSON：%s" % exc, file=sys.stderr)
        return 2

    try:
        if args.command == "watch":
            watcher = Watcher(cfg, config_path, dry_run=args.dry_run)
            watcher.run_forever()
            return 0
        if args.command == "status":
            return cmd_status(args, cfg)
        if args.command in ("replay", "report"):
            return cmd_replay(args, cfg)
        if args.command == "schedule":
            return cmd_schedule(args, cfg)
        if args.command == "sync-maa-schedule":
            return cmd_sync_maa_schedule(args, cfg)
        if args.command == "test":
            return cmd_test(args, cfg)
        if args.command == "doctor":
            return cmd_doctor(args, cfg)
    except FeishuError as exc:
        print("消息发送失败：%s" % exc, file=sys.stderr)
        return 1
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
