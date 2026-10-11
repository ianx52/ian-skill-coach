#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""skillcoach.py — Skill 开发教练的检查器

**它只做能机器判的事。判不了设计意图的，如实列进「人工清单」，不假装判过。**

七个模式：
    --self                 检查本技能自己（dogfooding）
    --diagnose <目录>      完整诊断（默认）
    --intake               S0 需求澄清三问 + 第四问
    --triggers             Q1 触发门：话术集锚点分析
    --security             Q4 安全：凭据 / 危险命令 / 绝对路径
    --cost <目录>          只算上下文成本
    --json                以 JSON 输出（便于接进别的流程）

退出码：0 = 无 FAIL · 1 = 有 FAIL · 2 = 用法或路径错误

判据全文见 references/01-上下文工程判据.md。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

# ── 输出小工具 ────────────────────────────────────────────────
RESULT: list = []


def rec(level: str, code: str, title: str, detail: str = "", fix: str = "") -> None:
    """level: FAIL / WARN / INFO / PASS"""
    RESULT.append({"level": level, "code": code, "title": title,
                   "detail": detail, "fix": fix})


# ⚠️ **Critical 三项**（I2 可验证 / I5 有边界 / I4 可维护）+ C1.2（负向边界）。
#    理由见 SKILL.md：这几条失败会**静默复利**——不做也不会立刻坏，所以最容易长期欠着，
#    等到发现时已经没人愿意重写。**把它们与普通 FAIL 区别对待，排序才有意义。**
CRITICAL = {"C1", "C1.2", "I2", "I4", "I5"}


def grade(results: list[dict]) -> tuple[str, str]:
    """结论等级 A/B/C/D。

    ⚠️ 此前 SKILL.md 与 README 都承诺「给出结论等级」，**而脚本根本没实现**——
    文档承诺了工具没做的事。修法是**补实现，不是删文档**（删了就少了最有用的那句话）。
    """
    fails = [r["code"] for r in results if r["level"] == "FAIL"]
    warns = [r["code"] for r in results if r["level"] == "WARN"]
    crit = [c for c in fails if c in CRITICAL]
    if crit:
        return "D", f"命中 Critical 项 {len(crit)} 条（{'、'.join(crit[:4])}）——先修这些"
    if len(fails) >= 3:
        return "D", f"FAIL {len(fails)} 条，多处缺口"
    if fails:
        return "C", f"FAIL {len(fails)} 条（非 Critical），有缺口但不动骨架"
    if len(warns) >= 3:
        return "C", f"无 FAIL，但 WARN {len(warns)} 条"
    if warns:
        return "B", f"无 FAIL，WARN {len(warns)} 条——接近工业级"
    return "A", "工业级就绪"


def emit(as_json: bool = False) -> int:
    """输出结果。

    ⚠️ **`--json` 时 stdout 必须是纯 JSON**。踩过的坑：`main()` 在调 `diagnose()` 之前
    先打了一行 `═══ 诊断：… ═══`，于是 `skillcoach.py --self --json` 的输出**不是合法 JSON**，
    喂给 json.loads 直接崩 —— 而人眼看终端完全正常，**只有脚本化调用才会发现**。
    规则：**给机器的输出里不许掺给人看的东西**；要给人看就写 stderr。
    """
    fails = [r for r in RESULT if r["level"] == "FAIL"]
    if as_json:
        print(json.dumps(RESULT, ensure_ascii=False, indent=2))
        return 1 if fails else 0

    icon = {"FAIL": "✘", "WARN": "△", "INFO": "·", "PASS": "✔"}
    order = {"FAIL": 0, "WARN": 1, "INFO": 2, "PASS": 3}
    warns = [r for r in RESULT if r["level"] == "WARN"]

    print("\n═══ 检查结果 ═══\n")
    for r in sorted(RESULT, key=lambda x: (order[x["level"]], x["code"])):
        print(f"  {icon[r['level']]} [{r['code']}] {r['title']}")
        if r["detail"]:
            print(f"      {r['detail']}")
        if r["fix"] and r["level"] in ("FAIL", "WARN"):
            print(f"      → {r['fix']}")

    n_pass = len([r for r in RESULT if r["level"] == "PASS"])
    print(f"\n═══ 汇总 ═══  FAIL {len(fails)} · WARN {len(warns)} · PASS {n_pass}")
    g, why = grade(RESULT)
    print(f"  结论等级：{g} · {why}")

    if fails:
        print("\n  ⚠ 有 FAIL —— 按上面「→」逐条处理后重跑。")
        print("  ⚠ 「没查」的项目标『未检查』，不许写成『通过』。")
    elif warns:
        print("\n  △ 只有 WARN —— 可以交付，但把这些写进交付说明。")
    else:
        print("\n  ✔ 无 FAIL、无 WARN。")
        print("  ⚠ 这只证明「没违反已知约束」，不证明「一定好用」。")
    return 1 if fails else 0


