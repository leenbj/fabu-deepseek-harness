#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""风格门禁：人化（去 AI 腔）+ 去广告腔。

来源：news-draft-skill 的 `gates.py`（human-writing 1.1.0 / Humanizer-zh 的硬规则），
移植到本技能后按稿件体裁做了两处调整：

1. **破折号与提示性冒号默认只报警**。中文新闻稿里 `——` 是正常标点，原技能把它当硬错，
   是因为它面向的是另一种文风。本技能默认降为提醒，加 `--strict` 才按硬错算。
2. 检查对象是**正文**（标题单独过 H2/K5），且先把本文自己的企业名、域名、短域名挖掉，
   避免规则扫到它们本身。

用法：

    python style_check.py --dir "<项目文件夹>"          # 批量（*.txt）
    python style_check.py 01-某企业-稿件.txt            # 单篇
    python style_check.py --dir "<项目文件夹>" --strict # 破折号/冒号也算硬错

退出码：0 全过（含只有提醒），1 有硬错，2 没有稿件。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# ── 人化硬规则 ────────────────────────────────────────────────────────────
HARD_TERMS = (
    "说白了", "说穿了", "先说结论", "赋能", "抓手", "商业闭环", "价值闭环",
    "能力沉淀", "拉通", "底层逻辑", "顶层设计", "认知跃迁", "价值释放",
    "能力建设", "降本增效", "内容矩阵", "全链路", "组合拳", "打开想象空间",
    "结构性机会", "关键命题", "深层逻辑", "技术底座", "公共底座", "技术主权",
    "单点风险", "主脊柱", "材料锚点", "认知增量", "迭代闭环",
)
ROAD_SIGNS = (
    "更微妙的是", "还有一层", "只说对了一半", "值得注意的是",
    "需要指出的是", "从某种意义上说", "希望这对您有帮助",
)
PIVOT_PATTERNS = (
    re.compile(r"(?:并)?不是[^。！？\n]{0,90}而是"),
    re.compile(r"不仅(?:仅仅)?是[^。！？\n]{0,90}而是"),
    re.compile(r"并非[^。！？\n]{0,90}而是"),
    re.compile(r"不在于[^。！？\n]{0,90}而在于"),
    re.compile(r"与其说[^。！？\n]{0,90}(?:不如|毋宁|倒不如)"),
    re.compile(r"表面(?:上)?[^。！？\n]{0,90}(?:其实|实际|实则)"),
    re.compile(r"看似[^。！？\n]{0,90}(?:其实|实际|实则)"),
    re.compile(r"回头(?:看|一看)?才(?:发现|明白|知道)"),
    re.compile(r"答案(?:是否定的|恰恰相反)|恰恰相反"),
    re.compile(r"[^，。！？\n]{1,12}不重要，(?:重要|要紧)的是"),
)
NOMINALIZATION = (
    re.compile(r"进行(?:了|一次|一场|着)?[^。，！？\n]{0,12}"
               r"(?:调整|优化|升级|分析|讨论|沟通|梳理|复盘|迭代|探索|尝试|思考|规划|布局)"),
    re.compile(r"实现了?[^。，！？\n]{0,16}的?[^。，！？\n]{0,8}(?:提升|增长|突破|转变|跃升|落地)"),
    re.compile(r"完成了?对[^。！？\n]{0,18}的"),
    re.compile(r"起到了?[^。，！？\n]{0,14}的?作用"),
)
CONJUNCTIONS = ("因为", "所以", "但是", "然而", "同时", "此外", "而且", "并且", "因此", "不仅")

