#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_skillcoach.py — 检查器自身的回归测试

**为什么必须有**：没有测试，这个 skill 就过不了它自己的 I2 判据——
而 I2 被列为 Critical，理由是「失败会静默复利」。

**测试的重点不是「能跑」，是「会不会误报」**：
检查器最坏的失败模式不是漏报，是**用一种看起来客观的方式产出误报**——
那会让人开始忽略红灯。

用法：python3 tests/test_skillcoach.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import skillcoach as S  # noqa: E402


def codes() -> dict:
    return {r["code"]: r["level"] for r in S.RESULT}


# ⚠️ **故意拆开拼接**：这是一条合成的测试夹具路径，不是真实用户目录。
#    若在源码里写成整串字面量，本技能跑自诊断时会被 Q4 判成「绝对用户路径泄露」——
#    也就是**测试夹具把被测试的门骗响了**。拆开写，源码里就不存在可匹配的路径串。
#    （另一条路是给每行加 `# skillcoach:ignore`，但本类里有一条测试**必须**不带标记，
#     否则「没有标记时仍然要报」就永远测不了。）
FAKE = "/" + "Users" + "/alice/notes.md"


class TestFrontmatter(unittest.TestCase):
    def setUp(self):
        S.RESULT.clear()

    def test_顶层标量键被解析(self):
        fm = S.frontmatter("---\nname: foo\ndescription: bar\n---\n正文")
        self.assertEqual(fm.get("name"), "foo")
        self.assertEqual(fm.get("description"), "bar")

    def test_嵌套键不得被当成顶层字段(self):
        """钉住历史 bug：缩进行被当顶层字段 → 照规范放进 metadata.* 反而必被报。"""
        fm = S.frontmatter("---\nname: foo\nmetadata:\n  version: 1.0.0\n---\n")
        self.assertNotIn("version", fm)
        self.assertIn("metadata", fm)

    def test_无_frontmatter_返回空(self):
        self.assertEqual(S.frontmatter("# 标题\n正文"), {})


class TestC1Description(unittest.TestCase):
    def setUp(self):
        S.RESULT.clear()

    def _c(self, d):
        S.RESULT.clear()
        S.check_c1({"description": d})
        return codes()

    def test_四问齐全_全过(self):
        d = ("做某件事。用于：场景一、场景二。Use when the user asks X. "
             "触发词：做X、帮我X。不适用：Y（那是别的技能的活）、Z。")
        c = self._c(d)
        # ⚠️ 只断言四问，不整体比对 —— 整体比对会在**新增一条信息性 rec**（C1.0 长度）
        #    时无辜变红，逼着人改测试。测试该钉行为，不该钉输出条数。
        for k in ("C1.1", "C1.2", "C1.3", "C1.4"):
            self.assertEqual(c.get(k), "PASS", f"{k} 没通过：{c}")

    def test_缺负向边界_判_FAIL(self):
        """这是最贵的一条——没有它，任何沾边的事都会触发。"""
        c = self._c("帮忙做点事。用于：各种场景。Use when needed. 触发词：帮我。")
        self.assertEqual(c["C1.2"], "FAIL")

    def test_无_description_判_FAIL(self):
        c = self._c("")
        self.assertEqual(c["C1"], "FAIL")

    def test_过短判_FAIL(self):
        c = self._c("很短")
        self.assertEqual(c["C1"], "FAIL")


class TestC5NegationExemption(unittest.TestCase):
    """C5 的自指误报——本教练最该防的一类错。"""

    def setUp(self):
        S.RESULT.clear()

    def test_讲述反模式不得被误判为违规(self):
        text = "- ❌ 不在 skill 里写「当前日期」「本次会话编号」这类会变的东西\n- 别的正常句子"
        S.RESULT.clear()
        S.check_c5(text)
        self.assertNotEqual(codes().get("C5"), "FAIL", "讲了反模式却被判违规 = 自指误报")

    def test_真违规照判_FAIL(self):
        text = "在正文开头写：当前日期是几号，然后继续。"
        S.RESULT.clear()
        S.check_c5(text)
        self.assertEqual(codes().get("C5"), "FAIL")

    def test_干净文本判_PASS(self):
        S.RESULT.clear()
        S.check_c5("这是一段完全正常的说明文字，没有任何会变的内容。")
        self.assertEqual(codes().get("C5"), "PASS")