# ── 读文件 ────────────────────────────────────────────────────
def read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def frontmatter(text: str) -> dict:
    """只解析顶层标量键。嵌套键（缩进行）跳过——不跳会把 metadata.* 当顶层字段。"""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    fm = {}
    for ln in text[3:end].splitlines():
        if not ln.strip() or ln.lstrip() != ln:      # 空行 / 缩进行 → 跳过
            continue
        if ":" not in ln:
            continue
        k, _, v = ln.partition(":")
        fm[k.strip()] = v.strip().strip('"').strip("'")
    return fm


def cjk_tokens(s: str) -> int:
    """粗估 token：CJK 1 字≈1 token，拉丁词≈1.3。**这是估算，不是精确值。**"""
    cjk = len(re.findall(r"[\u3400-\u9fff\u3000-\u303f\uff00-\uffef]", s))
    latin = len(re.findall(r"[A-Za-z0-9]+", s))
    return int(cjk + latin * 1.3)


# ── C 系列：上下文工程 ────────────────────────────────────────
def check_c1(fm: dict) -> None:
    """description 是路由条件，不是功能介绍。"""
    d = fm.get("description", "")
    if not d:
        rec("FAIL", "C1", "description 缺失",
            "frontmatter 里没有 description。",
            "补上 description —— 它是唯一的触发依据，缺了就不会被触发。")
        return
    if len(d) < 40:
        rec("FAIL", "C1", f"description 过短（{len(d)} 字符）",
            "太短几乎不可能写清边界。",
            "按四问重写：何时用 / 何时不用 / 交给谁 / 典型话术。")
    else:
        rec("PASS", "C1.0", f"description 存在（{len(d)} 字符）")

    # 四问
    q1 = bool(re.search(r"(用于|适用于|Use when|使用时)", d))
    q2 = bool(re.search(r"(不适用|不用于|不要用|不处理|NOT for|Not applicable)", d))
    q3 = bool(re.search(r"(那是.{0,24}的活|交给|转给|另见|见\s*`[a-z\-]+`)", d))
    q4 = bool(re.search(r"(触发词|Triggers?|when the user (asks|says|wants))", d))

    rec("PASS" if q1 else "FAIL", "C1.1", "问 1 · 什么时候用",
        "" if q1 else "没找到「用于 / Use when」这类场景声明。",
        "写清具体场景，不是品类（❌「帮助写作」✅「给口播稿做节奏检查时用」）。")
    rec("PASS" if q2 else "FAIL", "C1.2", "问 2 · 什么时候**不用**（负向边界）",
        "" if q2 else "**这是最贵的一条**：没有负向边界 = 任何沾边的事都会触发。",
        "补「不适用：X（那是 Y 的活）」。")
    rec("PASS" if q4 else "WARN", "C1.3", "问 3 · 典型话术（触发词）",
        "" if q4 else "没找到触发词或 Use when 句式。",
        "补几条用户真会说的话，不要写书面描述。")
    rec("PASS" if q3 else "WARN", "C1.4", "问 4 · 边界外交给谁",
        "" if q3 else "没写出边界外该找谁——只说「不处理」，调用方会自己乱猜。",
        "补「那是 <另一个技能> 的活」。")


def check_c2(root: Path, text: str) -> None:
    """三层披露：细则该下沉的下沉了。"""
    lines = text.splitlines()
    body_ln = len(lines)
    refs = sorted((root / "references").glob("**/*")) if (root / "references").is_dir() else []
    refs = [p for p in refs if p.is_file()]
    n_ref = len(refs)

    rec("INFO", "C2.0", f"正文 {body_ln} 行 · references {n_ref} 份")
    if body_ln >= 100 and n_ref == 0:
        rec("FAIL", "C2.1", "正文很长但没有 references/",
            f"正文 {body_ln} 行、零份 references —— 细则几乎可以肯定没下沉。",
            "把长清单 / 模板 / 对照表移到 references/，正文只留流程与判据。")
    elif body_ln >= 100:
        ratio = body_ln / max(n_ref, 1)
        if ratio > 150:
            rec("WARN", "C2.1", "正文与 references 比例偏高",
                f"每份 reference 摊到 {ratio:.0f} 行正文（经验值 ≤150 较健康）。",
                "看看有没有该下沉的段落。")
        else:
            rec("PASS", "C2.1", f"三层结构比例健康（每份 reference 摊 {ratio:.0f} 行正文）")
    else:
        rec("PASS", "C2.1", f"正文 {body_ln} 行，短技能不需要 references")


