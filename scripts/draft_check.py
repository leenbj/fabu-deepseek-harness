#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Per-article gate for domain-company news drafts.

Enforces the two rules that are easy to lose in a batch:

  H1  the enablement fact — 企业全称 + 官网启用 + the full domain, verbatim —
      must sit in paragraph 1 or 2;
  H2  the title must carry the enterprise name: prefer the full name, fall back
      to the documented short name when the full name would push the title past
      the length ceiling. The name must never be dropped from the title, and the
      body must still spell out the full name.

Pure standard library, no network, no platform binding.

    python draft_check.py --dir "<project folder>" [--short-of <file> ...]
    python draft_check.py 76-途度检测-稿件.txt --full "途度(上海)检测科技有限公司" --short 途度检测 --domain tudu.网址

Exit codes: 0 = all checks pass, 1 = at least one check failed, 2 = usage error.
"""
from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

TITLE_MAX_CHARS = 30          # ceiling for the whole headline (punctuation included)
BODY_MIN, BODY_MAX = 900, 1150  # SKILL §6.1
FACT_PARA_MIN_CHARS = 100     # H1f: a paragraph carrying the fact must not be a lone announcement

DOMAIN_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]{1,30}\.网址")
FULLNAME_RE = re.compile(
    r"[\u4e00-\u9fffA-Za-z0-9()（）·]{2,30}?"
    r"(?:股份有限公司|有限责任公司|有限公司|研究院|事务所|中心|工厂|厂)"
)
SUFFIX_WORDS = ("股份有限公司", "有限责任公司", "有限公司", "公司", "研究院", "事务所", "中心", "工厂", "厂")
PAREN_RE = re.compile(r"[（(][^）)]*[）)]")
OTHER_SUFFIX_RE = re.compile(r"\.([A-Za-z]{2,10})\b")

BANNED = [
    "据表格", "写作测试", "企业资料介绍", "企业资料显示", "表格显示",
    "经营范围", "注册资本", "法定代表", "成立于", "注册地",
    "抢注", "立即注册", "快来注册", "不容错过", "注册购买",
    "在线报修", "查询订单", "订单进度", "在线预约",
    "提升排名", "排名提升", "销量第一", "全网第一", "流量显著", "成交率",
    "信任度", "业务质量", "最新", "震撼", "首选", "领先", "必选", "重磅",
]


def parse_draft(text: str):
    lines = [l for l in text.split("\n")]
    idx = next((i for i, l in enumerate(lines) if l.strip()), 0)
    raw_title = lines[idx].strip()
    title = re.sub(r"^标题\s*[:：]\s*", "", raw_title)
    body = "\n".join(lines[idx + 1:]).strip()
    paras = [p.strip() for p in body.split("\n") if p.strip()]
    return title, body, paras


def detect_full_name(text: str) -> str | None:
    """从正文里挑出最像企业全称的那个片段。

    `厂`/`中心`/`工厂` 这些后缀太通用（"主机厂""检测中心"都会命中），
    所以先在所有候选里优先取带「公司／研究院／事务所」的，再比长度；
    都没有才退回第一个候选。否则「而客户与主机厂」会被当成企业全称。
    """
    cands = [m.group(0) for m in FULLNAME_RE.finditer(text)]
    if not cands:
        return None
    strong = [c for c in cands if re.search(r"公司|研究院|事务所", c)]
    pool = strong or cands
    return max(pool, key=len)


def core_name(full: str) -> str:
    """Full name minus parenthesised parts and company suffix words."""
    s = PAREN_RE.sub("", unicodedata.normalize("NFKC", full))
    for w in SUFFIX_WORDS:
        s = s.replace(w, "")
    return s.strip()


def detect_title_name(title: str, full: str | None, short: str | None):
    """Return (matched name in title, kind) where kind is 'full'|'short'|None.

    企业全称常带城市前缀（杭州某某科技有限公司），而标题习惯用品牌尾段（某某）。
    所以除了「从左边取前缀」，还要试核心名的**任意连续片段**（长度 ≥4），越长越优先——
    否则「杭州云栖智测科技有限公司」的标题写「云栖智测…」会被误判成"标题没有企业名"。
    """
    if full and full in title:
        return full, "full"
    if short and short in title:
        return short, "short"
    if full:
        core = core_name(full)
        for n in range(len(core), 1, -1):      # longest prefix of the core name
            cand = core[:n]
            if cand in title:
                return cand, "short"
        for size in range(len(core), 2, -1):   # longest inner chunk, length >= 3
            for i in range(0, len(core) - size + 1):
                cand = core[i:i + size]
                if cand in title:
                    return cand, "short"
    return None, None


def char_len(s: str) -> int:
    return len([c for c in s if not c.isspace()])


class Result:
    def __init__(self):
        self.rows = []

    def check(self, ok, code, msg):
        self.rows.append((bool(ok), code, msg))
        return ok

    @property
    def failed(self):
        return [r for r in self.rows if not r[0]]


def check_one(path: Path, full=None, short=None, domain=None, quiet=False) -> Result:
    text = path.read_text(encoding="utf-8")
    title, body, paras = parse_draft(text)
    first2 = "".join(paras[:2])
    r = Result()

    # --- derive entities when not supplied -----------------------------------
    if not domain:
        m = DOMAIN_RE.search(first2) or DOMAIN_RE.search(body)
        domain = m.group(0) if m else None
    if not full:
        full = detect_full_name(first2) or detect_full_name(body)

    print("=" * 88)
    print(f"{path.name}")
    print(f"  标题（{char_len(title)} 字）= {title}")
    print(f"  企业全称 = {full or '（未识别，请用 --full 指定）'}")
    print(f"  域名     = {domain or '（未识别，请用 --domain 指定）'}")
    print("=" * 88)

    if not r.check(bool(full), "H1/全称", "稿件中能识别到企业全称"):
        print("  FAIL  H1/全称  无法识别企业全称，请用 --full 指定后重跑")
    if not r.check(bool(domain), "H1/域名", "稿件中能识别到 *·网址 域名"):
        print("  FAIL  H1/域名  无法识别域名，请用 --domain 指定后重跑")
    if not full or not domain:
        print(f"\n{len(r.failed)} 项失败（实体未识别，其余检查跳过）")
        return r

    # --- H1: enablement fact in paragraph 1 or 2 -----------------------------
    r.check(full in first2, "H1a", "企业全称逐字出现在第 1 或第 2 自然段")
    r.check(domain in first2, "H1b", f"完整域名「{domain}」逐字出现在第 1 或第 2 自然段")
    r.check(("官网已启用" in first2) or ("官网启用" in first2), "H1c", "第 1 或第 2 自然段含「官网启用」表述")
    dom_para = next((i + 1 for i, p in enumerate(paras) if domain in p), None)
    r.check(dom_para is not None and dom_para <= 2, "H1d",
            f"域名位于第 {dom_para} 段（要求 ≤ 2，不得拖到第 3 段及以后）")
    name_para = next((i + 1 for i, p in enumerate(paras) if full in p), None)
    r.check(name_para is not None and name_para <= 2, "H1e",
            f"企业全称位于第 {name_para} 段（要求 ≤ 2）")

    # H1f: the fact must be woven in, not a standalone announcement paragraph.
    if dom_para:
        fact_para = paras[dom_para - 1]
        p_nws = char_len(fact_para)
        p_sent = [s for s in re.split(r"[。！？]", fact_para) if s.strip()]
        r.check(len(p_sent) >= 2 and p_nws >= FACT_PARA_MIN_CHARS, "H1f",
                f"启用事实融入行文、未独立成段（承载段 {p_nws} 字 / {len(p_sent)} 句；"
                f"要求 ≥{FACT_PARA_MIN_CHARS} 字且 ≥2 句）")
        # the fact must not sit in a paragraph that is ONLY the announcement
        sole = p_nws < FACT_PARA_MIN_CHARS or len(p_sent) < 2
        r.check(not sole, "H1g",
                "启用事实所在段落另有承接内容（不是一句公告独占一段）")

    # --- H2: title must carry the enterprise name ---------------------------
    used, kind = detect_title_name(title, full, short)
    r.check(used is not None, "H2a", f"标题含企业名称（识别为{'全称' if kind == 'full' else '简称'}「{used}」）"
            if used else "标题含企业名称（全称或资料明确的简称）—— 当前未识别到")
    tlen = char_len(title)
    r.check(tlen <= TITLE_MAX_CHARS, "H2b", f"标题长度 {tlen} 字 ≤ {TITLE_MAX_CHARS} 字")

    if used:
        full_title_len = tlen - char_len(used) + char_len(full)
        should_be_full = full_title_len <= TITLE_MAX_CHARS
        if should_be_full:
            r.check(kind == "full", "H2c",
                    f"择名规则：用全称的标题为 {full_title_len} 字（≤{TITLE_MAX_CHARS}），"
                    f"应当使用全称而不是简称")
        else:
            r.check(kind == "short", "H2d",
                    f"择名规则：用全称的标题为 {full_title_len} 字（>{TITLE_MAX_CHARS}），"
                    f"应当使用简称;当前用的是{'简称' if kind == 'short' else '全称'}")
    else:
        r.check(False, "H2c/H2d", "无法判定择名规则（标题未识别到企业名称）")

    # title must not be a bare domain
    stripped = title.replace(f"“{domain}”", "").replace(domain, "").strip(" “”：:")
    r.check(bool(stripped), "H2e", "标题不是裸域名")

    # --- body-level rules ----------------------------------------------------
    other = OTHER_SUFFIX_RE.findall(text)
    r.check(not other, "H3", f"全文无其他域名后缀举例（发现：{other}）")
    nws = char_len(body)
    r.check(BODY_MIN <= nws <= BODY_MAX, "H7", f"正文 {nws} 字，区间 {BODY_MIN}–{BODY_MAX}")
    hits = [w for w in BANNED if w in text]
    r.check(not hits, "H5/H6", f"无禁用词与制作过程用语（命中：{hits}）")
    r.check(not re.search(r"记者|走访|现场看到|笔者|采访", text), "H4", "无记者署名、走访、现场、采访")
    r.check(len(paras) >= 4, "结构", f"段落数 {len(paras)}（非单段堆砌）")

    for ok, code, msg in r.rows:
        print(f"  {'ok  ' if ok else 'FAIL'}  {code:<10} {msg}")
    print(f"\n  {len(r.rows) - len(r.failed)}/{len(r.rows)} 通过，{len(r.failed)} 项失败")
    return r


def discover(folder: Path):
    out = []
    for p in sorted(folder.rglob("*.txt")):
        n = p.name
        if "稿件" not in n or any(k in n for k in ("稿外", "说明", "来源", "科普")):
            continue
        out.append(p)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Per-article gate (H1 enablement fact + H2 title naming).")
    ap.add_argument("files", nargs="*", type=Path)
    ap.add_argument("--dir", dest="folder", type=Path)
    ap.add_argument("--full", help="企业全称（单篇时使用）")
    ap.add_argument("--short", help="资料中明确的简称（单篇时使用）")
    ap.add_argument("--domain", help="对应中文域名，如 某某.网址（单篇时使用）")
    args = ap.parse_args()

    paths = discover(args.folder) if args.folder else [p for p in args.files if p.is_file()]
    if not paths:
        print("no draft found (expected file names containing 稿件)", file=sys.stderr)
        return 2

    single = len(paths) == 1
    results = []
    for p in paths:
        results.append(check_one(
            p,
            full=args.full if single else None,
            short=args.short if single else None,
            domain=args.domain if single else None,
        ))
        print()

    total_failed = sum(len(r.failed) for r in results)
    if total_failed:
        print(f"RESULT: FAIL — {total_failed} 项未通过，共 {len(paths)} 篇")
        print("Remedy: 逐条按 SKILL §6 H1/H2 修改后重跑；")
        print("        标题放不下企业全称时用简称，并精简标题其余部分——不要把企业名删掉。")
        return 1
    print(f"RESULT: PASS — {len(paths)} 篇全部达标（H1 首两段启用事实、H2 标题企业名与择名规则）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
