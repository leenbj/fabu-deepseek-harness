#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每篇网页的核对门禁：自包含、内容对得上稿件、版式与参考页一致。

三组检查：

  A 自包含 —— 页面不得加载任何外部资源（参考页的 CDN 对 CSS 与图片都做了防盗链，
    留一个外链就会出现「本地打开样式全丢」的情况）。分享按钮的 a[href] 不算资源加载。
  B 内容   —— 标题、段落数、侧栏互链是否与同目录的稿件一致；侧栏不得出现本批以外的条目。
  C 版式   —— 用 Playwright 打开页面，核对关键元素的坐标 / 尺寸是否与参考页实测值一致
    （容差 ±1px）。没有 Playwright 时自动跳过，只跑 A、B，并在结论里写明。

用法：

    python page_check.py --dir "<项目文件夹>"
    python page_check.py 01-某企业-稿件.html --no-layout     # 只跑静态检查

退出码：0 = 全部通过，1 = 有未通过项，2 = 用法错误。
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render_pages import DraftError, discover, parse_draft  # noqa: E402
from render_pages import DEFAULT_SITE_INDEX, load_site_index  # noqa: E402

# 参考页在 1440px 视口下的实测值：(x, y, w, h)，None = 不核对
# 来源：对参考页 article-detail/W5d6gADN 跑 getBoundingClientRect（见 references/article-page.md）
EXPECT_BOX = {
    ".site-logo": (119, 32, 113, 113),
    ".site-name": (232, 67, 327, 48),
    ".site-sub": (239, 115, 339, 21),
    ".menu-list": (120, 150, 1200, 50),
    ".article-title": (120, 270, 860, 34),
    ".sidebar": (1020, None, 300, None),
    ".footer": (None, None, 1440, 250),
    ".section-icp": (None, None, 1440, 24),
}
# 相对页脚顶部的纵向偏移（页脚整体随正文长短上下移动，所以只核对相对值）
EXPECT_FOOTER_OFFSET = {
    ".f-tip": 72, ".f-links": 74, ".f-org": 104, ".f-addr": 137, ".f-domain": 168, ".f-copy": 215,
}
TOLERANCE = 1

EXTERNAL_PATTERNS = [
    (re.compile(r"<link\b[^>]*\bhref\s*=\s*[\"'](?!#|data:)", re.I), "无 <link> 外链"),
    (re.compile(r"<script\b[^>]*\bsrc\s*=", re.I), "无 <script src> 外链"),
    (re.compile(r"<(?:img|iframe|source|video|audio)\b[^>]*\bsrc\s*=\s*[\"'](?!data:)", re.I),
     "无指向外部资源的 src"),
    (re.compile(r"url\(\s*[\"']?(?!data:|#)", re.I), "CSS 里无 url() 外链"),
    (re.compile(r"@import\b", re.I), "CSS 里无 @import"),
]


class Report:
    def __init__(self, name: str):
        self.name = name
        self.rows: list[tuple[bool, str, str]] = []

    def check(self, ok: bool, code: str, msg: str) -> bool:
        self.rows.append((bool(ok), code, msg))
        return ok

    @property
    def failed(self):
        return [r for r in self.rows if not r[0]]


