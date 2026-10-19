#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reproduce the threshold calibration quoted in references/similarity-metric.md.

Runs the shipped metric (scripts/similarity_check.py) over three deliberately
chosen corpora and prints the comparison table, so the 10% / 15% thresholds can be
re-checked rather than taken on faith:

  1. "word-swap homogeneous" — the 反例 6 pair: same skeleton, only nouns changed.
     This MUST score high; if it ever drops near 10% the metric has stopped working.
  2. "clearly different"     — an unrelated control pair. This MUST score near zero.
  3. "real output"           — optional; pass two real drafts with --real A B.

    python calibrate_metric.py
    python calibrate_metric.py --real "<draftA.txt>" "<draftB.txt>"
"""
from __future__ import annotations

import argparse
import difflib
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load_checker():
    spec = importlib.util.spec_from_file_location("similarity_check", HERE / "similarity_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- corpus 1: 反例 6 —— 同骨架，仅替换公司名与行业词 -------------------------
WORD_SWAP = {
    "homo1": """标题：云梭服饰启用"云梭.网址"，供货接洽更便捷

在服装代工订单的接洽环节，采购方常常先拿到一个品牌名，再去找对应的生产主体。云梭服饰有限公司官网已启用中文域名"云梭.网址"，让这一环节里的名称确认多了一种方式。

该公司从事针织类服装的代工生产，服务对象以区域服装品牌与批发环节的采购方为主。它处理的是名称对应与信息查找的问题，网站具体提供哪些功能，仍以企业实际公开的内容为准。

中文域名的意义正在这一层。启用之后，与日常称呼一致的中文名称本身成为一个可访问的入口。对企业来说，这是把称呼与入口对齐；对委托方来说，这是核对主体的一种方式。

一次接洽完成的标志，往往只是单据上的几个字。当这几个字与官网上的企业名称指向同一家主体，接洽环节就少了一道确认的手续。
""",
    "homo2": """标题：汇筑装潢启用"汇筑.网址"，项目对接更便捷

在室内装修项目的接洽环节，业主常常先拿到一个品牌名，再去找对应的施工主体。汇筑装潢有限公司官网已启用中文域名"汇筑.网址"，让这一环节里的名称确认多了一种方式。

该公司从事住宅与商铺的室内装修施工，服务对象以区域家装客户与商铺业主为主。它处理的是名称对应与信息查找的问题，网站具体提供哪些功能，仍以企业实际公开的内容为准。

中文域名的意义正在这一层。启用之后，与日常称呼一致的中文名称本身成为一个可访问的入口。对业主来说，这是把称呼与入口对齐；对施工方来说，这是核对主体的一种方式。

一次项目对接完成的标志，往往只是合同上的几个字。当这几个字与官网上的企业名称指向同一家主体，接洽环节就少了一道确认的手续。
""",
}

# --- corpus 2: 明确不同 —— 无关题材的对照 -----------------------------------
CONTROL = {
    "ctl1": """标题：井下支护材料的一段工序，某某矿用材料有限公司启用"矿材.网址"

矿井巷道的支护材料在井下要按批次验收，不同批次的锚杆与网片要能追到供货方。材料的力学性能检测报告，通常由具备相应能力的机构出具。

某某矿用材料有限公司官网已启用中文域名"矿材.网址"。

该公司的产品用于煤矿巷道的初期支护，客户以区域内的煤矿与工程承包单位为主，供货前一般需要提供批次检验报告。

检测报告上的机构名称与供货方名称，在验收记录里往往并列出现。名称对不上，追溯就要多绕几步。

名称与主体能对上，是井下材料验收记录里首先要解决的问题。
""",
    "ctl2": """标题：社区生鲜门店调整理货节奏，某某果蔬有限公司启用"鲜达.网址"

社区生鲜门店的理货节奏，通常跟着周边居民的作息走。早晨与傍晚是两个高峰，其余时段以补货和顺手整理为主。

某某果蔬有限公司官网已启用中文域名"鲜达.网址"。

该公司的配送范围覆盖城区内的连锁门店，配送内容以叶菜、根茎类与常见果品为主，一般按门店前一天的订货量分拣。

门店收货时核对的是配送单上的名称与数量。名称写法不一致，收货环节就要多一次确认。

配送单上的几个字能否指向同一家供货主体，是门店收货时先要看的一处。
""",
}


def score(sc, text_a: str, text_b: str):
    """Normalise two raw texts exactly as the shipped checker does."""
    import tempfile, os
    with tempfile.TemporaryDirectory() as td:
        pa = Path(td) / "a-稿件.txt"
        pb = Path(td) / "b-稿件.txt"
        pa.write_text(text_a, encoding="utf-8")
        pb.write_text(text_b, encoding="utf-8")
        A = sc.Article(pa)
        B = sc.Article(pb)
    row = {n: sc.jaccard(sc.shingles(A.norm, n), sc.shingles(B.norm, n)) * 100
           for n in (5, 8, 10, 15)}
    df = difflib.SequenceMatcher(None, A.norm, B.norm).ratio() * 100
    run = sc.longest_common_run(A.norm, B.norm)
    title = sc.jaccard(sc.shingles(A.title_norm, sc.TITLE_NGRAM),
                       sc.shingles(B.title_norm, sc.TITLE_NGRAM)) * 100
    return row, df, run, title, A, B


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", nargs=2, type=Path, metavar=("A", "B"),
                    help="two real drafts to include as the 'genuinely different' row")
    args = ap.parse_args()

    sc = load_checker()

    rows = [
        ("换词式同质（反例 6，必须高分）", WORD_SWAP["homo1"], WORD_SWAP["homo2"]),
        ("明确不同（对照，必须近零）", CONTROL["ctl1"], CONTROL["ctl2"]),
    ]
    if args.real:
        rows.insert(0, ("真正不同（真实产出）",
                        args.real[0].read_text(encoding="utf-8"),
                        args.real[1].read_text(encoding="utf-8")))

    print(f"metric normalisation: as shipped in scripts/similarity_check.py")
    print(f"thresholds          : body 5-gram <= {sc.DEFAULT_THRESHOLD:g}%  |  "
          f"title {sc.TITLE_NGRAM}-gram <= {sc.DEFAULT_TITLE_THRESHOLD:g}%")
    print("=" * 100)
    print(f"{'corpus':<34} {'5-gram':>9} {'8-gram':>9} {'10-gram':>9} {'15-gram':>9} "
          f"{'difflib':>9} {'run':>5} {'title':>8}")
    print("-" * 100)
    for label, ta, tb in rows:
        row, df, run, title, _, _ = score(sc, ta, tb)
        print(f"{label:<34} " + " ".join(f"{row[n]:8.2f}%" for n in (5, 8, 10, 15))
              + f" {df:8.2f}% {run:5d} {title:7.2f}%")

    print()
    print("Expected shape: the word-swap pair must be far ABOVE the 10% ceiling while the")
    print("control pair sits at ~0%. If the word-swap pair ever lands near 10%, the metric")
    print("has regressed and the threshold in references/similarity-metric.md needs rework.")
    print("Note difflib's ordering is unstable across corpora — that is why it is not used.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