class TestC2AndC3(unittest.TestCase):
    def setUp(self):
        S.RESULT.clear()

    def test_长正文无_references_判_FAIL(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            S.RESULT.clear()
            S.check_c2(Path(d), "\n".join(f"第{i}行内容" for i in range(150)))
            self.assertEqual(codes().get("C2.1"), "FAIL")

    def test_强约束全在中部_判_FAIL(self):
        lines = ["开头" for _ in range(20)] + ["**必须**这样做"] * 3 + ["结尾" for _ in range(20)]
        S.RESULT.clear()
        S.check_c3("\n".join(lines))
        self.assertEqual(codes().get("C3"), "FAIL")

    def test_强约束在开头_判_PASS(self):
        lines = ["**必须**先做这个"] + ["中间内容"] * 40 + ["结尾"]
        S.RESULT.clear()
        S.check_c3("\n".join(lines))
        self.assertEqual(codes().get("C3"), "PASS")


class TestTriggerParsing(unittest.TestCase):
    """钉住「按标点乱切」那个 bug——它会产出看着热闹的噪声。"""

    def setUp(self):
        S.RESULT.clear()

    def test_只从触发词段提取(self):
        d = "做某件事，把需求理清，判断形态。用于：从零做一个 Skill、给它做体检。触发词：做个 skill、检查这个 skill、体检一下。不适用：写文案。"
        S.RESULT.clear()
        S.check_triggers(Path("."), {"description": d})
        info = [r for r in S.RESULT if r["code"] == "Q1.0"]
        self.assertTrue(info)
        detail = info[0]["detail"]
        self.assertIn("做个 skill", detail)
        self.assertNotIn("判断形态", detail, "把『用于』段的碎片也提出来了")

    def test_没有触发词段要提醒(self):
        S.RESULT.clear()
        S.check_triggers(Path("."), {"description": "做某事。用于：场景。不适用：别的。"})
        self.assertEqual(codes().get("Q1.0"), "WARN")


class TestSecuritySelfExempt(unittest.TestCase):
    def setUp(self):
        S.RESULT.clear()

    def test_扫描器自身被豁免(self):
        """模式串写在脚本里，不豁免会自己命中自己。"""
        S.RESULT.clear()
        S.check_security(ROOT)
        self.assertNotEqual(codes().get("Q4"), "FAIL",
                            "扫描器把自己的模式串当成了违规")


class TestInlineIgnore(unittest.TestCase):
    """行级忽略标记 —— 由真实误报驱动。

    2026-10-10，本工具第一次拿去审一个**真实的、别人写的** skill 时，
    在对方的一个测试文件里报了一处「绝对用户路径」——实为测试夹具的合成示例，
    而且那一行**本就自带忽略标记**。检查器当时不认标记 ⇒ 误报。
    回归就钉死这件事：**别让标注物反过来挡住标注者。**
    """

    def setUp(self):
        S.RESULT.clear()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _写(self, name, text):
        (Path(self.tmp) / name).write_text(text, encoding="utf-8")

    def test_本工具的标记能豁免(self):
        self._写("a.py", f'P = "{FAKE}"  # skillcoach:ignore\n')
        S.check_security(Path(self.tmp))
        self.assertNotEqual(codes().get("Q4"), "FAIL", "自带忽略标记的行没被跳过")

    def test_认外部的标记_skillcheck(self):
        """兼容目标技能已在用的标记名，不要另立一套。"""
        self._写("a.py", f'P = "{FAKE}"  # skillcheck:ignore PORT-09\n')
        S.check_security(Path(self.tmp))
        self.assertNotEqual(codes().get("Q4"), "FAIL", "不认 skillcheck:ignore ⇒ 对真实项目误报")

    def test_没有标记时仍然要报(self):
        """豁免不能宽到自己把门关掉 —— 这是死门的经典形态。"""
        self._写("a.py", f'P = "{FAKE}"\n')
        S.check_security(Path(self.tmp))
        self.assertEqual(codes().get("Q4"), "FAIL", "该报的没报 ⇒ 门形同虚设")

    def test_命中带行号(self):
        """报错位置指不准，门的效果打对折。"""
        self._写("a.py", f'X = 1\nY = 2\nP = "{FAKE}"\n')
        S.check_security(Path(self.tmp))
        detail = " ".join(str(r.get("detail", "")) for r in S.RESULT)
        self.assertIn("a.py:3", detail, f"命中没指到行：{detail}")


class TestSecurityDetectionWidth(unittest.TestCase):
    """钉住一次**检测面过窄**的漏报（判据 #71：红灯只亮一半）。

    第一版 pattern 是 `rm\\s+-rf\\s+/`——**要求 `rm -rf` 后紧跟 `/`**，
    于是「指向家目录 / 相对目录 / 变量路径」的各种写法、以及 `rm -fr` 变体，**全部逃逸**，
    而输出照报「命中 N 处」、文案还自称覆盖「危险删除」。

    ⚠️ 样本必须**拆开拼接**：整串写在源码里会被本门自己命中——
    **测试夹具把被测的门骗响**，本文件顶部已因此栽过一次。
    """

    def setUp(self):
        S.RESULT.clear()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _命中(self, text, name="a.sh") -> bool:
        (Path(self.tmp) / name).write_text(text, encoding="utf-8")
        S.RESULT.clear()
        S.check_security(Path(self.tmp))
        return codes().get("Q4") == "FAIL"

    def test_删除命令的四种写法都要命中(self):
        for cmd in ("rm" + " -rf " + "~/build",
                    "rm" + " -rf " + "./dist",
                    "rm" + " -fr " + "/tmp/a",
                    "rm" + " -rf " + "$HOME/cache"):
            with self.subTest(cmd=cmd):
                self.assertTrue(self._命中(cmd + "\n"), f"漏报：{cmd}")

    def test_写入裸设备要命中(self):
        self.assertTrue(self._命中("dd if=/dev/zero of=/dev/" + "sda\n"))

    def test_递归放开根权限要命中(self):
        self.assertTrue(self._命中("chmod -R 777 " + "/\n"))

    def test_非_sk_前缀的密钥要命中(self):
        self.assertTrue(self._命中("AWS_SECRET_ACCESS_KEY=" + '"wJalrXUtnFEMI9xQ"\n'))

    def test_私钥明文要命中(self):
        self.assertTrue(self._命中("-----BEGIN RSA PRIVATE " + "KEY-----\n"))

    def test_文档里的反例语境不误报(self):
        """一份「教人别写危险命令」的文档，不能被自己的扫描器判违规。"""
        (Path(self.tmp) / "doc.md").write_text(
            "- ❌ 不许在脚本里写 " + "rm" + " -rf " + "~/ 这种命令\n", encoding="utf-8")
        S.RESULT.clear()
        S.check_security(Path(self.tmp))
        self.assertNotEqual(codes().get("Q4"), "FAIL", "文档讲反例被判违规 = 自指误报")

    def test_代码文件不给反例豁免(self):
        """豁免只对 `.md` 生效——真代码里出现就是真问题。"""
        self.assertTrue(self._命中("# 不要这样做\n" + "rm" + " -rf " + "~/build\n"),
                        "代码文件里带『不要』就豁免 ⇒ 门被绕过")


class TestFrontmatterSeverity(unittest.TestCase):
    """钉住一次**口径冲突**造成的误报（判据 #70 自测盲区）。

    第一版 `FM` 把 `name` 与 `description` 并成一个 FAIL，
    于是**一个完全符合平台五字段规范的包被判 D**——
    而本包自己有 name，这个冲突在自测里**永远暴露不了**。
    """

    def setUp(self):
        S.RESULT.clear()
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _跑(self, fm: str) -> dict:
        (self.tmp / "SKILL.md").write_text("---\n" + fm + "---\n\n# 标题\n", encoding="utf-8")
        S.RESULT.clear()
        S.diagnose(self.tmp)
        return codes()

    def test_只有_description_没有_name_不算_FAIL(self):
        c = self._跑('description: "把文本转大写。用于：需要时。'
                     '不适用：翻译（那是翻译类技能的活）。触发词：转大写。Use when needed."\n')
        self.assertNotEqual(c.get("FM"), "FAIL", "按平台规范做的包被判 FAIL ⇒ 误报")
        self.assertEqual(c.get("FM.name"), "WARN")

    def test_缺_description_仍要_FAIL(self):
        c = self._跑("name: foo\n")
        self.assertEqual(c.get("FM"), "FAIL",
                         "description 是唯一的触发依据，缺了必须 FAIL")


class TestI5BoundaryPhrasing(unittest.TestCase):
    """钉住 I5 的**字面串**误报（判据 #68 家族）。

    第一版 b1 是 `(不接|不做什么|不处理什么|Not for)`——
    于是「不处理编码转换」「不做翻译」这类**最自然的写法**全不匹配，
    一个边界写得很清楚的 skill 被误报「缺：不接什么」。
    """

    def setUp(self):
        S.RESULT.clear()
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_不处理_不做_这类自然写法要认(self):
        text = ("## 边界\n不处理编码转换；不做翻译；不修改标点。\n"
                "## 何时停\n单次处理完即停。\n## 交给谁\n那是翻译类技能的活。")
        S.RESULT.clear()
        S.check_i(self.tmp, text)
        self.assertEqual(codes().get("I5"), "PASS", "「不处理 X」不认 ⇒ 误报缺「不接什么」")

    def test_三条缺一仍要_FAIL(self):
        S.RESULT.clear()
        S.check_i(self.tmp, "## 边界\n不处理编码转换。\n")
        self.assertEqual(codes().get("I5"), "FAIL", "只有一条边界也放行 ⇒ 门槛没立起来")


class TestC7ExcuseSection(unittest.TestCase):
    """C7 反借口段 —— 补上一次「**文档承诺了工具没做的事**」。

    SKILL.md 与 references/02 都写「正文 ≥80 行必填」，而脚本里一行实现都没有。
    """

    def setUp(self):
        S.RESULT.clear()

    def _长(self, n=None):
        return "\n".join(f"第{i}行" for i in range(n or (S.EXCUSE_MIN_LINES + 5)))

    def test_长正文无反借口段要_WARN(self):
        S.RESULT.clear()
        S.check_excuse(self._长())
        self.assertEqual(codes().get("C7"), "WARN")

    def test_短正文不要求(self):
        S.RESULT.clear()
        S.check_excuse("很短的一段说明。")
        self.assertEqual(codes().get("C7"), "INFO")

    def test_有反借口段则_PASS(self):
        S.RESULT.clear()
        S.check_excuse(self._长() + "\n## 常见借口与反驳\n| 会说 | 为什么不行 |\n")
        self.assertEqual(codes().get("C7"), "PASS")

    def test_只是提到这个词不算有(self):
        """⚠️ 钉住**夹具把门骗响**的一次教训。

        第一版正则只搜词，于是一份正文里写着「并没" + "有反借口段这种东西」的夹具
        被判成「有反借口段」——**检查器被一句提及骗过去了**。
        现在要求词出现在**标题行**。
        """
        S.RESULT.clear()
        S.check_excuse(self._长() + "\n正文到此为止，其中并没" + "有反借口段这种东西。\n")
        self.assertEqual(codes().get("C7"), "WARN", "只提到词就放行 ⇒ 门被措辞骗过")


class TestNegativeGate(unittest.TestCase):
    """Q1.h 负样本门 —— 必须**真的会响**。

    一条永不报警的门 = 没有门，而且更坏：它让人以为查过了。
    所以这里不光测「合规时 PASS」，更要测**各种作弊姿势都会 FAIL**。
    """

    DESC = "……不适用：只判断一份交付物对不对、给 Skill 写传播文案与推广、一次性的临时提示词。"

    def setUp(self):
        S.RESULT.clear()
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "tests").mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _写负样本(self, items):
        (self.tmp / "tests" / "triggers.json").write_text(
            json.dumps({"负样本": items}, ensure_ascii=False), encoding="utf-8")

    def _合法条目(self, n=3, cite="给 Skill 写传播文案与推广"):
        return [{"话术": f"话术{i}", "预期": "不触发", "挡住它的那句": cite}
                for i in range(n)]

    def test_引用真实边界则_PASS(self):
        self._写负样本(self._合法条目())
        S.check_negatives(self.tmp, self.DESC)
        self.assertEqual(codes().get("Q1.h"), "PASS")

    def test_引用编造的边界要_FAIL(self):
        """最坏的作弊：边界只写在测试里，description 根本没写。"""
        self._写负样本(self._合法条目(cite="这条边界根本不存在"))
        S.check_negatives(self.tmp, self.DESC)
        self.assertEqual(codes().get("Q1.h"), "FAIL", "编造引用没被抓到 ⇒ 门形同虚设")

    def test_条数不足要_FAIL(self):
        self._写负样本(self._合法条目(n=2))
        S.check_negatives(self.tmp, self.DESC)
        self.assertEqual(codes().get("Q1.h"), "FAIL", "2 条也放行 ⇒ 门槛没立起来")

    def test_预期写错要_FAIL(self):
        it = self._合法条目()
        it[0]["预期"] = "触发"
        self._写负样本(it)
        S.check_negatives(self.tmp, self.DESC)
        self.assertEqual(codes().get("Q1.h"), "FAIL", "「触发」和「不触发」写反没被抓到")

    def test_缺文件只_WARN不_FAIL(self):
        """没写负样本是「还没做」，不是「做错了」——两者不能报同一级。"""
        S.check_negatives(self.tmp, self.DESC)
        self.assertEqual(codes().get("Q1.h"), "WARN")

    def test_坏_JSON要_FAIL(self):
        (self.tmp / "tests" / "triggers.json").write_text("{不是 json", encoding="utf-8")
        S.check_negatives(self.tmp, self.DESC)
        self.assertEqual(codes().get("Q1.h"), "FAIL")

    def test_外部格式的_negatives_不许误报(self):
        """钉住一次**真实误报**。

        另一个 skill 早就有自己的 tests/triggers.json——键是 `positives` / `negatives`。
        本门硬套自己的 `负样本` 键，读出 0 条 ⇒ 报 FAIL「负样本 0 条」。
        **检查器把自己的约定当成了通用约定**——与 Q4 不认外部忽略标记是同一个病。
        """
        (self.tmp / "tests" / "triggers.json").write_text(
            json.dumps({"note": "x", "positives": [],
                        "negatives": [{"text": "今天天气怎么样", "why": "完全无关"}]},
                       ensure_ascii=False), encoding="utf-8")
        S.check_negatives(self.tmp, self.DESC)
        self.assertEqual(codes().get("Q1.h"), "PASS", "认不出外部格式就报错 ⇒ 误报")

    def test_认不出的字段只_WARN不_FAIL(self):
        """认不出就说认不出——**不替别人判对错**。"""
        (self.tmp / "tests" / "triggers.json").write_text(
            json.dumps({"something_else": 1}, ensure_ascii=False), encoding="utf-8")
        S.check_negatives(self.tmp, self.DESC)
        self.assertEqual(codes().get("Q1.h"), "WARN")

    def test_顶层是列表也不崩(self):
        (self.tmp / "tests" / "triggers.json").write_text("[1, 2, 3]", encoding="utf-8")
        S.check_negatives(self.tmp, self.DESC)
        self.assertEqual(codes().get("Q1.h"), "WARN")


