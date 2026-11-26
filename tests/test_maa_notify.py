#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MAA 状态监控的回归测试。

只依赖标准库：
    PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest discover -s tests -v
"""

import json
import plistlib
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import maa_notify as mn  # noqa: E402


def dt(text: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(text)


def ev(text: str, message: str) -> mn.GuiEvent:
    return mn.GuiEvent(ts=dt(text), level="trace", message=message)


def expected_fight_twice():
    return [
        mn.ExpectedTask("startup", "开始唤醒", "开始唤醒"),
        mn.ExpectedTask("recruit", "自动公招", "自动公招"),
        mn.ExpectedTask("fight", "刷理智", "刷理智（PR-A-1）", "PR-A-1"),
        mn.ExpectedTask("fight", "刷理智", "刷理智（1-7）", "1-7"),
        mn.ExpectedTask("mall", "收取信用及购物", "收取信用及购物"),
        mn.ExpectedTask("award", "领取奖励", "领取奖励"),
        mn.ExpectedTask("switchTheme", "更换主题", "更换主题（齐聚）", "齐聚"),
    ]


class TestGuiParser(unittest.TestCase):
    def test_parse_normal(self):
        event = mn.parse_gui_line(
            "[2026-11-23 19:01:03][trace]开始任务：开始唤醒"
        )
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.ts, dt("2026-11-23 19:01:03"))
        self.assertEqual(event.message, "开始任务：开始唤醒")

    def test_parse_optional_timezone(self):
        event = mn.parse_gui_line(
            "[2026-04-15 07:08:27 +0000][error]战斗出错"
        )
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.message, "战斗出错")

    def test_event_label_strips_sanity(self):
        self.assertEqual(mn.event_label("完成任务：刷理智 理智：1/208", "完成任务："), "刷理智")


class TestRunStartSignal(unittest.TestCase):
    def test_resource_update_is_not_a_run_start(self):
        self.assertFalse(
            mn.is_run_start_message("外部资源版本：雪山降临1101 更新时间：2026-11-24")
        )
        self.assertFalse(mn.is_run_start_message("资源已是最新版本"))
        self.assertFalse(mn.is_run_start_message("正在连接……"))
        self.assertTrue(mn.is_run_start_message("开始任务：开始唤醒"))
        self.assertTrue(mn.is_run_start_message("StartTasksFailed: connectFailed"))
        self.assertTrue(mn.is_run_start_message("任务已全部完成！") is False)


class TestTaskMapping(unittest.TestCase):
    def test_two_fights_are_distinct_instances(self):
        run = mn.Run.create(
            "2026-11-23T07:02", dt("2026-11-23 07:02"), expected_fight_twice()
        )
        for event in [
            ev("2026-11-23 07:02:10", "开始任务：开始唤醒"),
            ev("2026-11-23 07:02:30", "完成任务：开始唤醒"),
            ev("2026-11-23 07:02:35", "开始任务：自动公招"),
            ev("2026-11-23 07:03:00", "完成任务：自动公招"),
            ev("2026-11-23 07:10:41", "开始任务：刷理智"),
            ev("2026-11-23 07:11:01", "任务出错：刷理智"),
            ev("2026-11-23 07:11:02", "开始任务：刷理智"),
            ev("2026-11-23 07:38:40", "完成任务：刷理智 理智：4/208"),
            ev("2026-11-23 07:38:41", "开始任务：收取信用及购物"),
            ev("2026-11-23 07:39:00", "完成任务：收取信用及购物"),
            ev("2026-11-23 07:39:01", "开始任务：领取奖励"),
            ev("2026-11-23 07:39:10", "完成任务：领取奖励"),
            ev("2026-11-23 07:39:11", "开始任务：更换主题"),
            ev("2026-11-23 07:39:30", "完成任务：更换主题"),
            ev("2026-11-23 07:39:31", "任务已全部完成！"),
        ]:
            run.handle_event(event)

        fight_pr, fight_17 = [item for item in run.tasks if item.key == "fight"]
        self.assertEqual(fight_pr.display, "刷理智（PR-A-1）")
        self.assertEqual(fight_pr.state, "error")
        self.assertEqual(fight_17.display, "刷理智（1-7）")
        self.assertEqual(fight_17.state, "completed")
        self.assertEqual(run.classify(), "partial")
        counts = run.counts()
        self.assertEqual(counts["total"], 7)
        self.assertEqual(counts["completed"], 6)
        self.assertEqual(counts["failed"], 1)

    def test_single_fight_retry_counts_as_recovered(self):
        expected = [mn.ExpectedTask("fight", "刷理智", "刷理智（1-7）", "1-7")]
        run = mn.Run.create("2026-11-23T07:02", dt("2026-11-23 07:02"), expected)
        for event in [
            ev("2026-11-23 07:10:41", "开始任务：刷理智"),
            ev("2026-11-23 07:11:01", "任务出错：刷理智"),
            ev("2026-11-23 07:11:02", "开始任务：刷理智"),
            ev("2026-11-23 07:38:40", "完成任务：刷理智"),
            ev("2026-11-23 07:38:41", "任务已全部完成！"),
        ]:
            run.handle_event(event)
        task = run.tasks[0]
        self.assertEqual(task.state, "completed")
        self.assertTrue(task.had_error)
        self.assertEqual(len(task.attempts), 2)
        self.assertEqual(run.classify(), "success")

    def test_all_tasks_error_is_failed_not_success(self):
        run = mn.Run.create(
            "2026-07-22T11:53", dt("2026-07-22 11:53"), expected_fight_twice()
        )
        for name in ("开始唤醒", "自动公招", "基建换班", "刷理智", "收取信用及购物", "领取奖励"):
            run.handle_event(ev("2026-07-22 12:01:00", "开始任务：%s" % name))
            run.handle_event(ev("2026-07-22 12:01:10", "任务出错：%s" % name))
        run.handle_event(ev("2026-07-22 12:02:23", "任务已全部完成！"))
        self.assertEqual(run.classify(), "failed")
        self.assertEqual(run.counts()["completed"], 0)

    def test_subtask_completion_is_ignored(self):
        expected = [mn.ExpectedTask("mall", "收取信用及购物", "收取信用及购物")]
        run = mn.Run.create("2026-11-23T07:02", dt("2026-11-23 07:02"), expected)
        run.handle_event(ev("2026-11-23 07:38:41", "开始任务：收取信用及购物"))
        run.handle_event(ev("2026-11-23 07:39:03", "完成任务：访问好友"))
        self.assertEqual(run.tasks[0].state, "running")
        run.handle_event(ev("2026-11-23 07:39:52", "完成任务：收取信用及购物"))
        self.assertEqual(run.tasks[0].state, "completed")

    def test_stopped_after_all_done_is_still_success(self):
        run = mn.Run.create(
            "2026-08-03T19:39", dt("2026-08-03 19:39"), expected_fight_twice()
        )
        for task in run.tasks:
            task.begin(dt("2026-08-03 20:00"))
            task.complete(dt("2026-08-03 20:01"))
        run.handle_event(ev("2026-08-03 20:05:54", "任务已全部完成！"))
        run.handle_event(ev("2026-08-03 20:05:54", "已停止"))
        self.assertFalse(run.stopped)
        self.assertEqual(run.classify(), "success")

    def test_real_all_done_then_stopped_log(self):
        run = mn.Run.create(
            "2026-08-03T19:39", dt("2026-08-03 19:39"), expected_fight_twice()
        )
        for task in run.tasks:
            task.begin(dt("2026-08-03 19:40"))
            task.complete(dt("2026-08-03 20:00"))
        run.handle_event(ev("2026-08-03 20:05:53", "开始任务：CloseDown"))
        run.handle_event(ev("2026-08-03 20:05:54", "完成任务：CloseDown"))
        run.handle_event(ev("2026-08-03 20:05:54", "任务已全部完成！"))
        run.handle_event(ev("2026-08-03 20:05:54", "已停止"))
        self.assertEqual(run.classify(), "success")


class TestFailureModes(unittest.TestCase):
    def test_connection_failure(self):
        run = mn.Run.create(
            "2026-11-20T20:57", dt("2026-11-20 20:57"), expected_fight_twice()
        )
        run.handle_event(ev("2026-11-20 20:57:24", "正在连接……"))
        run.handle_event(ev("2026-11-20 20:57:25", "StartTasksFailed: connectFailed"))
        self.assertEqual(run.classify(), "failed")
        self.assertEqual(run.start_failed_reason, "连接模拟器失败")

    def test_cancel(self):
        run = mn.Run.create(
            "2026-11-23T19:01", dt("2026-11-23 19:01"), expected_fight_twice()
        )
        run.handle_event(ev("2026-11-23 19:01:03", "开始任务：开始唤醒"))
        run.handle_event(ev("2026-11-23 19:05:00", "已停止"))
        self.assertEqual(run.classify(), "cancelled")


class TestReportAndCard(unittest.TestCase):
    def cfg(self):
        cfg = mn.deep_merge(mn.DEFAULT_CONFIG, {})
        cfg["feishu"]["webhook"] = "https://example.invalid/hook"
        return cfg

    def test_card_payload(self):
        run = mn.Run.create(
            "2026-11-23T07:02", dt("2026-11-23 07:02"), expected_fight_twice()
        )
        for task in run.tasks:
            task.begin(dt("2026-11-23 07:02"))
            task.complete(dt("2026-11-23 07:03"))
        run.tasks[2].fail(dt("2026-11-23 07:11"), "关卡识别失败")
        run.end_time = dt("2026-11-23 07:40")
        run.status = "partial"
        report = mn.build_report(run, self.cfg())
        payload = mn.build_feishu_payload(report, self.cfg())
        self.assertEqual(payload["msg_type"], "interactive")
        self.assertEqual(payload["card"]["header"]["template"], "orange")
        self.assertIn("MAA", payload["card"]["header"]["title"]["content"])
        self.assertIn("关卡识别失败", payload["card"]["elements"][0]["text"]["content"])

    def test_format_duration(self):
        self.assertEqual(mn.fmt_duration(38), "38s")
        self.assertEqual(mn.fmt_duration(2304), "38m24s")
        self.assertEqual(mn.fmt_duration(3960), "1h06m")


class TestAutoStartWindow(unittest.TestCase):
    def test_auto_start_due(self):
        schedule = [(7, 2), (19, 1)]
        self.assertIsNone(mn.auto_start_due(dt("2026-11-24 06:56:59"), schedule, 5))
        self.assertEqual(
            mn.auto_start_due(dt("2026-11-24 06:57:00"), schedule, 5)[1],
            "2026-11-24T07:02",
        )
        self.assertEqual(
            mn.auto_start_due(dt("2026-11-24 07:01:59"), schedule, 5)[1],
            "2026-11-24T07:02",
        )
        self.assertIsNone(mn.auto_start_due(dt("2026-11-24 07:02:00"), schedule, 5))
        self.assertEqual(
            mn.auto_start_due(dt("2026-11-24 18:56:30"), schedule, 5)[1],
            "2026-11-24T19:01",
        )


class TestAutoControlBehavior(unittest.TestCase):
    def fake_watcher(self):
        cfg = mn.deep_merge(mn.DEFAULT_CONFIG, {})
        cfg["policy"]["auto_start_maa"] = True
        cfg["policy"]["auto_quit_maa"] = True
        cfg["policy"]["auto_start_lead_minutes"] = 5
        cfg["policy"]["auto_quit_delay_seconds"] = 0
        watcher = object.__new__(mn.Watcher)
        watcher.cfg = cfg
        watcher.dry_run = False
        watcher.schedule = [(7, 2)]
        watcher.state = {}
        watcher.active = None
        watcher.last_auto_check = 0.0
        watcher.pending_quit_at = None
        watcher.pending_quit_slot = ""
        return watcher

    def test_auto_start_once_in_window(self):
        watcher = self.fake_watcher()
        opened = []
        watcher._maa_running = lambda: False
        watcher._open_maa = lambda: opened.append(True) or True
        watcher._check_auto_start(dt("2026-11-24 06:58:00"))
        self.assertEqual(opened, [True])
        self.assertIn("2026-11-24T07:02", watcher.state["auto_started"])
        watcher._check_auto_start(dt("2026-11-24 06:58:10"))
        self.assertEqual(len(opened), 1)

    def test_auto_start_skips_if_already_running(self):
        watcher = self.fake_watcher()
        opened = []
        watcher._maa_running = lambda: True
        watcher._open_maa = lambda: opened.append(True) or True
        watcher._check_auto_start(dt("2026-11-24 06:58:00"))
        self.assertEqual(opened, [])

    def test_pending_quit_fires(self):
        watcher = self.fake_watcher()
        quit_called = []
        watcher._maa_running = lambda: True
        watcher._quit_maa = lambda: quit_called.append(True)
        watcher.pending_quit_at = dt("2026-11-24 07:45:00")
        watcher.pending_quit_slot = "2026-11-24T07:02"
        watcher._check_pending_quit(dt("2026-11-24 07:45:01"))
        self.assertEqual(quit_called, [True])
        self.assertIsNone(watcher.pending_quit_at)


class TestSchedulePlist(unittest.TestCase):
    def test_read_schedule_from_prefs_plist(self):
        cfg = mn.load_config(ROOT / "config.example.json")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prefs.plist"
            value = json.dumps(
                [
                    {"hour": 7, "minute": 2, "isEnabled": True},
                    {"hour": 19, "minute": 1, "isEnabled": True},
                    {"hour": 12, "minute": 0, "isEnabled": False},
                ]
            )
            path.write_bytes(
                plistlib.dumps({"MAAScheduledDailyTaskTimer": value})
            )
            cfg["maa"]["schedule_plist"] = str(path)
            self.assertEqual(mn.read_schedule(cfg), [(7, 2), (19, 1)])


class TestRealLogs(unittest.TestCase):
    def setUp(self):
        self.cfg = mn.load_config(ROOT / "config.example.json")
        self.gui = mn.expand_path(self.cfg["maa"]["gui_log"])

    def historical_cfg(self):
        """2026-11-23 时任务列表只有两个刷理智，避免被本机当前配置影响。"""
        tmp = Path(tempfile.mkdtemp(prefix="maa-plist-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        tasks = [
            {"enabled": True, "task": {"startup": {"_0": {}}}},
            {"enabled": True, "task": {"recruit": {"_0": {}}}},
            {"enabled": True, "task": {"infrast": {"_0": {}}}},
            {"enabled": True, "task": {"fight": {"_0": {"stage": "PR-A-1"}}}},
            {"enabled": True, "task": {"fight": {"_0": {"stage": "1-7"}}}},
            {"enabled": True, "task": {"mall": {"_0": {}}}},
            {"enabled": True, "task": {"award": {"_0": {}}}},
            {"enabled": True, "task": {"switchTheme": {"_0": {"themes": ["齐聚"]}}}},
        ]
        path = tmp / "Default.plist"
        path.write_bytes(plistlib.dumps(tasks))
        cfg = mn.deep_merge(mn.DEFAULT_CONFIG, self.cfg)
        cfg["maa"]["daily_tasks_plist"] = str(path)
        return cfg

    def test_replay_2026_11_23_0702(self):
        if not self.gui.exists():
            self.skipTest("本机没有 MAA gui.log")
        if "2026-11-23 07:02" not in self.gui.read_text(errors="replace"):
            self.skipTest("本机日志不包含 2026-11-23 07:02")
        start = dt("2026-11-23 07:02")
        end = dt("2026-11-23 07:45")
        report = mn.replay_run(self.historical_cfg(), start, end, "07:02")
        self.assertIsNotNone(report)
        assert report is not None
        self.assertEqual(report["status"], "partial")
        task_map = {item["display"]: item for item in report["tasks"]}
        self.assertEqual(task_map["刷理智（PR-A-1）"]["state"], "error")
        self.assertEqual(task_map["刷理智（1-7）"]["state"], "completed")
        reason = task_map["刷理智（PR-A-1）"]["reason"]
        if not reason:
            self.skipTest("本机 asst 日志已轮转，无法校验失败原因")
        self.assertIn("关卡识别失败", reason)

    @unittest.skipUnless(mn.MAA_DAILY_PLIST.exists(), "本机没有 MAA 任务清单")
    def test_expected_tasks_from_real_plist(self):
        tasks = mn.load_expected_tasks(self.cfg)
        fights = [item for item in tasks if item.key == "fight"]
        details = [item.detail for item in fights]
        if "PR-A-1" not in details or "1-7" not in details:
            self.skipTest("本机 MAA 任务列表不含 PR-A-1 / 1-7")
        self.assertGreaterEqual(len(fights), 2)
        self.assertFalse(any(item.key == "closedown" for item in tasks))


if __name__ == "__main__":
    unittest.main()
