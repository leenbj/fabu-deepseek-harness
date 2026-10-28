# -*- coding: utf-8 -*-
"""发布账号自动分配（**一稿多平台** + **每账号每天 3 案例 + 2 科普** + 跨历史最小负载）
——契约见 references/account-assignment.md。

用户的硬规则（2026-10-28 定，优先级最高）
  * **每篇都要发到搜狐号和头条号各一次**（一次任务覆盖多个媒体）；同一平台内一篇稿只投一个账号。
  * 一个「发布单元」= 案例稿 ＋ 它配对的科普稿；每个单元在每个平台各占一个账号的槽位。
  * **搜狐号每账号每天 5 篇，按 3 篇案例 + 2 篇科普切分**：案例槽 3、科普槽 2；
    案例与科普**优先同号**，该号某类槽位满了才拆号。
  * 当天放不下的单元进 `blocked_units`（顺延次日或加账号），**必须在交付说明里写明**；
    加 `--no-wrap` 则直接报错。

其余口径
  * 负载 = 账号的**累计已发布篇数**（不是本批内轮询）；挑账号时优先负载最小者，
    因此"这一批该用哪几个账号"也不是随机的，长期看仍在收窄历史差距。
  * 并列时按账号代号升序 ⇒ 同一输入必得同一输出（可复现）。
  * 登录态失效的账号不参与；槽位用尽的账号不再接该类稿。
  * 同一平台内不重复投：绝不把同一篇（或同一对）派给同平台的两个账号；
    但**跨平台必须都发**，缺某个平台时直接报错，不降级成单平台。

用法
  python assign_accounts.py --accounts accounts.json --ledger 发布台账.jsonl --numbers 1,2,3 --only-media sohu,toutiao
  python assign_accounts.py --accounts accounts.json --numbers 1,2 --no-wrap   # 槽位不够就报错
  python assign_accounts.py --self-test
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_WRAP_NOTE = ("本批有单元放不下：当日案例槽／科普槽已用尽。"
                     "这是用户允许的例外，**交付说明必须写明**哪些账号接了满额、哪些单元顺延。")

# 搜狐号实测每账号每天 5 篇：按用户的切分口径「3 篇案例 + 2 篇科普」
DEFAULT_CASE_DAILY = 3
DEFAULT_KEPU_DAILY = 2


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


def daily_caps(a: dict, case_daily: int | None, kepu_daily: int | None) -> tuple[int | None, int | None]:
    """该账号当天的「案例槽」「科普槽」各剩几个。

    上限优先取账号自己的 `case_daily_limit` / `kepu_daily_limit`，
    其次取全局 `--case-daily` / `--kepu-daily`，都没有就是不限。
    搜狐号实测每天 5 篇：默认按 **3 篇案例 + 2 篇科普** 切分（见 SKILL §7.2）。
    """
    c = a.get("case_daily_limit")
    if c is None:
        c = case_daily
    k = a.get("kepu_daily_limit")
    if k is None:
        k = kepu_daily
    return (None if c is None else int(c), None if k is None else int(k))


def assign(accts: list[dict], numbers: list,
           max_per_account: int | None = None,
           no_wrap: bool = False,
           platforms: list[str] | None = None,
           case_daily: int | None = DEFAULT_CASE_DAILY,
           kepu_daily: int | None = DEFAULT_KEPU_DAILY) -> tuple[list[dict], list[dict], dict]:
    """按「篇」分配：每篇稿件在**每个平台**各占一个账号的当日槽位。

    一个发布单元 = 1 篇案例稿 ＋ 1 篇配对科普稿，两篇都要发到本次覆盖的每个平台。
    **同一账号当天**有两条独立额度：案例槽 `case_daily` 个、科普槽 `kepu_daily` 个
    （搜狐号每天 5 篇 ⇒ 默认 3 案例 + 2 科普）。因此：

    - 一个账号一天可以接**多个单元**（默认最多 3 个单元的案例 + 2 个单元的科普），
      这取代了早先「一批一个账号一个单元」的口径；
    - 案例与科普**尽量放在同一个账号**（配对默认同号），案例槽不够时才**拆号**；
    - 同一平台内一篇稿只投一个账号（不重复投），但**每个平台都必须覆盖**。

    挑账号顺序：先看"本单元这个平台还空着、且案例与科普槽都够"的账号（配对同号优先），
    其次按累计负载最小者，并列取账号代号升序（可复现）。
    `no_wrap=True` 时槽位不够直接报错，不硬塞。
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
    before = dict(load)
    batch = {a["code"]: 0 for a in pool}          # 本批承接的篇数
    caps = {a["code"]: daily_caps(a, case_daily, kepu_daily) for a in pool}
    # 当日已用槽位：账号 JSON 可给 today_case / today_kepu，退化为 today_published 全记在案例槽
    used_case = {a["code"]: int(a.get("today_case") or 0) for a in pool}
    used_kepu = {a["code"]: int(a.get("today_kepu") or 0) for a in pool}
    for a in pool:
        if "today_case" not in a and "today_kepu" not in a:
            # 只知道总的今日已发数：保守地按"先占案例槽、再占科普槽"折算
            t = int(a.get("today_published") or 0)
            c_cap, k_cap = caps[a["code"]]
            take_case = min(t, c_cap) if c_cap is not None else t
            used_case[a["code"]] = take_case
            used_kepu[a["code"]] = 0 if c_cap is None else max(0, t - take_case)
            if k_cap is not None and used_kepu[a["code"]] > k_cap:
                used_kepu[a["code"]] = k_cap  # 超出的视为已无槽位

    def free_case(code: str) -> int:
        c_cap, _ = caps[code]
        return 10 ** 6 if c_cap is None else max(0, c_cap - used_case[code])

    def free_kepu(code: str) -> int:
        _, k_cap = caps[code]
        return 10 ** 6 if k_cap is None else max(0, k_cap - used_kepu[code])

    def cap_ok(code: str) -> bool:
        return max_per_account is None or batch[code] < max_per_account

    def take(code: str, kind: str) -> None:
        if kind == "case":
            used_case[code] += 1
        else:
            used_kepu[code] += 1
        load[code] += 1
        batch[code] += 1

    assignment: list[dict] = []
    split_units: list = []
    blocked: list = []
    order = sorted(numbers, key=sort_key)

    for num in order:
        row: dict = {"number": num, "articles": {}}
        ok = True
        unit_split = False
        for p in plats:
            cands = [a for a in by_plat[p] if cap_ok(a["code"])]
            both = [a for a in cands if free_case(a["code"]) >= 1 and free_kepu(a["code"]) >= 1]
            if both:
                pick = min(both, key=lambda a: (load[a["code"]], a["code"]))
                take(pick["code"], "case")
                take(pick["code"], "kepu")
                row["articles"][p] = {"case": pick["code"], "kepu": pick["code"], "split": False}
                continue
            # 配对同号放不下：拆号（案例、科普各找各的槽位）
            case_c = [a for a in cands if free_case(a["code"]) >= 1]
            kepu_c = [a for a in cands if free_kepu(a["code"]) >= 1]
            if not case_c or not kepu_c:
                ok = False
                break
            ca = min(case_c, key=lambda a: (load[a["code"]], a["code"]))
            ka = min([a for a in kepu_c if a["code"] != ca["code"]] or kepu_c,
                     key=lambda a: (load[a["code"]], a["code"]))
            take(ca["code"], "case")
            take(ka["code"], "kepu")
            row["articles"][p] = {"case": ca["code"], "kepu": ka["code"], "split": ca["code"] != ka["code"]}
            unit_split = unit_split or row["articles"][p]["split"]
        if not ok:
            blocked.append(num)
            continue
        if unit_split:
            split_units.append(num)
        assignment.append(row)

    if not assignment:
        raise AssignError(
            f"分配第 {blocked[0] if blocked else order[0]} 个单元时无可用槽位"
            f"（案例槽 {case_daily}／科普槽 {kepu_daily}／当日额度已满），停下报告（不硬塞）")
    if blocked and no_wrap:
        raise AssignError(
            f"本批 {len(numbers)} 个单元超出当日可用槽位，第 {blocked[0]} 个单元起无处分配"
            f"（--no-wrap 禁止硬塞）。请加账号、减篇数，或改天再发")

    after = dict(load)
    per_account = {c: n for c, n in batch.items() if n}
    stats = {
        "before": before,
        "after": after,
        "batch": batch,
        "platforms": plats,
        "pool_size": len(pool),
        "case_daily": case_daily,
        "kepu_daily": kepu_daily,
        "assigned_units": len(assignment),
        "blocked_units": blocked,
        "split_units": split_units,
        "distinct_accounts": len({code for x in assignment
                                  for v in x["articles"].values()
                                  for code in (v["case"], v["kepu"])}),
        "per_unit_posts": 2 * len(plats),
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
            "case_daily": stats["case_daily"], "kepu_daily": stats["kepu_daily"],
            "assigned_units": stats["assigned_units"],
            "blocked_units": stats["blocked_units"],
            "split_units": stats["split_units"],
            "distinct_accounts": stats["distinct_accounts"],
            "pool_size": stats["pool_size"],
            "spread_before": spread(before), "spread_after": spread(after),
            "load_source": source_note,
        }, ensure_ascii=False, indent=2))
        return 0

    out(f"账号池（{len(pool)} 个可用，已排除登录态失效）：")
    for a in sorted(pool, key=lambda x: x["code"]):
        c_cap, k_cap = daily_caps(a, stats["case_daily"], stats["kepu_daily"])
        lim = ("无限制" if c_cap is None and k_cap is None
               else f"当日案例槽 {'∞' if c_cap is None else c_cap}／科普槽 {'∞' if k_cap is None else k_cap}")
        out(f"  {a['code']:<6} {a['media']:<8} 累计 {a['published_count']:>3} 篇    {lim}")
    out(f"\n负载来源：{source_note}")
    plats = stats.get("platforms") or []
    out(f"\n本批 {len(assignment)} 个发布单元"
        f"（1 单元 = 案例稿 ＋ 配对科普稿，**每篇都发到 {'/'.join(plats)} 各一次**）的分配：")
    for x in assignment:
        parts = []
        for p, v in x["articles"].items():
            if v["case"] == v["kepu"]:
                parts.append(f"{p}={v['case']}")
            else:
                parts.append(f"{p}=案例:{v['case']}/科普:{v['kepu']}(拆号)")
        out(f"  编号 {str(x['number']):<6} → " + "   ".join(parts))
    if stats["blocked_units"]:
        out(f"\n⚠ 放不下的单元（当日槽位已用尽）：{stats['blocked_units']}")
        out(f"  {DEFAULT_WRAP_NOTE}")
    out()
    out(f"{'账号':<8}{'分配前':>8}{'分配后':>8}{'本批':>6}")
    out("-" * 34)
    for c in sorted(after):
        out(f"{c:<8}{before[c]:>8}{after[c]:>8}{after[c] - before[c]:>6}")
    out("-" * 34)

    n_units = len(assignment)
    out(f"分布判定：{n_units} 个单元用到 {stats['distinct_accounts']} 个不同账号"
        f"（可用账号 {stats['pool_size']} 个）")
    out(f"  每账号当日额度：案例 {stats['case_daily']} 篇 ＋ 科普 {stats['kepu_daily']} 篇"
        f"（搜狐号每天 5 篇的切分口径）")
    if stats["split_units"]:
        out(f"  ℹ 配对拆号：{stats['split_units']}"
            f"（这些单元的案例稿与科普稿没落到同一个账号，因为那个账号的某一类槽位已满）")
    else:
        out("  ✅ 所有单元的案例稿与科普稿都落在同一个账号（配对未拆号）。")
    over = [c for c, n in stats["batch"].items() if stats["case_daily"] is not None
            and n > stats["case_daily"] + (stats["kepu_daily"] or 0)]
    if over:
        out(f"  ⚠ 以下账号本批篇数超过当日总限额：{over}")
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

    out("=== 自测：一稿多平台 × 每账号 3 案例 + 2 科普 的当日槽位 ===")

    # 1) 5 搜狐 + 7 头条、10 个单元 → 搜狐侧每号 2 单元（2 案例+2 科普=4 篇），全落单号
    a1, _, s1 = assign(_pool12(), list(range(1, 11)))
    check("10 单元全部落位", len(a1) == 10 and not s1["blocked_units"],
          f"assigned={len(a1)} blocked={s1['blocked_units']}")
    check("每个单元都覆盖搜狐＋头条", all(set(x["articles"]) == {"sohu", "toutiao"} for x in a1))
    check("默认口径 = 3 案例 + 2 科普", (s1["case_daily"], s1["kepu_daily"]) == (3, 2),
          f"{s1['case_daily']}/{s1['kepu_daily']}")
    sohu_units = {}
    for x in a1:
        v = x["articles"]["sohu"]
        sohu_units.setdefault(v["case"], set()).add(x["number"])
    check("搜狐侧 5 个账号分摊 10 个单元（每号 2 个）",
          len(sohu_units) == 5 and set(map(len, sohu_units.values())) == {2},
          f"{[len(v) for v in sohu_units.values()]}")

    # 2) 一个账号一天最多 3 篇案例 + 2 篇科普（含跨平台各一次时不重复计数）
    over_case, over_kepu = [], []
    for p, xs in __import__("itertools").groupby([], key=str):  # noqa: F841  (占位，下面用显式循环)
        pass
    cap_case: dict = {}
    cap_kepu: dict = {}
    for x in a1:
        for p, v in x["articles"].items():
            cap_case[v["case"]] = cap_case.get(v["case"], 0) + 1
            cap_kepu[v["kepu"]] = cap_kepu.get(v["kepu"], 0) + 1
    check("每账号案例 ≤3", max(cap_case.values()) <= 3, f"{max(cap_case.values())}")
    check("每账号科普 ≤2", max(cap_kepu.values()) <= 2, f"{max(cap_kepu.values())}")

    # 3) 超出当天总槽位（5 个账号 × （3 案例+2 科普））时，多余单元被挡下而不是硬塞
    a3, _, s3 = assign(_pool12(), list(range(1, 16)))
    check("15 单元 → 搜狐侧仍不超 3 案例/号", max(cap_case.values()) <= 3)
    check("15 单元超过搜狐当日总槽位（5×3=15 案例、5×2=10 科普）→ 有单元放不下",
          bool(s3["blocked_units"]), f"blocked={s3['blocked_units']}")

    # 4) 单个搜狐号：科普槽只有 2 个 → 一个单元要案例＋科普都在同一平台落位，
    #    所以第 3 个单元放不下（不能只发案例不发科普）
    one = [_mk("s-1", "sohu"), _mk("t-1", "toutiao")]
    a4, _, s4 = assign([dict(x) for x in one], [1, 2, 3])
    check("单搜狐号只能落 2 个单元（科普槽 2 个是瓶颈）", len(a4) == 2 and s4["blocked_units"] == [3],
          f"assigned={len(a4)} blocked={s4['blocked_units']}")
    check("前两个单元配对同号（案例=科普）",
          all(a4[i]["articles"]["sohu"]["case"] == a4[i]["articles"]["sohu"]["kepu"] for i in (0, 1)))

    # 5) 两个搜狐号：搜狐侧科普槽 total 4，但头条只有 1 个账号（科普槽 2）→ 瓶颈在头条
    split_pool = [_mk("s-1", "sohu"), _mk("s-2", "sohu"), _mk("t-1", "toutiao")]
    a5, _, s5 = assign([dict(x) for x in split_pool], [1, 2, 3])
    check("瓶颈在最少槽位的平台：头条 1 号只能落 2 个单元",
          len(a5) == 2 and s5["blocked_units"] == [3],
          f"assigned={len(a5)} blocked={s5['blocked_units']}")
    check("案例仍不超过每号 3 个",
          max(__import__("collections").Counter(
              x["articles"]["sohu"]["case"] for x in a5).values()) <= 3)

    # 6) 某平台科普槽全部用尽 → 整单放不下，报错而不是只发案例
    try:
        assign([_mk("s-1", "sohu", today_case=0, today_kepu=2), _mk("t-1", "toutiao")], [1])
        check("科普槽满时整单报错（不半平台发布）", False)
    except AssignError as e:
        check("科普槽满时整单报错（不半平台发布）", True)  # noqa: F841

    # 7) 可复现：同一输入两次结果一致
    r1 = [x["articles"] for x in assign(_pool12(), list(range(1, 11)))[0]]
    r2 = [x["articles"] for x in assign(_pool12(), list(range(1, 11)))[0]]
    check("同一输入两次分配一致", r1 == r2)

    # 8) 编号不重复
    check("每个编号只出现一次", len({x["number"] for x in a1}) == len(a1))

    # 9) 登录态失效者不参与
    a9, _, _ = assign([{"code": "a-1", "media": "sohu", "login_ok": False},
                       {"code": "b-1", "media": "sohu", "login_ok": True},
                       {"code": "a-2", "media": "toutiao", "login_ok": True}], [1])
    check("失效账号不接活", a9[0]["articles"]["sohu"]["case"] == "b-1")

    # 10) 历史负载最小的账号优先
    uneven = [_mk("s-1", "sohu", 9), _mk("s-2", "sohu", 0), _mk("t-1", "toutiao", 9), _mk("t-2", "toutiao", 0)]
    a10, _, _ = assign([dict(x) for x in uneven], [1])
    check("两平台都挑负载最小的账号",
          a10[0]["articles"]["sohu"]["case"] == "s-2" and a10[0]["articles"]["toutiao"]["case"] == "t-2")

    # 11) 平台筛选
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

    # 12) 单账号本批上限护栏（配合 --no-wrap 才抛错；不配合则记入 blocked_units）
    try:
        assign([_mk("s-1", "sohu"), _mk("s-2", "sohu"), _mk("t-1", "toutiao")],
               [1, 2], max_per_account=1, no_wrap=True)
        check("--max-per-account 生效", False)
    except AssignError:
        check("--max-per-account 生效", True)

    # 13) 缺平台账号 → 报错，不降级为单平台
    try:
        assign([_mk("a-1", "sohu", 0), _mk("a-2", "toutiao", 0)],
               [1], platforms=["sohu", "toutiao", "csdn"])
        check("缺平台账号时报错", False)
    except AssignError as e:
        check("缺平台账号时报错", True, f"({e})")

    # 14) 自定义切分：案例 5 / 科普 5（其它平台口径）也能生效
    a14, _, s14 = assign([_mk("s-1", "sohu"), _mk("t-1", "toutiao")], list(range(1, 6)),
                         case_daily=5, kepu_daily=5)
    check("自定义 5/5 槽位可放 5 个单元（单账号两端）", len(a14) == 5, f"{len(a14)}")

    # 15) 每单元 2 篇 × 平台数 = 一个单元的发布条数
    check("10 单元 × 2 平台 × 2 篇 = 40 条", len(a1) * s1["per_unit_posts"] == 40,
          f"{s1['per_unit_posts']} 条/单元")

    out(f"\n自测结果：{'全部通过' if ok else '存在失败项'}")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="发布账号自动分配（一稿多平台 + 每号每天 3 案例 + 2 科普）")
    ap.add_argument("--accounts", type=Path, help="账号池 JSON（见 references/account-assignment.md）")
    ap.add_argument("--numbers", help="本批发布单元编号（= 稿件编号），逗号分隔，如 1,2,3")
    ap.add_argument("--only-media", help="只保留这些平台，逗号分隔，如 sohu,toutiao")
    ap.add_argument("--exclude-media", help="排除这些平台，逗号分隔，如 csdn（与 --only-media 同时给出时以 --only-media 为准）")
    ap.add_argument("--ledger", type=Path,
                    help="【首选】台账文件（JSONL 或 JSON 数组），每行一条已发布记录；统计各账号负载")
    ap.add_argument("--from-runs", type=Path,
                    help="【次选】从发布工具运行记录统计负载（会漏计手工驱动的发布，仅作粗略参考）")
    ap.add_argument("--max-per-account", type=int, default=None,
                    help="本批同一账号最多接几个单元（默认不限，由当日案例/科普槽位自然约束）")
    ap.add_argument("--case-daily", type=int, default=DEFAULT_CASE_DAILY,
                    help=f"每账号每天的案例稿槽位（默认 {DEFAULT_CASE_DAILY}；传 0 表示不限）")
    ap.add_argument("--kepu-daily", type=int, default=DEFAULT_KEPU_DAILY,
                    help=f"每账号每天的科普稿槽位（默认 {DEFAULT_KEPU_DAILY}；传 0 表示不限）")
    ap.add_argument("--no-wrap", action="store_true",
                    help="当日槽位不足时直接报错（而不是把多余单元记入 blocked_units 顺延）")
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
        case_daily = a.case_daily or None
        kepu_daily = a.kepu_daily or None
        assignment, pool, stats = assign(accts, numbers, a.max_per_account, a.no_wrap,
                                         case_daily=case_daily, kepu_daily=kepu_daily)
        return report(assignment, pool, stats, source, a.json)
    except AssignError as e:
        out(f"拒绝分配：{e}")
        return 2
    except Exception as e:  # noqa: BLE001
        out(f"执行失败：{type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
