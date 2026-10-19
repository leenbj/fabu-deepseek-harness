# -*- coding: utf-8 -*-
"""发布账号自动分配（跨历史的最小负载均衡）——契约见 references/account-assignment.md。

要点
  * 负载 = 账号的**累计已发布篇数**，不是"本批内轮询"；否则每批都从同一个账号开跑。
  * 贪心最小负载 ⇒ 最终极差最小；并列时按账号代号升序 ⇒ 同一输入必得同一结果（可复现）。
  * 登录态失效的账号不参与；当日超限的账号从候选集中剔除。
  * 一稿一账号：绝不把同一篇稿派给多个账号。

用法
  python assign_accounts.py --accounts accounts.json --numbers 76,83,80,73,79
  python assign_accounts.py --accounts accounts.json --numbers 76,83,80 --from-runs <runs目录>
  python assign_accounts.py --self-test
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def out(msg: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(msg)


class AssignError(Exception):
    pass


def load_accounts(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
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

    台账由发布流程每发一篇追加一行，天然可审计，且**不依赖发布是脚本还是手工完成的**。
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


def assign(accts: list[dict], numbers: list,
           max_per_account: int | None = None) -> tuple[list[tuple], list[dict], dict]:
    # 防御式取值：调用方可能传未经 load_accounts 规范化的原始字典
    pool = [a for a in accts if a.get("login_ok", False)]
    if not pool:
        raise AssignError("没有任何登录态有效的账号，拒绝分配")

    load = {a["code"]: int(a.get("published_count") or 0) for a in pool}
    today = {a["code"]: int(a.get("today_published") or 0) for a in pool}
    batch = {a["code"]: 0 for a in pool}
    before = dict(load)

    def sort_key(x):
        s = str(x)
        return (0, int(s)) if s.isdigit() else (1, s)

    assignment = []
    for num in sorted(numbers, key=sort_key):
        cand = [a for a in pool
                if (a.get("daily_limit") is None
                    or today[a["code"]] < int(a["daily_limit"]))
                and (max_per_account is None or batch[a["code"]] < max_per_account)]
        if not cand:
            raise AssignError(f"分配第 {num} 篇时无可用账号"
                              f"（当日限额已满，或本批单账号上限 {max_per_account} 已用尽），停下报告（不硬塞）")
        # 最小负载；并列取账号代号升序
        pick = min(cand, key=lambda a: (load[a["code"]], a["code"]))
        load[pick["code"]] += 1
        today[pick["code"]] += 1
        batch[pick["code"]] += 1
        assignment.append((num, pick["code"], pick.get("media") or ""))

    after = dict(load)
    return assignment, pool, {"before": before, "after": after, "batch": batch}


def spread(d: dict) -> int:
    if not d:
        return 0
    return max(d.values()) - min(d.values())


def report(assignment, pool, stats, source_note: str, as_json: bool) -> int:
    before, after = stats["before"], stats["after"]
    if as_json:
        out(json.dumps({
            "assignment": [{"number": n, "account": c, "media": m} for n, c, m in assignment],
            "before": before, "after": after,
            "spread_before": spread(before), "spread_after": spread(after),
            "load_source": source_note,
        }, ensure_ascii=False, indent=2))
        return 0

    out(f"账号池（{len(pool)} 个，已排除登录态失效）：")
    for a in sorted(pool, key=lambda x: x["code"]):
        lim = "无限制" if a["daily_limit"] is None else f"{a['today_published']}/{a['daily_limit']} 当日"
        out(f"  {a['code']:<6} {a['media']:<8} 累计 {a['published_count']:>3} 篇    {lim}")
    out(f"\n负载来源：{source_note}")
    out(f"\n本批 {len(assignment)} 篇的分配：")
    for n, c, m in assignment:
        out(f"  编号 {str(n):<6} → 账号 {c:<6} ({m})")
    out()
    out(f"{'账号':<8}{'分配前':>8}{'分配后':>8}{'本批':>6}")
    out("-" * 34)
    for c in sorted(after):
        out(f"{c:<8}{before[c]:>8}{after[c]:>8}{after[c] - before[c]:>6}")
    out("-" * 34)
    sb, sa = spread(before), spread(after)
    out(f"极差：分配前 {sb} → 分配后 {sa}")
    if sa == 0:
        out("判定：各账号累计负载完全相等。")
    elif sa == 1:
        out("判定：极差 1，已达最小（篇数不足以拉平到 0）。")
    elif sa < sb:
        out(f"判定：极差收窄 {sb}→{sa}，在收敛；继续按本规则分配即可逐步拉平。")
    else:
        out(f"判定：极差未收窄（{sb}→{sa}）。通常说明本批篇数太少或账号负载本身差距过大，"
            f"不是算法问题——继续分配即可拉平。")
    return 0


# ----------------------------- 自测 -----------------------------

def self_test() -> int:
    ok = True

    def check(name, cond, extra=""):
        nonlocal ok
        ok = ok and cond
        out(f"  [{'PASS' if cond else 'FAIL'}] {name} {extra}")

    out("=== 自测：均衡与可复现 ===")

    # 1) 从零开始，3 账号 6 篇 → 完全均分
    accts = [{"code": "a-1", "media": "sohu", "login_ok": True, "published_count": 0},
             {"code": "a-2", "media": "toutiao", "login_ok": True, "published_count": 0},
             {"code": "b-1", "media": "toutiao", "login_ok": True, "published_count": 0}]
    a1, p1, s1 = assign([dict(x) for x in accts], [1, 2, 3, 4, 5, 6])
    check("3 账号 6 篇 → 极差 0", spread(s1["after"]) == 0, f"after={s1['after']}")

    # 2) 历史不均：a-1 已 5 篇，另两个 0 篇，本批 5 篇
    #    负载只能加不能减 ⇒ 最优只能把两个 0 尽量抬高：5 篇摊成 3+2，最终 {5,3,2}，极差 3。
    #    注意 a-1 是最高负载，应当**一篇都不接**。
    accts2 = [{"code": "a-1", "media": "sohu", "login_ok": True, "published_count": 5},
              {"code": "a-2", "media": "toutiao", "login_ok": True, "published_count": 0},
              {"code": "b-1", "media": "toutiao", "login_ok": True, "published_count": 0}]
    a2, p2, s2 = assign([dict(x) for x in accts2], [11, 12, 13, 14, 15])
    check("最高负载账号一篇都不接", s2["after"]["a-1"] == 5, f"after={s2['after']}")
    check("低负载账号先接活", s2["after"]["a-2"] == 3 and s2["after"]["b-1"] == 2,
          f"after={s2['after']}")
    check("极差收窄", spread(s2["after"]) < spread(s2["before"]),
          f"{spread(s2['before'])}→{spread(s2['after'])}")
    check("分配是当前约束下的最优（负载只增不减）", spread(s2["after"]) == 3,
          f"spread={spread(s2['after'])}")

    # 3) 可复现：同一输入两次结果一致
    r1 = [c for _, c, _ in assign([dict(x) for x in accts2], [11, 12, 13, 14, 15])[0]]
    r2 = [c for _, c, _ in assign([dict(x) for x in accts2], [11, 12, 13, 14, 15])[0]]
    check("同一输入两次分配一致", r1 == r2, f"{r1}")

    # 4) 一稿一账号
    check("每篇只出现一次", len({n for n, _, _ in a2}) == len(a2))

    # 5) 登录态失效者不参与
    accts3 = [{"code": "a-1", "media": "sohu", "login_ok": False},
              {"code": "a-2", "media": "toutiao", "login_ok": True}]
    a3, _, s3 = assign([dict(x) for x in accts3], [1, 2, 3])
    check("失效账号不接活", all(c == "a-2" for _, c, _ in a3), f"{[c for _, c, _ in a3]}")

    # 6) 当日限额：超限账号被跳过
    accts4 = [{"code": "a-1", "media": "sohu", "login_ok": True, "published_count": 0,
               "daily_limit": 1, "today_published": 0},
              {"code": "a-2", "media": "toutiao", "login_ok": True, "published_count": 0}]
    a4, _, _ = assign([dict(x) for x in accts4], [1, 2])
    check("超限账号顺延", [c for _, c, _ in a4] == ["a-1", "a-2"], f"{[c for _, c, _ in a4]}")

    # 7) 全部超限 → 报错而不是硬塞
    try:
        assign([{"code": "a-1", "media": "sohu", "login_ok": True,
                 "daily_limit": 1, "today_published": 9}], [1])
        check("全部超限时报错", False)
    except AssignError as e:
        check("全部超限时报错", True, f"({e})")

    # 8) 账号池扩容：新增账号无需改任何代码/清单，自动纳入并被喂到追平
    accts5 = [{"code": "a-1", "media": "sohu", "login_ok": True, "published_count": 4},
              {"code": "a-2", "media": "toutiao", "login_ok": True, "published_count": 4},
              {"code": "d-1", "media": "toutiao", "login_ok": True, "published_count": 0}]
    a5, p5, s5 = assign([dict(x) for x in accts5], [1, 2, 3, 4])
    check("新增账号自动纳入并追平", s5["after"] == {"a-1": 4, "a-2": 4, "d-1": 4},
          f"after={s5['after']}")
    check("扩容后达到极差 0（无需人工配置）", spread(s5["after"]) == 0)

    # 9) 平台筛选：被排除的平台不参与分配，也不参与均衡统计
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

    # 10) 本批单账号上限护栏
    try:
        assign([{"code": "a-1", "media": "sohu", "login_ok": True, "published_count": 0}],
               [1, 2], max_per_account=1)
        check("--max-per-account 生效", False)
    except AssignError:
        check("--max-per-account 生效", True)

    out(f"\n自测结果：{'全部通过' if ok else '存在失败项'}")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="发布账号自动分配（跨历史最小负载均衡）")
    ap.add_argument("--accounts", type=Path, help="账号池 JSON（见 references/account-assignment.md）")
    ap.add_argument("--numbers", help="本批稿件编号，逗号分隔，如 76,83,80")
    ap.add_argument("--only-media", help="只保留这些平台，逗号分隔，如 sohu,toutiao")
    ap.add_argument("--exclude-media", help="排除这些平台，逗号分隔，如 csdn（与 --only-media 同时给出时以 --only-media 为准）")
    ap.add_argument("--ledger", type=Path,
                    help="【首选】台账文件（JSONL 或 JSON 数组），每行一条已发布记录；统计各账号负载")
    ap.add_argument("--from-runs", type=Path,
                    help="【次选】从发布工具运行记录统计负载（会漏计手工驱动的发布，仅作粗略参考）")
    ap.add_argument("--max-per-account", type=int, default=None,
                    help="本批同一账号最多接几篇（可选护栏；新增账号追平时可防它一次吃太多）")
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
        assignment, pool, stats = assign(accts, numbers, a.max_per_account)
        return report(assignment, pool, stats, source, a.json)
    except AssignError as e:
        out(f"拒绝分配：{e}")
        return 2
    except Exception as e:  # noqa: BLE001
        out(f"执行失败：{type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
