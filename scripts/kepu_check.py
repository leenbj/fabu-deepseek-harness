#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""中文域名科普稿门禁：正面性、篇幅、数据密度、方向对得上。

六条硬检查（任一不过即返工）：

  K1 正面性   不得出现中文域名的负面定性（负面词表 + 正则，见 NEGATIVE）；
  K2 篇幅     正文 900–1150 中文字（含中文标点、不含空白）；
  K3 数据     至少 3 处可量化表述（百分比 / 数量 / 倍数 / 年份）；
  K4 结构     ≥ 4 个自然段；
  K5 标题     ≤ 30 字（头条号标题上限 30，科普稿要发同一平台，超了就发不出去）；
           标题必须含「中文域名」，且必须含应用／价值／有用一类用语，
           并建议写成提问式（"中文域名对企业到底有没有用？…"）；
  K6 不是案例稿  不得出现「官网已启用」这类案例稿句式，也不得写「据表格」等制作过程用语。

另有两项提示（warn，不判失败）：方向关键词是否对得上、含具名机构引用时稿外是否标注了数据来源。

用法：

    python kepu_check.py --dir "<项目文件夹>"
    python kepu_check.py 01-科普-方向1-应用环境.txt

退出码：0 全部通过，1 有未通过项，2 用法错误。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

BODY_MIN, BODY_MAX = 900, 1150
TITLE_MAX = 30
MIN_DATA_POINTS = 3
MIN_PARAS = 4

# 标题必须出现「中文域名」，并且必须带一个"价值类"用语——标题要落在
# "中文域名对企业有什么用"上，而不是只描述机制。
VALUE_WORDS = ("应用", "价值", "有用", "有没有用", "好用", "用处", "作用",
               "意义", "值得", "好处", "优势", "划算", "实用", "带来")
# 提问式（建议而非硬性）：以问号结尾，或出现疑问词 / 能不能 / 有没有 等
QUESTION_RE = re.compile(r"[？?]\s*$|为什么|怎么|如何|是否|能不能|有没有|吗|值不值|多大|在哪|几何")

# 负面表述：命中即判失败。写中文域名时这些都是"先抑后扬"的典型句式。
NEGATIVE = [
    (r"中文域名[^。；\n]{0,12}(?:不支持|无法支持|不能支持|不被支持)", "把中文域名说成不被支持"),
    (r"(?:不支持|无法|不能|难以)[^。；\n]{0,10}中文域名", "把中文域名说成无法使用"),
    (r"中文域名[^。；\n]{0,10}(?:鸡肋|智商税|没必要|不值|不划算|割韭菜|坑人|骗局|噱头)", "对中文域名的贬损性定性"),
    (r"中文域名[^。；\n]{0,10}(?:劣势|短板|缺陷|硬伤|软肋|缺点|败笔)", "把中文域名说成短板"),
    (r"中文域名[^。；\n]{0,8}(?:太贵|很贵|特别贵|贵得离谱|价格虚高)", "对中文域名价格的负面定性"),
    (r"(?:比不上|不如|替代不了|比不过)[^。；\n]{0,8}(?:英文域名|\.com|com域名)", "贬低中文域名"),
    (r"兼容性(?:差|不好|不行|堪忧)", "兼容性差"),
    (r"(?:没有优势|毫无优势|劣势明显|吃亏|落后于)", "把中文域名说成没有优势"),
]
# 只在"价格"语境里提示人工复核（方向六本来就要谈价格）
PRICE_HINT = re.compile(r"续费|价格|费用")

CASE_PHRASES = ["官网已启用", "官网启用", "据表格", "写作测试", "企业资料介绍", "稿外说明"]
PRODUCTION_HINTS = ["据表格", "写作测试", "企业资料介绍", "自检结论"]

CJK = re.compile(r"[\u4e00-\u9fff]")
ARABIC_STAT = re.compile(r"\d+(?:\.\d+)?\s*(?:%|％|万|亿|倍|家|个|年|成|位|次)?")
CN_STAT = re.compile(r"(?:百分之[一二三四五六七八九十百零]+|[一二三四五六七八九]成)")

# 方向关键词（只做提示）：编号与 references/kepu-directions.md 一致
DIRECTION_KEYWORDS = {
    1: ["应用环境", "兼容", "支持", "浏览器", "AI", "场景"],
    2: ["SEO", "搜索", "收录", "权重", "排名"],
    3: ["主域名", "入口", "流量", "AI", "域名选择"],
    4: ["记", "传播", "输入", "复述", "口碑"],
    5: ["邮箱", "邮件", "支持率"],
    6: ["续费", "价格", "稀缺", "品牌", "商标"],
}

NAMED_SOURCES = ["信通院", "中国信息通信研究院", "CNNIC", "互联网络信息中心", "中国互联网协会",
                 "ICANN", "Gartner", "IDC", "艾瑞", "QuestMobile"]


def char_len(s: str) -> int:
    return len([c for c in s if not c.isspace()])


def parse(path: Path):
    lines = path.read_text(encoding="utf-8").split("\n")
    idx = next((i for i, l in enumerate(lines) if l.strip()), None)
    if idx is None:
        raise ValueError("文件是空的")
    title = re.sub(r"^标题\s*[:：]\s*", "", lines[idx].strip())
    body = "\n".join(l for l in lines[idx + 1:] if l.strip())
    paras = [p.strip() for p in body.split("\n") if p.strip()]
    return title, body, paras