def check_c3(text: str) -> None:
    """关键约束放在两端（Lost in the Middle）。"""
    lines = text.splitlines()
    n = len(lines)
    if n < 20:
        rec("INFO", "C3", "正文过短，跳过位置检查")
        return
    pat = re.compile(r"(铁律|红线|核心不变式|绝不|绝不允|NEVER|禁止|必须)")
    hits = [i for i, ln in enumerate(lines) if pat.search(ln)]
    if not hits:
        rec("WARN", "C3", "没找到强约束语句",
            "整篇没有「铁律 / 必须 / 禁止」这类硬约束。",
            "任何 skill 都该有一句「最不能违反的那条」。")
        return
    head, tail = n * 0.25, n * 0.75
    in_edge = [i for i in hits if i < head or i > tail]
    if not in_edge:
        rec("FAIL", "C3", "强约束全被埋在正文中部",
            f"最强约束出现在第 {hits[0]+1}–{hits[-1]+1} 行（共 {n} 行），全部落在中部 50%。"
            "模型注意力开头结尾最高、中部易被忽视。",
            "把最重要的一条挪到首屏（前 25%），收口自检放末尾（后 25%）。")
    else:
        rec("PASS", "C3", f"强约束在两端（共 {len(hits)} 处，其中 {len(in_edge)} 处在首尾 25%）")


def check_c4(text: str) -> None:
    """规则四要素：作用域 + 动作 + 例外 + 验证方式。"""
    has_verify = bool(re.search(r"(验证方式|怎么验证|怎么知道|复跑|跑一次|自查|git status)", text))
    has_except = bool(re.search(r"(例外|除非|除…外|除外|only if|except)", text))
    if has_verify and has_except:
        rec("PASS", "C4", "规则具备「例外 + 验证方式」")
    elif has_verify:
        rec("WARN", "C4", "有验证方式，但少见「例外」",
            "只写「不许做」的规则，遇到合理场景会被误伤或绕过。",
            "给高风险规则补一句「除非 <条件>」。")
    elif has_except:
        rec("WARN", "C4", "有例外，但没有验证方式",
            "**没有验证方式的规则，只能靠自觉。**",
            "每条硬规则补「怎么知道遵守了」——能被脚本抓最好。")
    else:
        rec("WARN", "C4", "规则四要素不全（缺「例外」与「验证方式」）",
            "规则容易写成越来越长的禁用词表。",
            "按「作用域 + 动作 + 例外 + 验证方式」重写关键几条。")


def check_c5(text: str) -> None:
    """Skill 里不许写动态内容（前缀稳定）。

    ⚠️ **同句否定豁免**：讲述反模式时必然会出现这些词（「❌ 不许写『当前日期』」）。
    不做豁免就会把「正在警告这条」误判成「违反了这条」——这正是本教练最反对的
    「用一种看起来客观的方式产出误报」。规则：命中词与否定标记同句 → 降级为 INFO。
    """
    pat = re.compile(r"(当前日期|今天的日期|现在是\d|每次加载时|用户名是|本次会话编号|随机)")
    neg = re.compile(r"(❌|不|别|勿|禁止|不许|避免|错|反例|警告|⚠|不要)")
    bad, negated = [], []
    for ln in text.splitlines():
        m = pat.search(ln)
        if not m:
            continue
        (negated if neg.search(ln) else bad).append(m.group(1))
    if bad:
        rec("FAIL", "C5", "正文含动态内容",
            f"命中：{'、'.join(sorted(set(bad)))}。写了它，Skill 就不再是字节稳定的前缀。",
            "把会变的东西挪到运行时状态，不写进 skill。")
    elif negated:
        rec("INFO", "C5", f"命中 {len(set(negated))} 个敏感词，但都在否定/反例语境中",
            f"（{'、'.join(sorted(set(negated)))}）——已按同句否定豁免，判为讲述反模式而非违规")
    else:
        rec("PASS", "C5", "未发现动态内容")


def check_c6(root: Path, text: str) -> None:
    """身份声明：这是什么、什么版本、什么许可。

    ⚠️ 这里查的**不是**「有没有写明抄了谁」——那是另一件事（若改编他人作品，
    许可证会另有要求，由作者自己负责）。C6 要的是更基本的一条：
    **使用方能不能判断装进来的是个什么东西。**
    一个不肯说清来路的 skill，本身就该被怀疑。
    """
    blob = text + read(root / "README.md") + read(root / "CHANGELOG.md")
    has_lic = bool(re.search(r"(许可|license|LICENSE|版权|copyright)", blob, re.I))
    has_desc = bool(re.search(r"(是什么|做什么|用于|概述|简介)", blob))
    ver = bool(Path(root / "CHANGELOG.md").exists())
    if has_lic and has_desc:
        rec("PASS", "C6", "有身份声明（做法 / 许可）")
    else:
        miss = "、".join([n for n, v in (("许可", has_lic), ("一句话说明它是做什么的", has_desc)) if not v])
        rec("WARN", "C6", "身份声明不全", f"缺：{miss}",
            "补许可与一句话说明——**skill 是别人机器上的指令，不肯说清来路的本身就可疑**。")
    rec("PASS" if ver else "FAIL", "I4", "有 CHANGELOG（可维护）",
        "" if ver else "没有 CHANGELOG —— 坏了没法对比、没法回退。",
        "建 CHANGELOG.md，每次改动记一条。")


