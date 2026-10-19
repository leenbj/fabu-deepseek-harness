#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cross-article similarity gate for domain-company news drafts.

Reference implementation of the metric defined in
`references/similarity-metric.md`: character 5-gram Jaccard similarity over
normalised body text, with a 10% ceiling on every pair in a batch.

Pure standard library. No platform, network or third-party dependency, so the
same definition can be implemented in any environment.

    python similarity_check.py --dir "<project folder>" [--threshold 10]
    python similarity_check.py draft1.txt draft2.txt draft3.txt
    python similarity_check.py --dir "<folder>" --entities "某某有限公司" "某某.网址"

Exit codes: 0 = every pair within threshold, 1 = at least one pair over,
2 = usage / no comparable article found.
"""
from __future__ import annotations

import argparse
import itertools
import re
import sys
import unicodedata
from pathlib import Path

DEFAULT_THRESHOLD = 10.0
DEFAULT_TITLE_THRESHOLD = 15.0
NGRAM = 5
TITLE_NGRAM = 2  # titles are short: bigrams discriminate, 5-grams are too coarse

# Mandated boilerplate that every article must carry (SKILL §6 H1/H6). These are
# phrasing the skill itself forces, so they must not be counted as "similarity".
MANDATED_BOILERPLATE = (
    "官网已启用中文域名",
    "仍以企业实际公开的内容为准",
    "网站具体提供哪些功能",
    "它处理的是名称对应与信息查找的问题",
    "它解决的是名称对应与查找入口的问题",
)

DOMAIN_RE = re.compile(r"[^\s。，、；：？！“”\"'（）()]{1,30}?\.网址")
ENTITY_RE = re.compile(
    r"[\u4e00-\u9fffA-Za-z0-9()（）·]{2,30}?"
    r"(?:股份有限公司|有限责任公司|有限公司|研究院|事务所|中心|工厂|厂)"
)
# sentences whose *function* is mandated, even though the wording should vary
DISCLAIMER_HINT_RE = re.compile(r"以企业实际公开的内容为准")


def split_title_body(text: str) -> tuple[str, str]:
    lines = text.split("\n")
    title = lines[0].strip()
    body = "\n".join(lines[1:]).strip()
    return title, body


def build_drop_list(body: str, extra_entities=()) -> list[str]:
    """Entity tokens that H1/H2 force to repeat; they are proper nouns, not writing."""
    drops = set(extra_entities)
    drops.update(m.group(0) for m in DOMAIN_RE.finditer(body))
    drops.update(m.group(0) for m in ENTITY_RE.finditer(body))
    # longest first so overlapping names are removed whole
    return sorted((d for d in drops if len(d) >= 2), key=len, reverse=True)


def normalise(text: str, drop=(), strip_boilerplate=True) -> str:
    """NFKC -> drop entity tokens -> drop mandated boilerplate -> keep CJK+alnum only."""
    s = unicodedata.normalize("NFKC", text)
    for token in drop:
        s = s.replace(token, "")
    if strip_boilerplate:
        for phrase in MANDATED_BOILERPLATE:
            s = s.replace(phrase, "")
    out = []
    for ch in s:
        code = ord(ch)
        if 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF or 0xF900 <= code <= 0xFAFF:
            out.append(ch)
        elif ch.isalnum():
            out.append(ch.lower())
    return "".join(out)


def shingles(s: str, n: int = NGRAM) -> set[str]:
    if len(s) < n:
        return {s} if s else set()
    return {s[i:i + n] for i in range(len(s) - n + 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def longest_common_run(a: str, b: str) -> int:
    """Longest verbatim character run, as auxiliary evidence."""
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    best = 0
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        ai = a[i - 1]
        for j in range(1, len(b) + 1):
            if ai == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


class Article:
    def __init__(self, path: Path, extra_entities=()):
        self.path = path
        text = path.read_text(encoding="utf-8")
        self.title, self.body = split_title_body(text)
        self.drop = build_drop_list(self.body, extra_entities)
        self.norm = normalise(self.body, self.drop)              # gated metric
        self.norm_raw = normalise(self.body, self.drop, strip_boilerplate=False)
        self.grams = shingles(self.norm)
        self.grams_raw = shingles(self.norm_raw)
        self.title_norm = normalise(self.title, self.drop)
        self.title_grams = shingles(self.title_norm, TITLE_NGRAM)


def discover(folder: Path) -> list[Path]:
    """Article files only: *稿件*.txt, excluding notes/sources sidecars."""
    found = []
    for p in sorted(folder.rglob("*.txt")):
        name = p.name
        if "稿件" not in name:
            continue
        if any(k in name for k in ("稿外", "说明", "来源", "科普")):
            continue
        found.append(p)
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description="Cross-article similarity gate (5-gram Jaccard).")
    ap.add_argument("files", nargs="*", type=Path, help="article .txt files")
    ap.add_argument("--dir", dest="folder", type=Path, help="folder to scan recursively")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                    help=f"max allowed body similarity %%, default {DEFAULT_THRESHOLD}")
    ap.add_argument("--title-threshold", type=float, default=DEFAULT_TITLE_THRESHOLD,
                    help=f"max allowed title similarity %%, default {DEFAULT_TITLE_THRESHOLD}")
    ap.add_argument("--entities", nargs="*", default=[],
                    help="extra entity strings to strip (company full names, domains)")
    args = ap.parse_args()

    if args.folder:
        paths = discover(args.folder)
    else:
        paths = [p for p in args.files if p.is_file()]
    if not paths:
        print("no comparable article found (expected files whose name contains 稿件)", file=sys.stderr)
        return 2
    if len(paths) < 2:
        print(f"only {len(paths)} article found; cross-article similarity needs at least 2.")
        print("single-article checks are covered by references/editorial-checklist.md (section B).")
        return 0

    articles = [Article(p, args.entities) for p in paths]
    print(f"metric   : body = character {NGRAM}-gram Jaccard, normalised "
          f"(entity tokens + mandated boilerplate removed)")
    print(f"           title = character {TITLE_NGRAM}-gram Jaccard, same normalisation")
    print(f"threshold: body <= {args.threshold:g}%  and  title <= {args.title_threshold:g}%, every pair")
    print(f"articles : {len(articles)}\n")

    for a in articles:
        print(f"  {a.path.name:<40} body {len(a.norm):>5} chars / {len(a.grams):>5} {NGRAM}-grams"
              f" | title {len(a.title_norm):>3} chars")
    print()

    worst = (0.0, None, None)
    over = 0
    print(f"  {'pair':<72} {'body%':>7} {'ref%':>7} {'run':>4} {'title%':>7}  verdict")
    print("  " + "-" * 108)
    for a, b in itertools.combinations(articles, 2):
        sim = jaccard(a.grams, b.grams) * 100
        ref = jaccard(a.grams_raw, b.grams_raw) * 100
        run = longest_common_run(a.norm, b.norm)
        tsim = jaccard(a.title_grams, b.title_grams) * 100
        trun = longest_common_run(a.title_norm, b.title_norm)
        ok = sim <= args.threshold and tsim <= args.title_threshold
        if not ok:
            over += 1
        label = f"{a.path.name} ↔ {b.path.name}"
        if len(label) > 70:
            label = label[:67] + "..."
        reason = ""
        if sim > args.threshold:
            reason = "body OVER"
        if tsim > args.title_threshold:
            reason = (reason + " + " if reason else "") + "title OVER"
        print(f"  {label:<72} {sim:7.2f} {ref:7.2f} {run:4d} {tsim:7.2f}  "
              f"{'ok' if ok else reason}")
        if sim > worst[0]:
            worst = (sim, a, b)
    print()

    if over:
        print(f"RESULT: FAIL — {over} pair(s) over threshold.")
        print(f"Worst body pair: {worst[1].path.name} ↔ {worst[2].path.name} = {worst[0]:.2f}%")
        print("Remedy: re-angle the offending article (opening type, paragraph order, argument")
        print("path, ending) — and vary the sentence patterns. Not a synonym swap.")
        print("See references/similarity-metric.md.")
        return 1

    print(f"RESULT: PASS — worst body pair {worst[1].path.name} ↔ {worst[2].path.name} "
          f"= {worst[0]:.2f}% (ceiling {args.threshold:g}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