class TestJsonMode(unittest.TestCase):
    """`--json` 的 stdout 必须是**纯 JSON**。

    踩过的坑：`main()` 在调诊断之前先打了一行标题，于是输出不是合法 JSON。
    **人眼看终端完全正常，只有脚本化调用才会崩** —— 所以必须有一条脚本化的用例来钉它。
    教训：**给机器的输出里不许掺给人看的东西。**
    """

    def _run(self, *argv):
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "skillcoach.py"), *argv],
            capture_output=True, text=True, cwd=ROOT)

    def _可解析(self, *argv):
        r = self._run(*argv)
        try:
            return json.loads(r.stdout)
        except json.JSONDecodeError as e:
            self.fail(f"`{' '.join(argv)}` 的 stdout 不是合法 JSON（{e}）：\n{r.stdout[:300]}")

    def test_self_json_可解析(self):
        data = self._可解析("--self", "--json")
        self.assertIsInstance(data, list)
        self.assertTrue(data)
        self.assertEqual(self._run("--self", "--json").returncode, 0)

    def test_diagnose_json_可解析(self):
        self._可解析("--diagnose", str(ROOT), "--json")

    def test_triggers_json_可解析(self):
        """曾经完全忽略 --json，直接吐人类文本。"""
        self._可解析("--triggers", "--json")

    def test_security_json_可解析(self):
        self._可解析("--security", "--json")