# ── I 系列：工业级 ───────────────────────────────────────────
def check_i(root: Path, text: str) -> None:
    tests = list((root / "tests").glob("**/*")) if (root / "tests").is_dir() else []
    tests = [p for p in tests if p.is_file()]
    rec("PASS" if tests else "FAIL", "I2", "可验证（改了能知道好坏）",
        f"tests/ 下 {len(tests)} 个文件" if tests else "没有 tests/，也没有可执行的检查清单。",
        "加最小回归：哪怕只有一条断言，也比「我看过了」强。")

    # ⚠️ **不要写成字面串**。第一版是 `(不接|不做什么|不处理什么|Not for)`——
    #    于是「不处理编码转换」「不做翻译」这类**最自然的写法**全不匹配，
    #    一个边界写得很清楚的 skill 被误报「缺：不接什么」（判据 #68 家族：
    #    检查器把自己的措辞当成了通用措辞）。放宽为「不 + 动词」的通用式。
    b1 = bool(re.search(r"(不接|不适用|Not for|NOT for|不(?:做|处理|负责|涵盖|支持|替代|管|碰|涉及))",
                        text))
    b2 = bool(re.search(r"(何时停|终止条件|停止条件|什么时候停|完成定义|循环上限)", text))
    b3 = bool(re.search(r"(交给|转给|另见|那是.{0,24}的活)", text))
    ok = b1 and b2 and b3
    miss = "、".join([n for n, v in (("不接什么", b1), ("何时停", b2), ("交给谁", b3)) if not v])
    rec("PASS" if ok else "FAIL", "I5", "有边界（三条齐）",
        "" if ok else f"缺：{miss}",
        "补齐「不接什么 / 何时停 / 交给谁」——边界模糊的 skill 会越用越偏。")


# ── C7：反借口段（正文超阈值时的必填项）──────────────────────
EXCUSE_MIN_LINES = 80


def check_excuse(text: str) -> None:
    """C7 · 反借口段：正文超阈值时，必须有一段「它会怎么对自己说 + 当场驳倒」。

    ⚠️ **为什么补这一条**：SKILL.md 与 `references/02` 都写「正文 ≥80 行**必填**」，
    而脚本里**一行实现都没有** —— **文档承诺了工具没做的事**。
    这正是本工具自己的 C4：「**没有验证方式的规则，只能靠自觉**」。

    ⚠️ **机器只能判「有没有这一段」，判不了写得好不好**——把标题改个名就能绕过。
    所以它报 WARN 不报 FAIL，且必须说清自己只判了存在性
    （判据 #71：别让红灯显得比实际更亮）。
    """
    n = len(text.splitlines())      # 与 check_c2 的「正文行数」同口径，避免两个数字打架
    # ⚠️ **必须要求「段落级」出现，不能只搜词**。踩过的坑：第一版正则含「反借口」，
    #    于是一份**正文里恰好提到「关于反借口段缺失的提示」**的夹具被判成「有」——
    #    **夹具自己把门骗响了**（与 `TestNoInternalTraces` 写 `/Users/` 是同一个错：
    #    把「出现过某类字样」当成了「存在某个段落」）。
    #    现在要求：词出现在**标题行**，或存在第一人称的偷懒句式。
    found = bool(
        re.search(r"^#{1,4}[^\n]*(?:借口|驳倒|反驳)", text, re.M)
        or re.search(r"(会这么对自己说|它会对自己说|自我说服|自己找理由跳过)", text)
    )
    if n < EXCUSE_MIN_LINES:
        rec("INFO", "C7", f"正文 {n} 行 < {EXCUSE_MIN_LINES}，反借口段非必填")
    elif found:
        rec("PASS", "C7", "正文较长，且有反借口段", "（只确认存在，不判写得好不好）")
    else:
        rec("WARN", "C7", f"正文 {n} 行 ≥ {EXCUSE_MIN_LINES}，但没找到反借口段",
            "文档规定这个长度必填。机器只判「有没有」——把标题改名就能绕过，所以它不算 FAIL。",
            "补一段「它会怎么对自己说 → 为什么站不住」：至少两条、覆盖不同动因"
            "（省事 / 赶时间 / 自我安慰）。写法见 references/03。")