def direction_of(name: str) -> int | None:
    m = re.search(r"方向\s*([1-6])", name)
    return int(m.group(1)) if m else None


def check_one(path: Path) -> tuple[int, int]:
    title, body, paras = parse(path)
    rows: list[tuple[bool, str, str]] = []

    # K1 正面性
    hits = []
    for pat, why in NEGATIVE:
        m = re.search(pat, body)
        if m:
            hits.append(f"{why}：…{m.group(0)[:24]}…")
    rows.append((not hits, "K1 正面性",
                 "全文无中文域名负面表述" if not hits else "；".join(hits)))

    # K2 篇幅
    n = char_len(body)
    rows.append((BODY_MIN <= n <= BODY_MAX, "K2 篇幅", f"正文 {n} 字（区间 {BODY_MIN}–{BODY_MAX}）"))

    # K3 数据密度
    stats = set(ARABIC_STAT.findall(body)) | set(CN_STAT.findall(body))
    rows.append((len(stats) >= MIN_DATA_POINTS, "K3 数据",
                 f"可量化表述 {len(stats)} 处（要求 ≥{MIN_DATA_POINTS}）："
                 f"{sorted(stats)[:6]}"))

    # K4 结构
    rows.append((len(paras) >= MIN_PARAS, "K4 结构", f"自然段 {len(paras)} 个（要求 ≥{MIN_PARAS}）"))

    # K5 标题：长度 / 必须出现「中文域名」/ 必须出现价值类用语 / 以提问为主
    tlen = char_len(title)
    rows.append((tlen <= TITLE_MAX, "K5a 标题长度", f"标题 {tlen} 字（≤{TITLE_MAX}，头条号上限）"))
    has_kw = "中文域名" in title
    rows.append((has_kw, "K5b 含中文域名",
                 "标题含「中文域名」" if has_kw
                 else "标题必须出现「中文域名」四个字（不能只写“域名”“中文后缀”“.网址”）"))
    vals = [w for w in VALUE_WORDS if w in title]
    rows.append((bool(vals), "K5c 价值用语",
                 f"标题含价值类用语：{'、'.join(vals)}" if vals
                 else "标题必须含应用／价值／有用一类用语（" + "／".join(VALUE_WORDS[:8]) + "…）"))
    asks = bool(QUESTION_RE.search(title))
    rows.append((True, "提示 提问式",
                 "标题是提问式、指向价值点" if asks
                 else "建议改成提问式（「…吗？」「…有没有用？」「价值在哪？」），更贴本次改版方向"))

    # K6 不是案例稿
    bad = [w for w in CASE_PHRASES + PRODUCTION_HINTS if w in title + body]
    rows.append((not bad, "K6 非案例稿", "不含案例稿句式与制作过程用语"
                 if not bad else f"命中：{sorted(set(bad))} —— 科普稿不写企业、不写「官网已启用」"))

    # warn：方向关键词
    d = direction_of(path.name)
    if d and d in DIRECTION_KEYWORDS:
        kws = DIRECTION_KEYWORDS[d]
        got = [k for k in kws if k in body]
        if not got:
            rows.append((True, "提示 方向", f"方向{d} 的关键词一个都没出现（{kws}），"
                                           f"确认本文是否真的在讲这个方向"))

    if PRICE_HINT.search(body) and d == 6:
        rows.append((True, "提示 价格", "本文谈价格：确认全程是「价值定价」口径，没有写成「贵/不划算」"))

    print("=" * 92)
    print(path.name)
    print(f"  标题（{char_len(title)} 字）= {title}")
    print("=" * 92)
    failed = 0
    for ok, code, msg in rows:
        if not ok:
            failed += 1
        print(f"  {'ok  ' if ok else 'FAIL'}  {code:<10} {msg}")
    print(f"\n  {len(rows) - failed}/{len(rows)} 通过，{failed} 项失败")
    return len(rows) - failed, failed


def main() -> int:
    ap = argparse.ArgumentParser(description="中文域名科普稿门禁（正面性 / 篇幅 / 数据 / 结构 / 标题 / 非案例稿）")
    ap.add_argument("files", nargs="*", type=Path)
    ap.add_argument("--dir", dest="folder", type=Path, help="项目文件夹（取 *科普*.txt）")
    a = ap.parse_args()

    paths: list[Path] = []
    if a.folder:
        paths.extend(sorted(a.folder.rglob("*科普*.txt")))
    for f in a.files:
        paths.append(f)
    if not paths:
        print("没有找到科普稿（文件名需含「科普」）", file=sys.stderr)
        return 2

    total_failed = 0
    for p in paths:
        try:
            _, failed = check_one(p)
        except Exception as exc:  # noqa: BLE001
            print(f"{p.name}: 解析失败 {exc}", file=sys.stderr)
            total_failed += 1
            continue
        total_failed += failed
        print()

    if total_failed:
        print(f"RESULT: FAIL — {total_failed} 项未通过，共 {len(paths)} 篇")
        print("Remedy: 负面表述改成正面论证（见 references/kepu-directions.md 各方向的「负面禁区」）；")
        print("        篇幅/数据/段落不达标就补论证链与数据组，不要灌水；标题超 30 字就精简——")
        print("        科普稿要与案例稿发同一平台，头条号标题上限就是 30 字。")
        return 1
    print(f"RESULT: PASS — {len(paths)} 篇科普稿全部达标")
    return 0


if __name__ == "__main__":
    sys.exit(main())
