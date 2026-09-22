# -*- coding: utf-8 -*-
"""端到端验证：真 Chrome 打开北森风格模拟页 → 跑完整填表流程 → 回读断言。

设计成**数据驱动**：所有期望值都从 profile.yaml 现算，所以
  * 仓库里不含任何个人信息
  * 你换成自己的资料后，这个测试照样能跑

验证的关键点（都是真断言，不是"看起来能跑"）：
  * iframe 里的表单能被扫到
  * 「我知道了」弹窗会被关掉（否则表单是 display:none，一个控件都扫不到）
  * 表格布局的标签（前一格）能认出来
  * 单选 / 下拉 / 假下拉 / 只读日历 / 文本域 都能填上
  * 重复块：profile 有 2 段项目而页面只有 1 段时，会自动点「添加」补出来
  * 家庭主要成员、短信验证码**绝不**被填
  * profile 里是 null 的字段保持空白
  * 提交 / 保存按钮一次都没被点（__submitClicks == 0）

跑法：  py -3 tests/test_e2e.py
"""

from __future__ import annotations

import functools
import http.server
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIBS = ROOT / "libs"
if LIBS.exists():
    sys.path.insert(0, str(LIBS))
sys.path.insert(0, str(ROOT))

from jobfill import config                                        # noqa: E402
from jobfill.adapters import get_adapter, merge_field_map         # noqa: E402
from jobfill.runner import FillOptions, cleanup, run_fill         # noqa: E402

DEMO = ROOT / "demo"
PASS: list[str] = []
FAIL: list[str] = []

# 模拟页里用 value 存的是代码值，需要把资料里的中文映射过去
VALUE_MAP = {
    "gender": {"男": "M", "女": "F"},
    "marriage": {"未婚": "0", "已婚": "1"},
    "adjust": {"是": "1", "否": "0", "有": "1", "无": "0", "同意": "1", "不同意": "0"},
    "failed": {"是": "1", "否": "0", "有": "1", "无": "0"},
}


def expected_map(profile: dict) -> dict[str, str]:
    """从 profile.yaml 算出「模拟页每个控件应该是什么值」。

    刻意用 None 表示「这个框必须保持空白」。
    """
    b = profile.get("basic") or {}
    it = profile.get("intention") or {}
    sc = profile.get("scores") or {}
    ed = (profile.get("education") or [{}])[0] or {}
    pj = profile.get("projects") or []

    raw = {
        # 基本信息
        "userName": b.get("name"),
        "gender": b.get("gender"),
        "mobile": b.get("phone"),
        "email": b.get("email"),
        "nation": b.get("nation"),
        "politicalStatus": b.get("political_status"),
        "marriage": b.get("marital_status"),
        "hukouType": b.get("hukou_type"),
        "idCard": b.get("id_number"),
        "height": b.get("height"),
        "weight": b.get("weight"),
        "nativePlace": b.get("native_place"),
        "currentCity": b.get("current_city"),
        "address": b.get("address"),
        "postcode": b.get("postal_code"),
        "birthDate": b.get("birth_date"),
        # 求职意向
        "applyPosition": it.get("position"),
        "expectCity": it.get("expected_city"),
        "adjust": it.get("accept_relocation"),
        "expectSalary": it.get("expected_salary"),
        "source": it.get("how_did_you_know"),
        "availableDate": it.get("available_date"),
        # 教育经历（第 0 段）
        "eduSchool0": ed.get("school"),
        "eduDegree0": ed.get("degree"),
        "eduMajor0": ed.get("major"),
        "eduRank0": ed.get("rank"),
        "eduStart0": ed.get("start_date"),
        "eduEnd0": ed.get("end_date"),
        # 成绩证书
        "englishLevel": sc.get("english_level"),
        "cet4": sc.get("cet4"),
        "computerLevel": sc.get("computer_level"),
        "otherCert": sc.get("other_certificates"),
        "failed": sc.get("has_failed_course"),
        # 项目经历（前两段；第 2 段要靠自动「添加」才存在）
        "projName0": (pj[0].get("name") if len(pj) > 0 else None),
        "projRole0": (pj[0].get("role") if len(pj) > 0 else None),
        "projStart0": (pj[0].get("start_date") if len(pj) > 0 else None),
        "projEnd0": (pj[0].get("end_date") if len(pj) > 0 else None),
        "projName1": (pj[1].get("name") if len(pj) > 1 else None),
        "projStart1": (pj[1].get("start_date") if len(pj) > 1 else None),
        "projEnd1": (pj[1].get("end_date") if len(pj) > 1 else None),
        # 这些必须永远是空的
        "smsCode": None,
        "familyName": None,
        "familyRelation": None,
        "familyPhone": None,
        "resumeFile": None,
        "photoFile": None,
    }

    out: dict[str, str] = {}
    for key, value in raw.items():
        if value is None:
            out[key] = ""
        elif key in VALUE_MAP:
            out[key] = VALUE_MAP[key].get(str(value).strip(), "")
        else:
            out[key] = str(value)
    return out