# ── Q4：安全 ─────────────────────────────────────────────────
# ⚠️ **行级忽略标记**。教训来自本工具第一次审真实 skill 时的**误报**：
#    对方测试夹具里有一处合成示例路径，那一行**本就自带** `# skillcheck:ignore` 标记，
#    但检查器不认标记 ⇒ 照报 FAIL。
#    修法：逐行扫，命中标记的行直接跳过。
#    理由与 Q4 自身豁免同源：**用来做标注的东西，不能反过来把标注者挡住。**
IGNORE_RX = re.compile(r"#\s*(?:skillcoach|skillcheck)\s*:\s*ignore\b\s*([A-Za-z0-9\-]*)", re.I)

# ⚠️ **文档里的反例语境**：危险命令串出现在「不要写 / 示例 / ❌」旁边时，
#    是在**教人避开它**，不是在用它。不豁免，一份「教人写安全脚本」的好文档
#    就会被自己的扫描器判违规（与 C5 的同句否定豁免同源）。
#    **只对 `.md` 生效**：代码文件里出现就是真问题，不给豁免。
NEG_CONTEXT = re.compile(r"(❌|不许|不要|不得|禁止|避免|反例|示例|例如|别写|ignore)", re.I)


def check_security(root: Path) -> None:
    # ⚠️ **扫描器自身要豁免**：它的模式串与本文档的示例路径就写在文件里，不豁免会自己命中自己。
    #    这是「用来遮挡的东西自己不能暴露在外面」的镜像问题——遮挡物至少不能挡住自己。
    SKIP = {"scripts/skillcoach.py"}
    # ⚠️ **检测面必须够宽**。踩过的坑：第一版只写 `rm\s+-rf\s+/`——**要求 `rm -rf` 后紧跟 `/`**，
    #    于是 `rm -rf ~/x` / `rm -rf ./x` / `rm -fr /x` / `rm -rf $VAR/*` **全部逃逸**，
    #    而输出照报「命中 N 处」、文案还自称覆盖「危险删除」——
    #    **红灯只亮一半，和绿灯撒谎一样坏**（判据 #71）。
    #    口径：**删除 / 覆写类命令一律命中**，不靠「目标长什么样」来赌。
    pats = [
        (r"\brm\s+(?:-[a-zA-Z]*[rR][a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*[rR])[a-zA-Z]*\s", "危险删除"),
        (r"\b(?:dd|shred)\b[^\n]*/dev/[sh]d[a-z]", "写入裸设备"),
        (r">\s*/dev/[sh]d[a-z]", "直接写裸设备"),
        (r"\b(?:chmod|chown)\s+-[a-zA-Z]*R[a-zA-Z]*\s+(?:777|a\+rwx|0?777)\s+/(?:\s|$|\*)",
         "递归放开根权限"),
        (r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:ba|z|da|k)?sh\b", "管道执行远端脚本"),
        (r"\bsk-[A-Za-z0-9]{16,}", "疑似明文 API key"),
        (r"\bAKIA[0-9A-Z]{16}\b", "疑似 AWS key"),
        (r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----", "私钥明文"),
        (r"/Users/[A-Za-z0-9_.\-]+/", "绝对用户路径"),
        (r"(?:api[_-]?key|secret[_-]?key|access[_-]?key|password|passwd|token)"
         r"\s*[:=]\s*['\"][^'\"\s$]{8,}", "硬编码凭据"),
    ]
    found, skipped, ignored, negated = [], 0, 0, 0
    for p in root.rglob("*"):
        if not p.is_file() or p.suffix not in (".py", ".sh", ".js", ".md", ".json", ".yaml", ".yml"):
            continue
        rel = p.relative_to(root).as_posix()
        if rel in SKIP:
            skipped += 1
            continue
        is_doc = p.suffix == ".md"          # 文档里举反例 ≠ 代码里真干
        for i, ln in enumerate(read(p).splitlines(), 1):
            if IGNORE_RX.search(ln):
                ignored += 1
                continue
            for rx, label in pats:
                if re.search(rx, ln, re.I):
                    if is_doc and NEG_CONTEXT.search(ln):
                        negated += 1
                        break
                    # **带上行号**：不指位置的门，效果打对折（报「有 1 处」找不到在哪）。
                    found.append(f"{rel}:{i} → {label}")
                    break
    if found:
        rec("FAIL", "Q4", f"安全扫描命中 {len(found)} 处",
            "；".join(sorted(set(found))[:6]),
            "凭据改用 ${VAR} 占位；绝对路径改相对；危险命令加确认。"
            "（若该处是测试夹具里的合成示例，在行尾加 `# skillcoach:ignore`。）")
    else:
        rec("PASS", "Q4", "安全扫描未命中（凭据 / 危险命令 / 绝对路径）")
    rec("INFO", "Q4.1", f"已豁免扫描器自身 {skipped} 个文件 · 行级忽略标记跳过 {ignored} 行 · "
                        f"文档反例语境跳过 {negated} 行；"
                        "本项只覆盖结构性模式（**不做混淆还原 / 不判逻辑本身是否危险**），"
                        "不替代人工通读")


# ── Q1：触发 ─────────────────────────────────────────────────
def check_triggers(root: Path, fm: dict) -> None:
    """Q1 触发门。

    ⚠️ **只从「触发词：」那一段提短语**——不要在整条 description 上按标点乱切。
    第一版就是那么写的，结果把「用于：从零做一个 Skill、把需求理清…」整段切成了
    「并诊断已有」「是给」「而是「会被触发吗」这种碎片，输出一堆看着热闹的噪声。
    提不出干净的短语时，**如实说提不出**，不要拿噪声凑数。
    """
    d = fm.get("description", "")
    m = re.search(r"(?:触发词|Triggers?)\s*[:：]([^。]*?)(?:。|不适用|NOT for|Not applicable|$)", d)
    seg = m.group(1) if m else ""
    cand = [w.strip(" 「」\"'") for w in re.split(r"[，,、；;]", seg) if 1 < len(w.strip()) <= 20]
    # ⚠️ **不要过滤含 "skill" 的候选**。第一版写成 `not re.search(r"(Skill|skill|用于…)")`，
    #    意图是刮掉从「用于：」段漏进来的碎片；但「触发词：做个 skill、检查这个 skill」
    #    里两条合法触发词**因此被静默丢弃**，3 条砍成 1 条，然后报「样本偏少」——
    #    **把内容问题伪装成样本量问题**，是最难查的一类（判据 52：静默丢弃比没查更坏）。
    #    现在只刮**结构标签**，不刮业务词。
    cand = [w for w in cand if not re.search(r"(用于|不适用|触发词|Use when|Not applicable)", w)]

    if not seg:
        rec("WARN", "Q1.0", "description 里没有明确的「触发词：」段",
            "触发词混在长句里，调度器更难判断该不该调用。",
            "单独起一段「触发词：a、b、c」。")
    elif len(cand) < 3:
        rec("WARN", "Q1.0", f"触发词段只提出 {len(cand)} 条",
            f"样本偏少，覆盖不了真实说法。",
            "补到 5 条以上，并至少各给一条**不该触发**的反例。")
    else:
        rec("INFO", "Q1.0", f"从「触发词：」段提取 {len(cand)} 条候选",
            "、".join(cand[:12]))
    # ⚠️ 负样本门**不在这里调**：`check_triggers` 不该读文件系统。
    #    第一版把 `check_negatives(root, d)` 塞在本函数末尾，结果任何传 Path(".") 的
    #    单元测试都会顺带读到真实 tests/triggers.json 并往 RESULT 里塞一条 FAIL。
    #    **函数名承诺的范围就是它的边界**——超纲的副作用要挪到编排层。
    #    调用点见 diagnose()。


def check_negatives(root: Path, d: str) -> None:
    """Q1.h 负样本门。

    ⚠️ **设计取舍**：脚本**判不了语义**——「这句话该不该触发」是判断题，不是匹配题。
    所以这里不假装判断，只核一件机械可查的事：
        `挡住它的那句` 必须**逐字出现在 description 里**。
    这挡住的是最坏的一类作弊：**边界只在测试里写着、description 里根本没写**——
    测试全绿，用户那边照样误触。

    此前这门只报「需要人工构造」，而且从来没人构造 ⇒ **一条永不报警的门**。
    """
    f = root / "tests" / "triggers.json"
    if not f.exists():
        rec("WARN", "Q1.h", "还没有负样本集",
            "脚本只能提候选，判不了「这句话该不该触发」。",
            "写 3–5 条**不该触发**的话术到 tests/triggers.json，"
            "每条注明 description 里挡住它的那句。")
        return
    try:
        doc = json.loads(read(f))
    except json.JSONDecodeError as e:
        rec("FAIL", "Q1.h", "tests/triggers.json 不是合法 JSON",
            str(e)[:120], "修 JSON 语法后重跑。")
        return

    # ⚠️ **不许假设全世界都用本工具的 schema。** 踩过的坑：另一个 skill 早就有自己的
    #    tests/triggers.json（键是 `positives` / `negatives`，13 条正样本），本门硬套
    #    自己的 `负样本` 键，读出 0 条 ⇒ 报 FAIL「负样本 0 条」——**纯误报**。
    #    这与 Q4 不认外部忽略标记是同一个病：**检查器把自己的约定当成了通用约定。**
    #    处置：认到的就核，认不到就**如实说认不到**，不替对方判对错。
    items = doc.get("负样本") if isinstance(doc, dict) else None
    if items is None:
        alien = (doc.get("negatives") or doc.get("负例") or []) if isinstance(doc, dict) else []
        if alien:
            rec("PASS", "Q1.h", f"负样本 {len(alien)} 条（外部格式，样本已存在）",
                "用的是 `negatives` 键，未提供「挡住它的那句」，"
                "所以本门只确认**存在性**，核不了引用的边界。",
                "想让门核到底，给每条补一个 `挡住它的那句`（须逐字取自 description）。")
        else:
            rec("WARN", "Q1.h", "有 tests/triggers.json，但没有可识别的负样本字段",
                "认「负样本」或「negatives」两种键，都没有。**不替别人判对错，跳过。**",
                "按本工具格式加 `负样本` 键；或确认该文件与触发门无关。")
        return

    bad = []
    for it in items:
        say = str(it.get("话术", "?"))[:16]
        cite = (it.get("挡住它的那句") or "").strip()
        if it.get("预期") != "不触发":
            bad.append(f"「{say}」预期应写「不触发」")
        elif not cite:
            bad.append(f"「{say}」没写「挡住它的那句」")
        elif cite not in d:
            bad.append(f"「{say}」引的「{cite[:14]}」在 description 里找不到")
    if bad or len(items) < 3:
        rec("FAIL", "Q1.h", f"负样本 {len(items)} 条，其中 {len(bad)} 条对不上 description",
            "；".join(bad[:4]) or f"条数不足：{len(items)} < 3",
            "要么把边界补进 description，要么改掉引错的那句。"
            "**不要改测试去迁就门**——那正是防敷衍性修复要防的事。")
    else:
        rec("PASS", "Q1.h", f"负样本 {len(items)} 条，边界引用逐条在 description 中核实")


# ── S0：需求澄清 ─────────────────────────────────────────────
INTAKE = """═══ S0 · 需求澄清（请逐条回答）═══

【三问定生死】
 1. 你会怎么跟它说？（你真会说的那句话，不是书面描述）
    → 这就是 description 的原料。写不出，这个 skill 永远不会被触发。

 2. 下次再用，要记得上次的事吗？
    → 要记忆 = 有状态 = Skill 层做不到，需要 Agent 承载。

 3. 它需要临场拿主意吗，还是要反过来问你？
    → 要判断 / 要追问 = Agent；规则能穷举 = Skill 就够。

【第四问 · 最容易被跳过，也最贵】
 4. 这件事的成败，卡在哪一环？
    → 答「找得全 / 筛得准」：能帮上。
    → 答「讲得好 / 判断得准」：只能给规范，给不了能力。
    → 瓶颈不在这里能帮忙的地方 → 劝退。
      含义不是「做不出来」，是「做出来也不解决他的问题」。
      ⚠️ 「成功做出来一个没用的东西」是最贵的失败——
         做不出来只损失时间；做出来了但没用，还看起来像成功。

【按需追问】
 5. 它有没有一个必须固定的身份或立场？
 6. 它绝对不该做什么？
 7. 要装到哪几家工具里用？

形态建议（三档，不是二选一）：
 纯 Skill              —— 以上 2/3 都答「否」，规则能穷举。更便宜、更通用。
 Agent-in-Skill（默认）—— 命中任意一条 Agent 特征。基线用 Skill 承载 + 可选原生增强层。
 原生 Agent 增强        —— 确实需要工具白名单 / 独立上下文 / 模型路由。
                          ⚠️ 代价：更难移植（Agent 层没有开放标准）、更贵、更容易失控。
"""


# ── 主流程 ───────────────────────────────────────────────────
def diagnose(root: Path, as_json: bool = False) -> int:
    sk = root / "SKILL.md"
    if not sk.exists():
        print(f"✘ 找不到 {sk}\n  SKILL.md 必须在包根。", file=sys.stderr)
        return 2
    text = read(sk)
    fm = frontmatter(text)
    if not fm:
        rec("FAIL", "FM", "frontmatter 缺失或格式不对",
            "SKILL.md 开头必须是 --- 包裹的 YAML。",
            "补 frontmatter：至少 description；上架还需 slug/displayName/version/summary/license。")
    else:
        # ⚠️ **两个字段的严重度不一样，不能并成一个 FAIL**：
        #    · `description` 缺失 = **FAIL**：它是**唯一的触发依据**，缺了永不触发。
        #    · `name` 缺失 = **WARN**：它是**跨工具兼容**的建议字段，
        #      **不在平台发布规范里**（平台要的是 slug/displayName/version/summary/license）。
        #    踩过的坑：第一版把两者并成一个 FAIL ⇒ **一个完全符合平台规范的包被判 D**。
        #    ⚠️ 而这个口径冲突在自测里**永远暴露不了**——本包自己有 name（判据 #70 自测盲区）。
        if not fm.get("description"):
            rec("FAIL", "FM", "frontmatter 缺 description",
                "description 是唯一的触发依据，缺了就不会被触发。",
                "补上 description。")
        else:
            rec("PASS", "FM", "frontmatter 有 description")
        if not fm.get("name"):
            rec("WARN", "FM.name", "没有 name 字段（跨工具兼容建议补，非平台硬要求）",
                "部分工具按 `name` 索引技能；**平台发布规范只要求那五个，不含 name**。",
                "补一行 `name:` 即可。**这是兼容性建议，不是上架硬要求。**")
        need_plat = [k for k in ("slug", "displayName", "version", "summary", "license") if not fm.get(k)]
        rec("WARN" if need_plat else "PASS", "FM.plat",
            "平台发布字段" + ("缺失" if need_plat else "齐全"),
            f"缺：{'、'.join(need_plat)}" if need_plat else "slug / displayName / version / summary / license 全在",
            "要上架公开平台，这五个字段缺一不可。" if need_plat else "")

    check_c1(fm)
    check_c2(root, text)
    check_c3(text)
    check_c4(text)
    check_c5(text)
    check_c6(root, text)
    check_excuse(text)
    check_i(root, text)
    check_security(root)
    check_negatives(root, fm.get("description", ""))
    check_triggers(root, fm)

    return emit(as_json)


def cost_report(root: Path) -> int:
    sk = root / "SKILL.md"
    if not sk.exists():
        print(f"✘ 找不到 {sk}", file=sys.stderr)
        return 2
    text = read(sk)
    fm = frontmatter(text)
    desc = fm.get("description", "")
    body = text.split("---", 2)[-1] if text.startswith("---") else text
    refs = [p for p in (root / "references").glob("**/*") if p.is_file()] if (root / "references").is_dir() else []
    ref_tok = sum(cjk_tokens(read(p)) for p in refs)

    print("\n═══ 上下文成本（粗估，不是精确 token 数）═══\n")
    print(f"  常驻（每轮都付）：{cjk_tokens(desc)} token   ← description，{len(desc)} 字符")
    print(f"  按需（触发时付）：{cjk_tokens(body)} token   ← SKILL.md 正文")
    print(f"  深入（用到才付）：{ref_tok} token   ← references {len(refs)} 份")
    print(f"\n  合计最坏情况：{cjk_tokens(desc) + cjk_tokens(body) + ref_tok} token")
    print("\n  ⚠ 判据：常驻部分越小越好。正文与细则该下沉的没下沉，")
    print("    会把「按需」变成「每轮」，且稀释注意力。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Skill 开发教练 · 检查器",
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", nargs="?", default=None, help="skill 目录（默认本技能自己）")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--self", action="store_true", help="检查本技能自己")
    g.add_argument("--diagnose", metavar="DIR", help="完整诊断")
    g.add_argument("--intake", action="store_true", help="S0 需求澄清问卷")
    g.add_argument("--triggers", action="store_true", help="只跑 Q1 触发门")
    g.add_argument("--security", action="store_true", help="只跑 Q4 安全扫描")
    g.add_argument("--cost", metavar="DIR", help="只算上下文成本")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    here = Path(__file__).resolve().parent.parent

    if args.intake:
        print(INTAKE)
        return 0
    if args.cost:
        return cost_report(Path(args.cost).expanduser())
    if args.triggers:
        root = Path(args.diagnose or args.target or here).expanduser()
        fm = frontmatter(read(root / "SKILL.md"))
        check_triggers(root, fm)
        # ⚠️ 负样本门也要跑。否则 `--triggers` 单跑会给一个「无负样本集」的包评 A，
        #    而 `--diagnose` 对同一个包给 WARN —— **两个入口两个口径**。
        check_negatives(root, fm.get("description", ""))
        return emit(args.json)
    if args.security:
        root = Path(args.target or here).expanduser()
        check_security(root)
        return emit(args.json)

    root = Path(args.diagnose or args.target or here).expanduser()
    if not root.is_dir():
        print(f"✘ 不是目录：{root}", file=sys.stderr)
        return 2
    if not args.json:          # ⚠️ 标题行绝不能进 stdout 的 JSON 流，见 emit() 的说明
        print(f"═══ 诊断：{root} ═══")
    return diagnose(root, args.json)


if __name__ == "__main__":
    sys.exit(main())