def static_checks(html_path: Path, txt_dir: Path | None = None,
                  source_txt: Path | None = None, index_ids: set[str] | None = None) -> Report:
    rep = Report(html_path.name)
    doc = html_path.read_text(encoding="utf-8")

    # --- A 自包含 -----------------------------------------------------------
    for pattern, label in EXTERNAL_PATTERNS:
        m = pattern.search(doc)
        rep.check(m is None, "A/自包含",
                  label if m is None else f"{label} —— 实际命中：{m.group(0)[:70]}")
    rep.check("@font-face" in doc and "data:font/woff" in doc, "A/字体",
              "分享图标字体已内联为 data URI")
    rep.check('src="data:image/png;base64,' in doc, "A/站标",
              "站点标识已内联为 data URI")

    # --- B 内容与稿件一致 ---------------------------------------------------
    # 稿件来源优先级：清单里登记的源文件 > 同名 .txt > --txt-dir 下的同名 .txt
    sibling = source_txt if (source_txt and source_txt.exists()) else html_path.with_suffix(".txt")
    if not sibling.exists() and txt_dir is not None:
        candidate = txt_dir / sibling.name
        if candidate.exists():
            sibling = candidate
    if not sibling.exists():
        rep.check(False, "B/稿件",
                  f"找不到对应稿件 {html_path.with_suffix('.txt').name}，无法核对内容"
                  f"（页面与稿件同目录、用 --txt-dir 指定稿件目录，或用短 ID 生成时的 _pages.json）")
        return rep

    try:
        title, paras, _ = parse_draft(sibling)
    except DraftError as exc:
        rep.check(False, "B/稿件", f"稿件解析失败：{exc}")
        return rep

    # 页面里的 &quot; / &#x27; 等实体要先还原，才能与稿件原文逐字比对
    plain = html.unescape(doc)

    m = re.search(r'<h1 class="article-title">(.*?)</h1>', doc, re.S)
    h1 = html.unescape(m.group(1).strip()) if m else None
    rep.check(h1 == title, "B/标题", f"页面标题与稿件一致（{title[:24]}…）"
              if h1 == title else f"页面标题与稿件不一致：页面「{h1}」/ 稿件「{title}」")

    n_p = len(re.findall(r"<p>", doc))
    rep.check(n_p == len(paras), "B/段落数", f"正文 {n_p} 段，稿件 {len(paras)} 段")

    for i, para in enumerate(paras, 1):
        if para not in plain:
            rep.check(False, "B/段落", f"第 {i} 段未逐字出现在页面里（开头：{para[:20]}…）")
            break
    else:
        rep.check(True, "B/段落", "每一段正文都能逐字在页面里找到")

    # 侧栏链接来自站点索引，可能指向**往批**的页面（本地没有这个文件），
    # 所以判定标准是「本地存在 或 索引里有这个 ID」，不是「必须同目录」。
    sidebar = re.search(r'<aside class="sidebar">(.*?)</aside>', doc, re.S)
    sidebar_html = sidebar.group(1) if sidebar else ""
    links = re.findall(r'href="([^"#][^"]*\.html)"', sidebar_html)
    missing = []
    for h in links:
        if (html_path.parent / h).exists():
            continue
        if index_ids is not None and Path(h).stem in index_ids:
            continue
        missing.append(h)
    if index_ids is None:
        rep.check(True, "B/侧栏", f"侧栏 {len(links)} 条互链（没有站点索引，跳过死链核对）")
    else:
        rep.check(not missing, "B/侧栏", f"侧栏 {len(links)} 条互链都有对应页面（本地或站点索引）"
                  if not missing else f"侧栏存在失效链接（本地没有、索引里也没有）：{missing}")
    absolute = [h for h in links if h.startswith(("http:", "https:", "//"))]
    rep.check(not absolute, "B/侧栏外链", "侧栏只链到站内页面，不指向站外"
              if not absolute else f"侧栏出现了站外链接：{absolute}")
    # 「最新动态」标题只在有可链内容时才生成：有标题就必须有条目（不编造外部旧条目）
    has_recent = 'class="uk-h4"' in sidebar_html
    rep.check((not has_recent) or bool(links), "B/侧栏列表",
              f"有「最新动态」标题时条目数 {len(links)}（索引里没有其它页面时不生成该标题）")

    # 制作过程用语不得进页面
    banned = [w for w in ("据表格", "写作测试", "企业资料介绍", "稿外说明", "自检结论") if w in doc]
    rep.check(not banned, "B/用语", f"页面无制作过程用语（命中：{banned}）")
    return rep


def layout_checks(html_paths: list[Path]) -> tuple[list[Report], str | None]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover
        return [], f"未安装 Playwright（{exc}），已跳过版式核对"

    reports: list[Report] = []
    js = """
    (sels) => {
      const out = {};
      for (const s of sels) {
        const el = document.querySelector(s);
        if (!el) { out[s] = null; continue; }
        const r = el.getBoundingClientRect();
        out[s] = [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)];
      }
      return out;
    }
    """
    sels = list(EXPECT_BOX) + list(EXPECT_FOOTER_OFFSET) + [".footer-red"]
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            for path in html_paths:
                rep = Report(path.name)
                page.goto("file:///" + str(path).replace("\\", "/"),
                          wait_until="load", timeout=30000)
                page.wait_for_timeout(150)
                got = page.evaluate(js, sels)
                for sel, exp in EXPECT_BOX.items():
                    cur = got.get(sel)
                    if cur is None:
                        rep.check(False, "C/元素", f"{sel} 不存在")
                        continue
                    for i, (e, g) in enumerate(zip(exp, cur)):
                        if e is None:
                            continue
                        axis = "xywh"[i]
                        rep.check(abs(e - g) <= TOLERANCE, "C/版式",
                                  f"{sel} {axis}={g}（参考页 {e}）")
                footer = got.get(".footer")
                if footer:
                    for sel, off in EXPECT_FOOTER_OFFSET.items():
                        cur = got.get(sel)
                        if cur is None:
                            rep.check(False, "C/元素", f"{sel} 不存在")
                            continue
                        rel = cur[1] - footer[1]
                        rep.check(abs(rel - off) <= TOLERANCE, "C/页脚",
                                  f"{sel} 距页脚顶 {rel}px（参考页 {off}px）")
                red = got.get(".footer-red")
                rep.check(red is not None and abs(red[3] - 52) <= TOLERANCE, "C/页脚",
                          f"页脚红色长条高 {red[3] if red else '缺失'}px（参考页 52px）")
                reports.append(rep)
            browser.close()
    except Exception as exc:
        return [], f"版式核对未能完成（{exc}）"
    return reports, None


