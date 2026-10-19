#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""中文域名科普稿的「方向发号 + 配对台账」。

每篇企业案例稿配一篇科普稿；6 个方向**按顺序轮换**，用完回到第 1 个，
保证 6 个方向都能被用到。方向与论点的原文见 references/kepu-directions.md。

四个子命令：

    plan   发号：按台账里的 next_index 顺序发 N 个方向，可导出 plan.json（不改台账）
    commit 登记：校验科普稿确实写出来了，写进台账并推进方向指针（幂等：同一 plan 只记一次）
    links  记账：把自媒体/网页链接写进对应记录（科普稿的链接只记台账，不占表格列）
    status 查看：6 个方向各用了几次、下一个是哪个、最近记录

用法：

    python kepu_plan.py plan --count 2 --out plan.json
    python kepu_plan.py commit --plan plan.json --dir "<项目文件夹>" --company-dir "<项目文件夹>"
    python kepu_plan.py links --json links.json
    python kepu_plan.py status

台账默认在 `~/.dsh/fabu-deepseek-harness/kepu-ledger.json`，可用 `--ledger` 指定。
退出码：0 成功，1 有校验失败，2 用法错误。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_LEDGER = Path.home() / ".dsh" / "fabu-deepseek-harness" / "kepu-ledger.json"

# 方向编号 → (短键, 一句话论点)。编号即 references/kepu-directions.md 里的方向序号。
DIRECTIONS = {
    1: ("env-parity", "应用环境已与英文域名一致，AI 平台对中文域名支持更好"),
    2: ("seo-parity", "SEO 效果与英文域名一致，搜索引擎全部支持中文域名"),
    3: ("primary-domain", "AI 时代入口结构改变，中文域名适合做网站主域名"),
    4: ("memorability", "易记易传播，比又长又没逻辑的英文域名更好记"),
    5: ("email-support", "邮箱支持率已近六成，且多数中小企业不受影响"),
    6: ("pricing", "续费价格高于 .com 是品牌价值与稀缺性的正常体现"),
}