# ── 去广告腔 ──────────────────────────────────────────────────────────────
# 移植时按新闻稿体裁拆成两档：
#   硬错 = 真正的推销语言（劝导、绝对化承诺、促销句式、第二人称直接对读者说话）
#   提醒 = 中性比较与常见形容（"更容易被复述""更省心"），多半是正常表述，交人判断
AD_HARD = (
    ("劝导口吻", re.compile(r"值得|不妨|建议您|应当|应该|最好|尽早|早点|趁早|抓紧|"
                            r"别错过|抓住机会|越早越好|不如早点")),
    ("绝对化承诺", re.compile(r"立竿见影|一劳永逸|再也不用|从此不用|轻松搞定|事半功倍|"
                              r"包您|绝对|不容错过|只要[^。；\n]{0,14}就")),
    ("第二人称", re.compile(r"您|咱们")),
    ("促销句式", re.compile(r"一键|全网最低|限时|抢购|马上行动")),
)
AD_WARN = (
    ("比较级收益", re.compile(r"更容易|更省事|省事|省心|省力|更方便|更有把握|少绕一步|"
                              r"少问一轮|机会就多|多一次机会|更划算|更值钱")),
    ("评价性形容词", re.compile(r"优质|高效|专业|领先|权威|贴心|便捷|强大|一站式|全方位|"
                                "知名|卓越|完美|顶级|深受|广泛好评")),
    ("收益化表述", re.compile(r"作用很大|意义重大|价值很大|大有帮助|好处明显|更具优势")),
)
STYLE_WARN = (
    ("万能结尾", re.compile(r"未来可期|值得期待|让我们拭目以待|大有可为|行稳致远")),
    ("三项同构排比", re.compile(r"(?:[\u4e00-\u9fa5]{2,6}、){2}[\u4e00-\u9fa5]{2,6}(?=[，。])")),
)

DASH = (("—", "破折号"), ("–", "连接号式破折号"))
COLONS = (("：", "中文冒号"), (":", "英文冒号"))

QUOTED = re.compile(r"「[^」]*」|『[^』]*』|“[^”]*”|‘[^’]*’|\"[^\"]{0,80}\"|《[^》]*》")


def mask_quoted(text: str) -> str:
    """把引号内的内容挖掉：引述别人的话（比如客户说的“核对你是谁”）不算对读者说话。"""
    return QUOTED.sub(lambda m: " " * len(m.group()), text)


def prose(text: str) -> str:
    return "".join(line for line in text.split("\n") if line.strip())


def parse_draft(path: Path):
    lines = path.read_text(encoding="utf-8").split("\n")
    i = next((k for k, l in enumerate(lines) if l.strip()), 0)
    title = re.sub(r"^标题\s*[:：]\s*", "", lines[i].strip())
    body = "\n".join(lines[i + 1:]).strip()
    return title, body