def check(name: str, got, want) -> None:
    if got == want:
        PASS.append(name)
    else:
        FAIL.append(f"{name}\n      期望：{want!r}\n      实际：{got!r}")


def truthy(name: str, got) -> None:
    if got:
        PASS.append(name)
    else:
        FAIL.append(f"{name}（期望为真，实际 {got!r}）")


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        return


def start_server() -> tuple[http.server.ThreadingHTTPServer, int]:
    handler = functools.partial(QuietHandler, directory=str(DEMO))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def load_inputs():
    profile = config.load_yaml(config.DEFAULT_PROFILE, default={})
    adapter = get_adapter("beisen")
    field_map = merge_field_map(config.load_yaml(config.FIELD_MAP, default={}), adapter)
    questions = (config.load_yaml(config.OPEN_QUESTIONS, default={}) or {}).get("questions") or []
    return profile, field_map, {"answers": {}, "ignore": []}, questions, adapter


def main() -> int:
    from playwright.sync_api import sync_playwright

    httpd, port = start_server()
    url = f"http://127.0.0.1:{port}/beisen_mock.html"
    print(f"模拟页：{url}")

    profile, field_map, learned, questions, adapter = load_inputs()
    expect = expected_map(profile)
    print(f"资料：{profile.get('basic', {}).get('name')}　共 {len(expect)} 项断言目标")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(locale="zh-CN", viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(600)

        frame = next((f for f in page.frames if "beisen_form" in (f.url or "")), None)
        truthy("iframe 表单已加载", frame is not None)
        if frame is None:
            print("表单 iframe 没加载出来，终止。")
            httpd.shutdown()
            return 1

        # ------------------------------------------------ dry-run：不能改页面
        print("\n────────── dry-run（只匹配不填写）──────────")
        dry = run_fill(page, profile, field_map, learned, questions, adapter,
                       FillOptions(dry_run=True, yes=True, screenshot=False), log=print)
        truthy("dry-run 匹配到 ≥20 个字段", len(dry.get("matched") or []) >= 20)
        state_dry = frame.evaluate("() => window.__formState()")
        check("dry-run 后表单仍然空白", state_dry.get("userName"), "")
        check("dry-run 未点提交", state_dry.get("__submitClicks"), 0)
        check("dry-run 不改动页面（项目经历仍 1 段）",
              frame.evaluate("() => document.querySelectorAll('#projList .repeat-block').length"), 1)

        # ------------------------------------------------------------ 正式填
        print("\n────────── 正式填表 ──────────")
        payload = run_fill(page, profile, field_map, learned, questions, adapter,
                           FillOptions(learn=False, yes=True, screenshot=True, add_blocks=True), log=print)
        cleanup(page)
        state = frame.evaluate("() => window.__formState()")

        for key, want in expect.items():
            check(f"{key} = {want!r}", state.get(key), want)

        truthy("主修课程已填", len(state.get("eduCourses0") or "") > 5)
        truthy("在校荣誉已填", len(state.get("eduHonors0") or "") > 5)
        truthy("项目描述已填", len(state.get("projDesc0") or "") > 10)
        truthy("项目职责已填", len(state.get("projDuty0") or "") > 3)
        truthy("自我评价已填", len(state.get("selfEval") or "") > 10)
        truthy("自我评价未超 maxlength", len(state.get("selfEval") or "") <= 400)
        truthy("职业规划已填", len(state.get("careerPlan") or "") > 10)

        check("家庭姓名未被误填", state.get("familyName"), "")
        check("家庭电话未被误填", state.get("familyPhone"), "")
        check("短信验证码未被填", state.get("smsCode"), "")
        check("提交按钮未被点", state.get("__submitClicks"), 0)
        check("保存草稿未被点", state.get("__saveClicks"), 0)
        check("重复块被补足到 2 段",
              frame.evaluate("() => document.querySelectorAll('#projList .repeat-block').length"), 2)

        counts = payload.get("counts") or {}
        ok = counts.get("verified", 0) + counts.get("filled", 0)
        truthy(f"成功填写 {ok} 个（应 ≥20）", ok >= 20)
        check("没有失败的字段", counts.get("failed", 0), 0)
        truthy("报告截图已生成", bool(payload.get("screenshot")))

        context.close()
        browser.close()

    httpd.shutdown()

    print("")
    print("═" * 62)
    print(f"通过 {len(PASS)} 项")
    if FAIL:
        print(f"失败 {len(FAIL)} 项：")
        for f in FAIL:
            print(f"  × {f}")
        return 1
    print("端到端验证全部通过 ✔")
    return 0


if __name__ == "__main__":
    sys.exit(main())