class TestNoInternalTraces(unittest.TestCase):
    """公开包里不许留「能指认到某个人或某台机器」的痕迹。

    ⚠️ 这类东西**不会让门响、不会让测试红**，只会**静默地把内部语境带出去**。
    功能缺陷坏了有人报错；内部信息漏了**没人会告诉你**——所以得专门有一条盯着。

    ⭐ 两条设计约束，都是踩过的坑：

    ① **黑名单必须放包外。**
       把要遮的词直接写进这个文件，就等于**把要藏的东西随包一起发出去**——
       「用来遮挡的东西」自己不能暴露在外面。
       ⇒ 私密词从包外文件读（`$SKILLCOACH_DENYLIST`，回退 `$PREFLIGHT_DENYLIST`）；
          **没设时显式报「这一类没查」**，绝不静默当作零命中。
          「没查」和「查过没问题」是两件事。

    ② **本类不再给自己开豁免。**
       旧版跳过自身扫描，理由是「这个文件自己就写着这些词」——
       那恰恰说明**它把要防的东西放在了自己扫不到的地方**，所以它从来没抓到自己。
       现在词在包外，豁免也就不需要了：**本文件自己也在扫描范围内。**
    """

    # 可以随包走的只有**结构性特征**：形状固定，不含任何具体人名 / 项目名。
    # 拼接写法规避自匹配——否则本文件会被自己扫出来（夹具把被测试的门骗响）。
    STRUCTURAL = [
        ("绝对家目录路径", "/" + r"(?:Users|home)/[A-Za-z0-9_.-]+/"),
        ("Windows 绝对路径", r"[A-Za-z]:\\{1,2}Users\\"),
        ("邮箱地址", r"[\w.+-]+@[\w-]+\.[\w.]{2,}"),
        ("中国大陆手机号", r"\b1[3-9]\d{9}\b"),
    ]

    @staticmethod
    def _私密词():
        """从**包外**文件读私密词。返回 (词表 | None, 来源说明)。"""
        for var in ("SKILLCOACH_DENYLIST", "PREFLIGHT_DENYLIST"):
            raw = os.environ.get(var)
            if not raw:
                continue
            p = Path(raw).expanduser()
            if not p.is_file():
                continue
            words = [l.strip() for l in p.read_text(encoding="utf-8").splitlines()
                     if l.strip() and not l.startswith("#")]
            return words, f"{var} → {p}"
        return None, "未设置"

    @classmethod
    def _扫(cls, root, words=None):
        if words is None:
            words, _ = cls._私密词()
        bad = []
        for p in sorted(root.rglob("*")):
            if not p.is_file() or p.suffix not in (".md", ".py", ".json", ".txt"):
                continue
            rel = p.relative_to(root).as_posix()
            for i, ln in enumerate(S.read(p).splitlines(), 1):
                for label, rx in cls.STRUCTURAL:
                    m = re.search(rx, ln)
                    if m:
                        bad.append(f"{rel}:{i} {label} → {m.group(0)}")
                for w in (words or []):
                    if w in ln:
                        bad.append(f"{rel}:{i} 命中包外黑名单词（词本身不打印）")
        return bad

    def test_无内部痕迹(self):
        bad = self._扫(ROOT)
        self.assertEqual(bad, [], "公开包里出现内部痕迹：\n  " + "\n  ".join(bad[:12]))

    def test_没设私密词表要明说没查(self):
        """「没查」不许伪装成「通过」——否则这一条本身就成了摆设。"""
        words, _ = self._私密词()
        if words is None:
            print("\n  ⚠️ 未设置包外私密词表（SKILLCOACH_DENYLIST / PREFLIGHT_DENYLIST）")
            print("     ⇒ **私密词这一类本次没有检查**。这是「没查」，不是「没问题」。")
            self.skipTest("未设置包外私密词表：这一类没查（不等于通过）")

    def test_扫描器真的抓得住(self):
        """一条永不报警的门 = 没有门。拿**已知违规**的合成样本打一次。

        ⚠️ 这正是不该用「本包里有没有内部痕迹」来证明自己的原因：
        本包**没有**痕迹是**预期结果**，而预期结果是证明不了门有效的。
        """
        # ① 结构性：合成的家目录路径（拼接写，源码里不存在可匹配的整串）
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            fake_home = "/" + "Users" + "/" + "someuser" + "/" + "notes.md"
            (tdp / "NOTES.md").write_text(f"夹具：{fake_home}\n", encoding="utf-8")
            self.assertTrue(self._扫(tdp, []), "结构性扫描一个都没抓到 ⇒ 这门是坏的")

        # ② 黑名单词：用临时词表，既不碰真词表、也不把词写进源码
        word = "ZZZ" + "-INTERNAL-DEMO"
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            (tdp / "LEAK.md").write_text(f"内部代号：{word}\n", encoding="utf-8")
            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                             encoding="utf-8") as f:
                tmp = f.name
                f.write("# 临时词表（测试用，用完即删）\n" + word + "\n")
            old = os.environ.get("SKILLCOACH_DENYLIST")
            os.environ["SKILLCOACH_DENYLIST"] = tmp
            try:
                hit = self._扫(tdp)
            finally:
                if old is None:
                    os.environ.pop("SKILLCOACH_DENYLIST", None)
                else:
                    os.environ["SKILLCOACH_DENYLIST"] = old
                os.unlink(tmp)
            self.assertTrue(any("包外黑名单词" in h for h in hit),
                            "包外黑名单词抓不住 ⇒ 这一类是摆设：" + repr(hit))


