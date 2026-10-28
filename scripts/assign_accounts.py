# -*- coding: utf-8 -*-
"""发布账号自动分配（**一稿多平台** + **本批严格分散** + 跨历史最小负载）——契约见 references/account-assignment.md。

用户的硬规则（2026-10-28 定，优先级最高，覆盖 2026-10-27 的单平台口径）
  * **每篇都要发到搜狐号和头条号各一次**（一次任务覆盖多个媒体）；同一平台内一篇稿只投一个账号。
  * 一个「发布单元」= 案例稿 ＋ 它配对的科普稿；每个单元在**每个平台**各占 1 个账号，
    一个单元共 4 条发布（2 篇 × 2 平台）。
  * **同一批里一个账号最多接 1 个单元**，也就是"一个账号不接第二个单元"。
  * 单元数 N ≤ **单批容量**（各平台可用账号数的最小值）时，N 个单元落到每平台 N 个不同账号。
  * N > 单批容量时才开第 2 轮复用账号（用户允许），但**必须在交付说明里写明**
    哪几个账号接了 2 个单元；要禁止复用就加 `--no-wrap`（直接报错并给出缺口）。

其余口径（与旧版一致）
  * 负载 = 账号的**累计已发布篇数**（不是本批内轮询）；同一轮内挑负载最小的账号，
    因此"这一批该用哪几个账号"也不是随机的，长期看仍在收窄历史差距。
  * 并列时按账号代号升序 ⇒ 同一输入必得同一输出（可复现）。
  * 登录态失效的账号不参与；当日剩余额度不足 2 篇的账号从本轮候选集中剔除。
  * 同一平台内不重复投：绝不把同一篇（或同一对）派给同平台的两个账号；
    但**跨平台必须都发**，缺某个平台时直接报错，不降级成单平台。

用法
  python assign_accounts.py --accounts accounts.json --ledger 发布台账.jsonl --numbers 1,2,3 --only-media sohu,toutiao
  python assign_accounts.py --accounts accounts.json --numbers 1,2 --no-wrap   # 超单批容量就报错
  python assign_accounts.py --self-test
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_WRAP_NOTE = ("本批单元数 > 单批容量（各平台账号数的最小值）：第 2 轮开始复用账号。"
                     "这是用户允许的例外，**交付说明必须写明**哪些账号接了 2 个单元。")


def out(msg: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(msg)


class AssignError(Exception):
    pass


def load_accounts(path: Path) -> list[dict]:
    # utf-8-sig：Windows 的 PowerShell / 记事本导出的 JSON 常带 BOM，utf-8 会直接报错
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, list) or not data:
        raise AssignError("--accounts 必须是含至少一个账号对象的 JSON 数组")
    accts = []
    for a in data:
        if not a.get("code"):
            raise AssignError(f"账号缺 code 字段：{a!r}")
        accts.append({
            "code": str(a["code"]),
            "media": str(a.get("media") or ""),
            # 拿不到登录态就不派活：缺省视为无效
            "login_ok": bool(a.get("login_ok", False)),
            "daily_limit": a.get("daily_limit"),
            "today_published": int(a.get("today_published") or 0),
            "published_count": int(a.get("published_count") or 0),
        })
    return accts


def filter_pool(accts: list[dict], only_media: str | None,
                exclude_media: str | None) -> tuple[list[dict], list[str]]:
    """按平台筛选账号池。返回 (保留的账号, 被排除的 'code(media)' 说明列表)。

    `only_media` 与 `exclude_media` 同时给出时以 `only_media` 为准。
    被排除的账号既不参与分配，也不参与均衡统计（否则会把它算成"负载很低"而去追）。
    """
    keep = {m.strip() for m in only_media.split(",") if m.strip()} if only_media else None
    drop = {m.strip() for m in exclude_media.split(",") if m.strip()} if exclude_media else set()
    if keep is not None:
        excluded = [f"{x['code']}({x['media']})" for x in accts if x["media"] not in keep]
        kept = [x for x in accts if x["media"] in keep]
    elif drop:
        excluded = [f"{x['code']}({x['media']})" for x in accts if x["media"] in drop]
        kept = [x for x in accts if x["media"] not in drop]
    else:
        excluded, kept = [], list(accts)
    if not kept:
        raise AssignError(f"平台筛选后账号池为空（only_media={only_media!r} exclude_media={exclude_media!r}）")
    return kept, excluded


def counts_from_runs(runs_dir: Path) -> dict:
    """按账号统计「真实发布且未失败」的运行次数（读 run.json 的 accountCode/dryRun/failedStep）。

    ⚠ 已知会漏计：由 agent 手工驱动、没有走发布工具跑完流程的发布不会留下成功运行记录
    （实测 5 篇里有 2 篇如此，导致 a-2 少算 1、b-1 少算 1）。因此**台账优先**，本函数只作粗略参考。
    """
    if not runs_dir.exists():
        raise AssignError(f"运行记录目录不存在：{runs_dir}")
    counts: dict[str, int] = {}
    for rj in runs_dir.glob("*/run.json"):
        try:
            d = json.loads(rj.read_text(encoding="utf-8"))
        except Exception:
            continue
        code = d.get("accountCode")
        if not code:
            continue
        if d.get("dryRun") is True:
            continue
        if d.get("failedStep"):
            continue
        counts[str(code)] = counts.get(str(code), 0) + 1
    return counts


def counts_from_ledger(path: Path) -> dict:
    """首选来源：台账。每行一条已发布记录：{"time","number","account","media","link"}。

    台账由发布流程每次成对发布后追加两行（案例稿一行、科普稿一行），
    天然可审计，且**不依赖发布是脚本还是手工完成的**。
    """
    if not path.exists():
        raise AssignError(f"台账不存在：{path}")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    if text.startswith("["):
        recs = json.loads(text)
    else:
        recs = [json.loads(l) for l in text.splitlines() if l.strip()]
    counts: dict[str, int] = {}
    for r in recs:
        code = r.get("account")
        if code:
            counts[str(code)] = counts.get(str(code), 0) + 1
    return counts


def sort_key(x):
    s = str(x)
    return (0, int(s)) if s.isdigit() else (1, s)


def platforms_of(pool: list[dict]) -> list[str]:
    """本次要覆盖的平台（按账号池里实际存在的平台，排序后固定顺序，保证可复现）。"""
    return sorted({(a.get("media") or "").strip() for a in pool if (a.get("media") or "").strip()})


def assign(accts: list[dict], numbers: list,
           max_per_account: int | None = None,
           no_wrap: bool = False,
           platforms: list[str] | None = None) -> tuple[list[dict], list[dict], dict]:
    """分配：**每个单元在每个平台上各占一个账号**（一稿多发，但同一账号本批只接一个单元）。

    一个发布单元 = 1 篇案例稿 ＋ 配对科普稿，两篇都要发到本次覆盖的**每个平台**各一次；
    因此每个单元消耗「每个平台 1 个账号」，每个账号本批只接 1 个单元（= 2 篇：案例＋科普）。

    第 1 轮把每个平台的账号分给不同单元（挑该平台累计负载最小者，并列取代号升序）；
    只有当单元数 > 任一平台的可用账号数时才会开第 2 轮（用户允许，但必须写进交付说明）。
    `no_wrap=True` 时第 2 轮直接报错，不硬塞。`max_per_account` 是额外的本批硬上限。
    """
    pool = [a for a in accts if a.get("login_ok", False)]
    if not pool:
        raise AssignError("没有任何登录态有效的账号，拒绝分配")

    plats = platforms or platforms_of(pool)
    by_plat: dict[str, list[dict]] = {p: [a for a in pool if (a.get("media") or "").strip() == p]
                                      for p in plats}
    empty = [p for p, xs in by_plat.items() if not xs]
    if empty:
        raise AssignError(
            f"平台 {','.join(empty)} 没有任何可用账号——一稿多平台要求每个平台至少 1 个账号；"
            f"请加账号，或用 --only-media 缩小平台范围")

    load = {a["code"]: int(a.get("published_count") or 0) for a in pool}
    today = {a["code"]: int(a.get("today_published") or 0) for a in pool}
    limit = {a["code"]: a.get("daily_limit") for a in pool}
    batch = {a["code"]: 0 for a in pool}
    before = dict(load)
    # 一个单元给账号增加 2 篇（案例稿＋科普稿）
    per_unit = 2

    def usable(a: dict, used: set[str]) -> bool:
        code = a["code"]
        return (code not in used
                and (limit[code] is None
                     or today[code] + per_unit <= int(limit[code]))
                and (max_per_account is None or batch[code] < max_per_account))

    remaining = sorted(numbers, key=sort_key)
    assignment: list[dict] = []
    rounds = 0
    capacity = min(len(xs) for xs in by_plat.values())

    while remaining:
        if rounds >= 1 and no_wrap:
            raise AssignError(
                f"本批 {len(numbers)} 个单元 > 单批可严格分散的容量 {capacity} 个"
                f"（= 最少的那个平台的账号数），余下 {len(remaining)} 个单元无处分配"
                f"（--no-wrap 禁止同账号接第 2 个单元）。请加账号或减篇数，不要硬塞")
        rounds += 1
        used_this_round: set[str] = set()
        deferred: list = []

        for num in remaining:
            picks: dict[str, str] = {}
            for p in plats:
                cand = [a for a in by_plat[p] if usable(a, used_this_round)]
                if not cand:
                    break
                # 该平台本轮内负载最小者；并列取账号代号升序
                pick = min(cand, key=lambda a: (load[a["code"]], a["code"]))
                picks[p] = pick["code"]
            if len(picks) != len(plats):
                deferred.append(num)
                continue
            for p in plats:
                code = picks[p]
                used_this_round.add(code)
                load[code] += per_unit
                today[code] += per_unit
                batch[code] += 1
            assignment.append({"number": num, "accounts": picks,
                               "account": picks[plats[0]],  # 兼容旧字段：首个平台
                               "media": ",".join(plats), "round": rounds})

        if len(deferred) == len(remaining):
            cap_note = (f"本批单账号上限 {max_per_account} 已用尽"
                        if max_per_account is not None else "无账号可接")
            raise AssignError(
                f"分配第 {deferred[0]} 个单元时无可用账号"
                f"（当日限额已满／{cap_note}），停下报告（不硬塞）")
        remaining = deferred

    after = dict(load)
    reused = {c: n for c, n in batch.items() if n > 1}
    stats = {
        "before": before,
        "after": after,
        "batch": batch,
        "rounds": rounds,
        "reused": reused,
        "platforms": plats,
        "capacity": capacity,
        # 严格分散：本批每个账号最多接 1 个单元
        "distinct_accounts": len({code for x in assignment for code in x["accounts"].values()}),
        "pool_size": len(pool),
        "strict_spread": not reused,
        "per_unit_posts": per_unit * len(plats),
    }
    return assignment, pool, stats


def spread(d: dict) -> int:
    if not d:
        return 0
    return max(d.values()) - min(d.values())


def report(assignment, pool, stats, source_note: str, as_json: bool) -> int:
    before, after = stats["before"], stats["after"]
    if as_json:
        out(json.dumps({
            "assignment": assignment,
            "before": before, "after": after,
            "batch": stats["batch"],
            "rounds": stats["rounds"],
            "reused_accounts": stats["reused"],
            "distinct_accounts": stats["distinct_accounts"],
            "pool_size": stats["pool_size"],
            "strict_spread": stats["strict_spread"],
            "spread_before": spread(before), "spread_after": spread(after),
            "load_source": source_note,
        }, ensure_ascii=False, indent=2))
        return 0

    out(f"账号池（{len(pool)} 个可用，已排除登录态失效）：")
    for a in sorted(pool, key=lambda x: x["code"]):
        lim = "无限制" if a["daily_limit"] is None else f"{a['today_published']}/{a['daily_limit']} 当日"
        out(f"  {a['code']:<6} {a['media']:<8} 累计 {a['published_count']:>3} 篇    {lim}")
    out(f"\n负载来源：{source_note}")
    plats = stats.get("platforms") or []
    out(f"\n本批 {len(assignment)} 个发布单元"
        f"（1 单元 = 案例稿 ＋ 配对科普稿，**每篇都发到 {'/'.join(plats)} 各一次**）的分配：")
    for x in assignment:
        extra = "" if x["round"] == 1 else f"   ← 第 {x['round']} 轮（账号复用）"
        pairs = "  ".join(f"{p}={c}" for p, c in x["accounts"].items())
        out(f"  编号 {str(x['number']):<6} → {pairs}{extra}")
    out()
    out(f"{'账号':<8}{'分配前':>8}{'分配后':>8}{'本批':>6}")
    out("-" * 34)
    for c in sorted(after):
        out(f"{c:<8}{before[c]:>8}{after[c]:>8}{after[c] - before[c]:>6}")
    out("-" * 34)

    n_units, n_distinct = len(assignment), stats["distinct_accounts"]
    out(f"分散判定：{n_units} 个单元落到 {n_distinct} 个不同账号"
        f"（可用账号 {stats['pool_size']} 个；单批容量 {stats.get('capacity')} 个单元"
        f"＝各平台账号数的最小值）")
    if stats["strict_spread"]:
        out("  ✅ 严格分散：没有任何账号接到第 2 个单元（每账号本批只接 1 个单元）。")
    else:
        out(f"  ⚠ 有账号接到多个单元：{stats['reused']}（共 {stats['rounds']} 轮）")
        out(f"  {DEFAULT_WRAP_NOTE}")
    out(f"  本批总发布条数：{n_units} 单元 × {len(plats)} 平台 × 2 篇 = "
        f"{n_units * stats.get('per_unit_posts', 0)} 条")

    sb, sa = spread(before), spread(after)
    out(f"\n累计极差：分配前 {sb} → 分配后 {sa}")
    if sa == 0:
        out("判定：各账号累计负载完全相等。")
    elif sa <= 1:
        out("判定：极差 ≤1，已达最小（篇数不足以拉平到 0）。")
    elif sa < sb:
        out(f"判定：极差收窄 {sb}→{sa}，在收敛；继续按本规则分配即可逐步拉平。")
    elif sb == 0:
        # 分配前完全相等、分配后出现差距：这是"按批分散"的必然结果（每批只能喂一部分账号），
        # 下一批会挑负载最小的账号，长期才收敛。这里要说清，别让人误以为算法坏了。
        out(f"判定：分配前各账号完全相等，本批使极差变为 {sa}——这是按批分散的正常结果"
            f"（一批只喂一部分账号），下一批会优先喂负载最低的账号，多批之后自然拉平。")
    else:
        out(f"判定：极差未收窄（{sb}→{sa}）。通常说明本批篇数太少或账号负载本身差距过大，"
            f"不是算法问题——继续分配即可拉平。")
    return 0


# ----------------------------- 自测 -----------------------------

def _mk(code, media="sohu", load=0, **kw):
    d = {"code": code, "media": media, "login_ok": True, "published_count": load}
    d.update(kw)
    return d


def _pool12():
    """当前注册表的形状：5 个搜狐号 + 7 个头条号 = 12 个账号。"""
    sohu = ["a-1", "b-1", "c-1", "e-1", "f-1"]
    toutiao = ["a-2", "b-2", "c-2", "e-2", "f-2", "d-2", "g-2"]
    return [_mk(c, "sohu") for c in sohu] + [_mk(c, "toutiao") for c in toutiao]


def self_test() -> int:
    ok = True

    def check(name, cond, extra=""):
        nonlocal ok
        ok = ok and cond
        out(f"  [{'PASS' if cond else 'FAIL'}] {name} {extra}")

    out("=== 自测：一稿多平台 × 每个账号本批只接一个单元 ===")

    # 1) 5 搜狐 + 7 头条 → 单批容量 = 5（各平台账号数的最小值）
    a1, p1, s1 = assign(_pool12(), list(range(1, 6)))
    check("12 账号 5 单元 → 容量 5、1 轮", s1["capacity"] == 5 and s1["rounds"] == 1,
          f"capacity={s1['capacity']} rounds={s1['rounds']}")
    check("每个单元都拿到搜狐＋头条两个账号",
          all(set(x["accounts"]) == {"sohu", "toutiao"} for x in a1),
          f"{[x['accounts'] for x in a1][:2]}")
    check("5 单元 → 10 个不同账号（每平台 5 个）", s1["distinct_accounts"] == 10,
          f"distinct={s1['distinct_accounts']}")
    check("5 单元 → 无账号接第 2 个单元", s1["strict_spread"] and not s1["reused"],
          f"reused={s1['reused']}")

    # 2) 平台内的账号互不重复（同一篇不会在同平台重复投）
    sohu_codes = [x["accounts"]["sohu"] for x in a1]
    check("搜狐侧 5 个不同账号", len(set(sohu_codes)) == 5, f"{sohu_codes}")
    toutiao_codes = [x["accounts"]["toutiao"] for x in a1]
    check("头条侧 5 个不同账号", len(set(toutiao_codes)) == 5, f"{toutiao_codes}")

    # 3) 选账号按累计负载最小 + 代号升序（每个平台各自比）
    mixed = [_mk("a-1", "sohu", 3), _mk("b-1", "sohu", 0),
             _mk("a-2", "toutiao", 5), _mk("b-2", "toutiao", 0)]
    a3, _, _ = assign([dict(x) for x in mixed], [7])
    check("两平台各取负载最小者", a3[0]["accounts"] == {"sohu": "b-1", "toutiao": "b-2"},
          f"{a3[0]['accounts']}")

    # 4) 7 单元 / 容量 5 → 开第 2 轮，搜狐侧 2 个账号接 2 个单元
    a4, _, s4 = assign(_pool12(), list(range(1, 8)))
    check("7 单元 → 2 轮", s4["rounds"] == 2, f"rounds={s4['rounds']}")
    check("7 单元 → 搜狐侧 2 个账号接 2 个单元", len(s4["reused"]) == 2, f"{s4['reused']}")
    check("7 单元 → 有复用即不算严格分散", s4["strict_spread"] is False)

    # 5) --no-wrap：超容量直接报错，不硬塞
    try:
        assign(_pool12(), list(range(1, 7)), no_wrap=True)
        check("--no-wrap 超容量时报错", False)
    except AssignError as e:
        check("--no-wrap 超容量时报错", True, f"({e})")

    # 6) 10 单元 / 容量 5 → 搜狐侧每账号恰好 2 个单元
    a6, _, s6 = assign(_pool12(), list(range(1, 11)))
    sohu_batch = {c: n for c, n in s6["batch"].items()
                  if c in {"a-1", "b-1", "c-1", "e-1", "f-1"}}
    check("10 单元 → 搜狐侧每账号 2 个单元", set(sohu_batch.values()) == {2},
          f"{sorted(set(sohu_batch.values()))}")

    # 7) 可复现：同一输入两次结果一致
    r1 = [x["accounts"] for x in assign(_pool12(), list(range(1, 8)))[0]]
    r2 = [x["accounts"] for x in assign(_pool12(), list(range(1, 8)))[0]]
    check("同一输入两次分配一致", r1 == r2, f"{r1[:2]}")

    # 8) 编号不重复（每个单元只出现一次）
    check("每个编号只出现一次", len({x["number"] for x in a4}) == len(a4))

    # 9) 登录态失效者不参与
    accts3 = [{"code": "a-1", "media": "sohu", "login_ok": False},
              {"code": "b-1", "media": "sohu", "login_ok": True},
              {"code": "a-2", "media": "toutiao", "login_ok": True}]
    a9, _, _ = assign([dict(x) for x in accts3], [1])
    check("失效账号不接活", a9[0]["accounts"]["sohu"] == "b-1",
          f"{a9[0]['accounts']}")

    # 10) 当日限额：一个单元要占 2 篇，剩余额度不足 2 篇的账号本轮跳过
    accts4 = [_mk("a-1", "sohu", 0, daily_limit=5, today_published=4),
              _mk("b-1", "sohu", 0, daily_limit=5, today_published=0),
              _mk("a-2", "toutiao", 0)]
    a10, _, _ = assign([dict(x) for x in accts4], [1])
    check("额度不足 2 篇的账号跳过", a10[0]["accounts"]["sohu"] == "b-1",
          f"{a10[0]['accounts']}")

    # 11) 全部超限 → 报错而不是硬塞
    try:
        assign([_mk("a-1", "sohu", 0, daily_limit=5, today_published=9),
                _mk("a-2", "toutiao", 0)], [1])
        check("全部超限时报错", False)
    except AssignError as e:
        check("全部超限时报错", True, f"({e})")

    # 12) 历史不均时优先喂低负载账号（每平台各有冗余账号，高负载者才轮不到）
    uneven = [_mk("a-1", "sohu", 5), _mk("b-1", "sohu", 0), _mk("c-1", "sohu", 0),
              _mk("a-2", "toutiao", 0), _mk("b-2", "toutiao", 0)]
    a12, _, s12 = assign([dict(x) for x in uneven], [1, 2])
    check("历史高负载账号本批不接活", s12["batch"]["a-1"] == 0, f"{s12['batch']}")
    check("历史不均时本批仍严格分散", s12["strict_spread"] and s12["distinct_accounts"] == 4,
          f"distinct={s12['distinct_accounts']}")

    # 13) 平台筛选：被排除的平台不参与分配，也不参与均衡统计
    kept, excluded = filter_pool(
        [{"code": "a-1", "media": "sohu"}, {"code": "a-2", "media": "toutiao"},
         {"code": "c-1", "media": "csdn"}], None, "csdn")
    check("--exclude-media 排除指定平台",
          [x["code"] for x in kept] == ["a-1", "a-2"] and excluded == ["c-1(csdn)"], f"{excluded}")
    kept2, _ = filter_pool(
        [{"code": "a-1", "media": "sohu"}, {"code": "a-2", "media": "toutiao"},
         {"code": "c-1", "media": "csdn"}], "sohu,toutiao", None)
    check("--only-media 只保留指定平台", [x["code"] for x in kept2] == ["a-1", "a-2"])
    try:
        filter_pool([{"code": "c-1", "media": "csdn"}], "sohu", None)
        check("筛选后为空时报错", False)
    except AssignError:
        check("筛选后为空时报错", True)

    # 14) 本批单账号上限护栏（与轮次规则叠加）
    try:
        assign([_mk("a-1", "sohu", 0), _mk("a-2", "toutiao", 0)], [1, 2], max_per_account=1)
        check("--max-per-account 生效", False)
    except AssignError:
        check("--max-per-account 生效", True)

    # 15) 各平台只有 1 个账号：N 单元 → N 轮
    a15, _, s15 = assign([_mk("a-1", "sohu", 0), _mk("a-2", "toutiao", 0)], [1, 2, 3])
    check("单账号平台 3 单元 → 3 轮", s15["rounds"] == 3 and s15["batch"]["a-1"] == 3,
          f"rounds={s15['rounds']}")

    # 16) 缺平台账号：一稿多平台凑不齐 → 报错，不降级为单平台
    try:
        assign([_mk("a-1", "sohu", 0), _mk("a-2", "toutiao", 0)],
               [1], platforms=["sohu", "toutiao", "csdn"])
        check("缺平台账号时报错", False)
    except AssignError as e:
        check("缺平台账号时报错", True, f"({e})")

    # 17) 每单元 2 篇 × 平台数 = 一个单元的发布条数
    check("5 单元 × 2 平台 × 2 篇 = 20 条", len(a1) * s1["per_unit_posts"] == 20,
          f"{s1['per_unit_posts']} 条/单元")

    out(f"\n自测结果：{'全部通过' if ok else '存在失败项'}")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="发布账号自动分配（本批严格分散 + 跨历史最小负载）")
    ap.add_argument("--accounts", type=Path, help="账号池 JSON（见 references/account-assignment.md）")
    ap.add_argument("--numbers", help="本批发布单元编号（= 稿件编号），逗号分隔，如 1,2,3")
    ap.add_argument("--only-media", help="只保留这些平台，逗号分隔，如 sohu,toutiao")
    ap.add_argument("--exclude-media", help="排除这些平台，逗号分隔，如 csdn（与 --only-media 同时给出时以 --only-media 为准）")
    ap.add_argument("--ledger", type=Path,
                    help="【首选】台账文件（JSONL 或 JSON 数组），每行一条已发布记录；统计各账号负载")
    ap.add_argument("--from-runs", type=Path,
                    help="【次选】从发布工具运行记录统计负载（会漏计手工驱动的发布，仅作粗略参考）")
    ap.add_argument("--max-per-account", type=int, default=None,
                    help="本批同一账号最多接几个单元（默认不限，但同一轮内最多 1 个；"
                         "不想让账号接第 2 个单元就用 --no-wrap）")
    ap.add_argument("--no-wrap", action="store_true",
                    help="禁止同一账号在本批接第 2 个单元：单元数超过可用账号数时直接报错")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    ap.add_argument("--self-test", action="store_true", help="跑内置自测并退出")
    a = ap.parse_args()

    if a.self_test:
        return self_test()

    if not a.accounts or not a.numbers:
        out("需要 --accounts 与 --numbers（或 --self-test）")
        return 1

    try:
        accts = load_accounts(a.accounts)

        # 平台筛选：账号池可按平台限定；被排除的账号不参与分配，也不参与均衡统计
        accts, excluded = filter_pool(accts, a.only_media, a.exclude_media)
        if excluded:
            out(f"平台筛选：已排除 {excluded}"
                f"（only_media={a.only_media or '-'} exclude_media={a.exclude_media or '-'}）\n")

        source = "账号 JSON 里的 published_count"
        if a.ledger:
            counts = counts_from_ledger(a.ledger)
            for x in accts:
                x["published_count"] = counts.get(x["code"], 0)
            source = f"台账 {a.ledger}（每行一条已发布记录，共 {sum(counts.values())} 条）"
        elif a.from_runs:
            counts = counts_from_runs(a.from_runs)
            for x in accts:
                x["published_count"] = counts.get(x["code"], 0)
            source = (f"运行记录目录 {a.from_runs}（真实发布且未失败的次数）"
                      f"——注意：手工驱动的发布会漏计，建议改用 --ledger")
        if excluded:
            source += f"；账号池已按平台筛选，排除 {excluded}"

        numbers = [s.strip() for s in a.numbers.split(",") if s.strip()]
        assignment, pool, stats = assign(accts, numbers, a.max_per_account, a.no_wrap)
        return report(assignment, pool, stats, source, a.json)
    except AssignError as e:
        out(f"拒绝分配：{e}")
        return 2
    except Exception as e:  # noqa: BLE001
        out(f"执行失败：{type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