def out(msg: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(msg)


def load(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "next_index": 1, "batches": [], "records": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("version", 1)
    data.setdefault("next_index", 1)
    data.setdefault("batches", [])
    data.setdefault("records", [])
    if data["next_index"] not in DIRECTIONS:
        data["next_index"] = 1
    return data


def save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def seqs_from(data: dict, start: int, count: int) -> list[int]:
    return [start + i for i in range(count)]


# --------------------------------------------------------------------------- #
# plan
# --------------------------------------------------------------------------- #
def cmd_plan(a) -> int:
    ledger = load(a.ledger)
    seqs = seqs_from(ledger, a.start_seq, a.count)
    idx = ledger["next_index"]
    items = []
    for n, seq in enumerate(seqs):
        d = ((idx - 1 + n) % len(DIRECTIONS)) + 1
        key, point = DIRECTIONS[d]
        items.append({
            "seq": seq,
            "direction": d,
            "key": key,
            "point": point,
            "expected_prefix": f"{seq:02d}-科普-方向{d}-",
        })

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    plan = {
        "plan_version": 1,
        # 批次标识：带时间戳，保证「不同批次」不会因为都叫 plan.json 而互相顶掉
        "batch_id": f"{stamp}-d" + "".join(str(it["direction"]) for it in items),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ledger": str(a.ledger),
        "start_index": idx,
        "count": len(items),
        "items": items,
    }

    out(f"台账    : {a.ledger}")
    out(f"下一方向: 第 {idx} 方向（本轮发 {len(items)} 篇，轮换顺序如下）")
    out("")
    out(f"{'序号':<6}{'方向':<6}{'短键':<16}{'文件名前缀':<22}论点")
    out("-" * 108)
    for it in items:
        out(f"{it['seq']:<6}{it['direction']:<6}{it['key']:<16}{it['expected_prefix']:<22}{it['point']}")

    if a.out:
        a.out.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        out(f"\n发号单 → {a.out}")
        out("写完后用 commit 登记：python kepu_plan.py commit --plan " f"{a.out} --dir <项目文件夹>")
    return 0


# --------------------------------------------------------------------------- #
# commit
# --------------------------------------------------------------------------- #
def cmd_commit(a) -> int:
    plan = json.loads(a.plan.read_text(encoding="utf-8"))
    ledger = load(a.ledger)

    # 幂等键：优先用发号单里自带的 batch_id（plan 生成时带时间戳，天然不撞车）；
    # 老发号单没有这个字段时，退回「文件名 + 方向列表」——只用文件名会让
    # 不同批次的 plan.json 互相顶掉，导致方向指针推不动。
    dirs = ",".join(str(it.get("direction")) for it in plan.get("items", []))
    batch_id = a.batch_id or plan.get("batch_id") or f"{Path(a.plan).stem}[{dirs}]"
    if any(b.get("batch_id") == batch_id for b in ledger["batches"]):
        out(f"这批（{batch_id}）已经登记过，不重复推进方向指针。")
        return 0

    records, problems = [], []
    for it in plan["items"]:
        hits = sorted(a.dir.glob(it["expected_prefix"] + "*.txt"))
        if len(hits) != 1:
            problems.append(f"{it['expected_prefix']}*.txt 命中 {len(hits)} 个（要求恰好 1 个）")
            continue
        kepu = hits[0]
        company = None
        if a.company_dir:
            chits = sorted(a.company_dir.glob(f"{it['seq']:02d}-*稿件*.txt"))
            company = chits[0].name if len(chits) == 1 else None
            if len(chits) != 1:
                problems.append(f"{it['seq']:02d}-*稿件*.txt 命中 {len(chits)} 个，无法配对案例稿")
        title = ""
        for line in kepu.read_text(encoding="utf-8").splitlines():
            if line.strip():
                title = line.strip()
                break
        records.append({
            "batch_id": batch_id,
            "seq": it["seq"],
            "direction": it["direction"],
            "direction_key": it["key"],
            "kepu_file": kepu.name,
            "kepu_title": title,
            "company_file": company,
            "committed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "links": {},
        })

    if problems:
        for p in problems:
            out("  FAIL  " + p)
        out("\n未登记、未推进方向指针（要么全部对上，要么一个都不记）。")
        return 1

    ledger["batches"].append({"batch_id": batch_id,
                              "committed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                              "count": len(records)})
    ledger["records"].extend(records)
    ledger["next_index"] = ((plan["start_index"] - 1 + len(records)) % len(DIRECTIONS)) + 1
    save(a.ledger, ledger)

    out(f"已登记 {len(records)} 篇：")
    for r in records:
        pair = f" ↔ {r['company_file']}" if r["company_file"] else ""
        out(f"  {r['kepu_file']}{pair}   方向{r['direction']}（{r['direction_key']}）")
    out(f"\n方向指针推进到：第 {ledger['next_index']} 方向")
    return 0


# --------------------------------------------------------------------------- #
# links
# --------------------------------------------------------------------------- #
def cmd_links(a) -> int:
    ledger = load(a.ledger)
    entries = json.loads(a.json.read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        entries = [entries]

    by_file = {r["kepu_file"]: r for r in ledger["records"]}
    missing = [e.get("kepu_file") for e in entries if e.get("kepu_file") not in by_file]
    if missing:
        out(f"这些科普稿在台账里没有记录，先 commit：{missing}")
        return 1

    for e in entries:
        rec = by_file[e["kepu_file"]]
        rec["links"] = {k: v for k, v in e.items() if k != "kepu_file" and v}
        rec["links"]["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save(a.ledger, ledger)

    out(f"已写入 {len(entries)} 条链接记录：")
    for e in entries:
        rec = by_file[e["kepu_file"]]
        for k in ("account", "platform", "kepu_media_url", "kepu_page_url",
                  "company_media_url", "company_page_url"):
            if rec["links"].get(k):
                out(f"  {e['kepu_file']}  {k} = {rec['links'][k]}")
    return 0


# --------------------------------------------------------------------------- #
# status
# --------------------------------------------------------------------------- #
def cmd_status(a) -> int:
    ledger = load(a.ledger)
    counts = {d: 0 for d in DIRECTIONS}
    for r in ledger["records"]:
        counts[r["direction"]] = counts.get(r["direction"], 0) + 1

    out(f"台账    : {a.ledger}")
    out(f"已生成  : {len(ledger['records'])} 篇科普稿，下一方向 = 第 {ledger['next_index']} 方向")
    out("")
    out(f"{'方向':<6}{'短键':<16}{'已用':<6}论点")
    out("-" * 100)
    for d, (key, point) in DIRECTIONS.items():
        mark = " ←下一个" if d == ledger["next_index"] else ""
        out(f"{d:<6}{key:<16}{counts[d]:<6}{point}{mark}")

    unused = [d for d, c in counts.items() if c == 0]
    if unused:
        out(f"\n还没用到的方向：{unused}（继续按顺序写就能补齐）")
    if a.limit and ledger["records"]:
        out("\n最近记录：")
        for r in ledger["records"][-a.limit:]:
            lk = r.get("links", {})
            link = lk.get("kepu_media_url") or lk.get("kepu_page_url") or "(未记链接)"
            out(f"  {r['kepu_file']}  方向{r['direction']}  {link}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="中文域名科普稿：方向发号与配对台账")
    ap.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER, help=f"台账路径（默认 {DEFAULT_LEDGER}）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan", help="按顺序发号")
    p.add_argument("--count", type=int, required=True, help="本批要写几篇科普稿")
    p.add_argument("--start-seq", type=int, default=1, help="本批第一篇的序号（与案例稿前缀一致），默认 1")
    p.add_argument("--out", type=Path, help="导出 plan.json")
    p.set_defaults(func=cmd_plan)

    c = sub.add_parser("commit", help="登记科普稿并推进方向指针")
    c.add_argument("--plan", type=Path, required=True)
    c.add_argument("--dir", type=Path, required=True, help="科普稿所在目录")
    c.add_argument("--company-dir", type=Path, help="案例稿所在目录（用于配对），默认同 --dir")
    c.add_argument("--batch-id", help="批次标识，默认取 plan.json 的文件名")
    c.set_defaults(func=cmd_commit)

    l = sub.add_parser("links", help="把链接写进台账")
    l.add_argument("--json", type=Path, required=True, help="[{kepu_file, account, kepu_media_url, kepu_page_url, ...}]")
    l.set_defaults(func=cmd_links)

    s = sub.add_parser("status", help="查看方向使用情况")
    s.add_argument("--limit", type=int, default=0, help="附带显示最近 N 条记录")
    s.set_defaults(func=cmd_status)

    a = ap.parse_args()
    if a.cmd == "commit" and not a.company_dir:
        a.company_dir = a.dir
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