class TestSelfDiagnose(unittest.TestCase):
    """端到端：本技能自己必须无 FAIL。"""

    def test_自己无_FAIL(self):
        S.RESULT.clear()
        rc = S.diagnose(ROOT)
        fails = [r for r in S.RESULT if r["level"] == "FAIL"]
        self.assertEqual(fails, [], f"本技能自身有 FAIL：{[(r['code'], r['title']) for r in fails]}")
        self.assertEqual(rc, 0)

    def test_平台五字段齐全(self):
        """上架前必查：少一个就可能被驳回。"""
        fm = S.frontmatter(S.read(ROOT / "SKILL.md"))
        for k in ("slug", "displayName", "version", "summary", "license"):
            self.assertTrue(fm.get(k), f"平台必备字段缺失：{k}")

    def test_资源索引与真实文件一致(self):
        """清单里写的文件必须真的存在——文档污染的第一种形态。"""
        text = S.read(ROOT / "SKILL.md")
        for p in ("01-上下文工程判据", "02-八道工序与四道门", "03-反借口段写法"):
            self.assertIn(p, text, f"资源索引里没列 {p}")
            self.assertTrue((ROOT / "references" / f"{p}.md").exists(), f"{p}.md 不存在")

    def test_正文引用的脚本都存在(self):
        text = S.read(ROOT / "SKILL.md")
        self.assertIn("scripts/skillcoach.py", text)
        self.assertTrue((ROOT / "scripts" / "skillcoach.py").exists())


if __name__ == "__main__":
    # ⚠️ **末尾必须有结论行**。踩过的坑：测试里有用例会 `print()`（端到端自诊断），
    #    于是 `python tests/test_skillcoach.py | tail` 看到的是**诊断输出**，
    #    真正的 `OK` / `FAILED` 被挤到中间 —— 我因此把 2 个失败读成了「全过」。
    #    结论行必须放在**最后一个 print**，让人怎么截都截得掉。
    print(f"═══ 回归测试 · {ROOT.name} ═══\n")
    res = unittest.TextTestRunner(verbosity=2).run(
        unittest.TestLoader().loadTestsFromModule(sys.modules[__name__]))
    n_bad = len(res.failures) + len(res.errors)
    print(f"\n═══ 回归结论 ═══  运行 {res.testsRun} · 失败 {len(res.failures)} · 错误 {len(res.errors)}")
    print("✔ 全过" if not n_bad else "✘ 有失败 —— 往上翻 FAIL/ERROR 段")
    sys.exit(0 if not n_bad else 1)