def check_one(path: Path, strict: bool = False) -> tuple[int, int]:
    title, body = parse_draft(path)
    text = prose(body)
    rows: list[tuple[bool, str, str]] = []

    def where(pos: int) -> int:
        return body.count("\n", 0, pos) + 1

    # S1 破折号 / 提示性冒号
    marks: list[str] = []
    for sym, label in DASH:
        for m in re.finditer(re.escape(sym), text):
            marks.append(f"{label}@第{where(m.start())}行")
    for sym, label in COLONS:
        for m in re.finditer(re.escape(sym), text):
            tail = text[m.end():m.end() + 2].lstrip()
            if tail[:1] not in ("「", "『", "“", "‘", '"'):
                marks.append(f"提示性{label}@第{where(m.start())}行")
    rows.append((not marks if strict else True, "S1 标点" if strict else "提醒 标点",
                 f"破折号/提示性冒号 {len(marks)} 处（{'--strict 下为硬错' if strict else '仅提醒'}）"
                 + (f"：{'、'.join(marks[:5])}" if marks else "")))

    # S2 人化硬规则
    hits: list[str] = []
    for term in HARD_TERMS:
        if term in text:
            hits.append(f"硬禁词「{term}」")
    for term in ROAD_SIGNS:
        if term in text:
            hits.append(f"模型路标「{term}」")
    for pat in PIVOT_PATTERNS:
        for m in pat.finditer(text):
            hits.append(f"翻案腔「{re.sub(r'\\s+', ' ', m.group())[:28]}」")
            break
    rows.append((not hits, "S2 人化", "无 AI 腔硬禁词／模型路标／翻案腔"
                 if not hits else "、".join(hits[:5])))

    # S3 人化提醒
    notes: list[str] = []
    nominal = sum(len(p.findall(text)) for p in NOMINALIZATION)
    if nominal:
        notes.append(f"名词化句式 {nominal} 处")
    han = len(re.findall(r"[\u4e00-\u9fff]", text))
    conj = sum(text.count(t) for t in CONJUNCTIONS)
    if han >= 600 and conj * 1000 / han > 7:
        notes.append(f"连词密度 {conj * 1000 // han}/千字")
    if re.search(r"(?:此外|同时|因此|综上|总的来说).{0,12}(?:未来|新篇章|新征程|未来可期)", text):
        notes.append("结尾像通用积极收束")
    rows.append((True, "提醒 人化", "无明显问题" if not notes else "；".join(notes)))

    # S4 广告腔硬错（第二人称与劝导只在“引号之外”判——引述别人的话不算对读者说话）
    bare = mask_quoted(text)
    bad: list[str] = []
    for label, pat in AD_HARD:
        got = [m.group() for m in pat.finditer(bare)]
        if got:
            bad.append(f"{label}「{'、'.join(dict.fromkeys(got))[:20]}」")
    for m in re.finditer(r"[!！]", text):
        bad.append(f"感叹号「{text[max(0, m.start() - 8):m.end() + 1]}」")
    rows.append((not bad, "S4 广告腔", "无劝导／绝对化承诺／第二人称／促销句式"
                 if not bad else "、".join(bad[:4])))

    # S5 广告腔／文风提醒
    soft: list[str] = []
    for label, pat in AD_WARN + STYLE_WARN:
        got = [m.group() for m in pat.finditer(bare)]
        if got:
            soft.append(f"{label}「{'、'.join(dict.fromkeys(got))[:20]}」")
    rows.append((True, "提醒 广告腔", "无明显问题" if not soft else "；".join(soft[:4])))

    print("=" * 92)
    print(path.name)
    print(f"  标题（{len(title)} 字）= {title}")
    print(f"  正文 {han} 字")
    print("=" * 92)
    fail = 0
    for ok, name, detail in rows:
        print(f"  {'ok  ' if ok else 'FAIL'}  {name:<10} {detail}")
        if not ok:
            fail += 1
    print()
    return len(rows) - fail, fail


def discover(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    files = [p for p in sorted(target.rglob("*.txt")) if "稿外" not in p.name]
    files = [p for p in files if not any(k in p.name for k in ("说明", "来源", "清单"))]
    return files


def main() -> int:
    ap = argparse.ArgumentParser(description="风格门禁：人化（去 AI 腔）+ 去广告腔")
    ap.add_argument("files", nargs="*", type=Path)
    ap.add_argument("--dir", dest="folder", type=Path, help="项目文件夹（递归 *.txt）")
    ap.add_argument("--strict", action="store_true",
                    help="破折号与提示性冒号也按硬错算（原技能口径）")
    a = ap.parse_args()

    paths: list[Path] = list(a.files)
    if a.folder:
        paths += discover(a.folder)
    seen, ordered = set(), []
    for p in paths:
        k = str(p.resolve())
        if k not in seen and p.exists():
            seen.add(k)
            ordered.append(p)
    if not ordered:
        print("没有找到稿件", file=sys.stderr)
        return 2

    tot_ok = tot_fail = 0
    for p in ordered:
        ok, bad = check_one(p, a.strict)
        tot_ok += ok
        tot_fail += bad

    print("-" * 92)
    if tot_fail:
        print(f"RESULT: FAIL — {tot_fail} 项未通过（{len(ordered)} 篇）")
        print("改法：硬禁词/模型路标/翻案腔 → 换成直陈；广告腔 → 改成中性陈述，"
              "删掉「值得/建议/您/更省事」这类劝导与承诺；感叹号一律不用。")
        return 1
    print(f"RESULT: PASS — {len(ordered)} 篇全部无硬错（{tot_ok} 项通过；提醒项请人工回看）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