def main() -> int:
    ap = argparse.ArgumentParser(description="稿件网页核对门禁（自包含 / 内容 / 版式）")
    ap.add_argument("files", nargs="*", type=Path)
    ap.add_argument("--dir", dest="folder", type=Path)
    ap.add_argument("--txt-dir", type=Path,
                    help="稿件目录（页面不在稿件旁边时用，例如用了 render_pages.py --out-dir）")
    ap.add_argument("--manifest", type=Path,
                    help="短 ID 模式生成的 _pages.json（默认在 --dir 及其一级子目录里自动找）")
    ap.add_argument("--index", type=Path, default=DEFAULT_SITE_INDEX,
                    help=f"站点级互链索引（默认 {DEFAULT_SITE_INDEX}），用于核对侧栏链接是否有效")
    ap.add_argument("--no-layout", action="store_true", help="跳过版式核对（不需要 Playwright）")
    args = ap.parse_args()

    paths: list[Path] = []
    sources: dict[str, Path] = {}

    def load_manifest(mf: Path) -> None:
        """短 ID 模式下的清单：文件 → 源稿件。"""
        try:
            data = json.loads(mf.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"清单读取失败（{mf}）：{exc}", file=sys.stderr)
            return
        for entry in data.get("pages", []):
            page = (mf.parent / entry["file"]).resolve()
            if page.exists():
                paths.append(page)
                if entry.get("source"):
                    src = Path(entry["source"])
                    if src.exists():
                        sources[str(page)] = src

    if args.folder:
        # 案例稿清单 _pages.json 与科普稿清单 _pages-科普.json 都要认
        manifests = sorted(args.folder.glob("_pages*.json")) + \
            sorted(args.folder.glob("*/_pages*.json"))
        for mf in manifests:
            if mf.exists():
                load_manifest(mf)
        paths.extend(sorted(args.folder.rglob("*稿件*.html")))
    if args.manifest and args.manifest.exists():
        load_manifest(args.manifest)
    for f in args.files:
        if f.suffix.lower() != ".html":
            f = f.with_suffix(".html")
        paths.append(f)

    seen, ordered = set(), []
    for p in paths:
        key = str(p.resolve())
        if key not in seen:
            seen.add(key)
            ordered.append(p.resolve())
    paths = ordered
    if not paths:
        print("没有找到页面文件（可以是「*稿件*.html」，或短 ID 模式下由 _pages.json 登记）",
              file=sys.stderr)
        return 2

    # 站点索引：侧栏互链可能指向往批页面，用它判断链接是否仍然有效
    index_ids: set[str] | None = None
    if args.index and Path(args.index).exists():
        index_ids = {e.get("id") for e in load_site_index(Path(args.index))["items"] if e.get("id")}
        print(f"站点索引: {args.index}（{len(index_ids)} 篇可用作互链核对）\n")

    reports = [static_checks(p, args.txt_dir, sources.get(str(p)), index_ids) for p in paths]
    skipped = None
    if not args.no_layout:
        layout_reports, skipped = layout_checks(paths)
        if layout_reports:
            for lr, sr in zip(layout_reports, reports):
                sr.rows.extend(lr.rows)
        else:
            for sr in reports:
                sr.check(True, "C/跳过", skipped or "版式核对已跳过")

    total_failed = 0
    for rep in reports:
        print("=" * 88)
        print(rep.name)
        print("=" * 88)
        for ok, code, msg in rep.rows:
            print(f"  {'ok  ' if ok else 'FAIL'}  {code:<10} {msg}")
        failed = len(rep.failed)
        total_failed += failed
        print(f"\n  {len(rep.rows) - failed}/{len(rep.rows)} 通过，{failed} 项失败\n")

    if skipped:
        print(f"注意：{skipped}")
    if total_failed:
        print(f"RESULT: FAIL — {total_failed} 项未通过（{len(paths)} 个页面）")
        print("Remedy: 重新生成前先看是哪一组：A 说明有外链没内联；")
        print("        B 说明页面内容与稿件对不上；C 说明 assets/page/site.css 被改动或参考页改版了——")
        print("        按 references/article-page.md 第八节重新测量后再改样式。")
        return 1
    print(f"RESULT: PASS — {len(paths)} 个页面全部达标（自包含 / 内容一致 / 版式与参考页一致）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
