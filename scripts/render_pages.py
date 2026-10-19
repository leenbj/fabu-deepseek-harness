#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每篇稿件生成一个自包含的 HTML 页面。

版式复刻自参考页（北京知识产权研究会官网 article-detail 页）：
只把标题、分类与时间、正文换成稿件内容；站点标识、导航、侧栏、页脚保持该页外观。
样式、站点标识与分享图标字体全部内联，产物是单文件、可离线双击打开，
不向任何 CDN 或第三方发请求（该参考页的 CDN 对图片与 CSS 都做了防盗链，
直链在本地必然 403，所以必须内联）。

用法：

    python render_pages.py --dir "<项目文件夹>"            # 批量：一篇稿件一个 .html
    python render_pages.py 01-某企业-稿件.txt --out-dir out
    python render_pages.py --dir "<项目文件夹>" --datetime "2026-10-16 09:00:00"
    python render_pages.py --dir "<项目文件夹>" --dry-run   # 只报将要生成什么

稿件文件格式与 scripts/draft_check.py 一致：第一行是 ``标题：…``，其后每个非空行是一段正文。

退出码：0 = 全部生成成功，1 = 有稿件无法解析或写入失败，2 = 用法错误。
"""
from __future__ import annotations

import argparse
import base64
import datetime as _dt
import hashlib
import html
import json
import os
import random
import re
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_ASSETS = SKILL_DIR / "assets" / "page"

# 短 ID：与参考页 /article-detail/W5d6gADN 同样的 8 位「大小写字母＋数字」形式。
# 由标题哈希得到，同一标题永远得到同一个 ID，跨批次复现，上传后 URL 干净。
BASE62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
MANIFEST_NAME = "_pages.json"

# 站点级索引：记录**所有已生成/已上线**的页面，用来做跨批次的侧栏互链。
# 每次运行都会把本批页面写进去，所以批次之间能互相看见。
SITE_INDEX_NAME = "site-index.json"
DEFAULT_SITE_INDEX = Path.home() / ".dsh" / "fabu-deepseek-harness" / SITE_INDEX_NAME


def load_site_index(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "items": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "items": []}
    data.setdefault("version", 1)
    data.setdefault("items", [])
    return data


def save_site_index(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def upsert_index(index: dict, items: list[dict], kind: str, site_origin: str) -> None:
    """把本批页面写进站点索引：新页面记 first_seen，老页面只更新标题/时间/文件名。"""
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    by_id = {e.get("id"): e for e in index["items"]}
    for it in items:
        e = by_id.get(it["id"])
        if e is None:
            index["items"].append({
                "id": it["id"],
                "file": it["html"].name,
                "title": it["title"],
                "datetime": it["datetime"],
                "kind": kind,
                "site": site_origin,
                "first_seen": now,
            })
        else:
            e.update({"file": it["html"].name, "title": it["title"],
                      "datetime": it["datetime"], "kind": kind, "site": site_origin,
                      "updated_at": now})


def index_pool(index: dict, pool_latest: int = 0) -> list[dict]:
    """按「先收录的在前」排好的候选池；pool_latest>0 时只取最新的 N 条。"""
    items = [e for e in index["items"] if e.get("id") and e.get("file")]
    items.sort(key=lambda e: (e.get("first_seen", ""), e.get("datetime", ""), e.get("id", "")),
               reverse=True)
    return items[:pool_latest] if pool_latest > 0 else items


def pick_related(pool: list[dict], current_id: str, count: int, seed) -> list[dict]:
    """从池子里随机挑 count 条（不含自己），挑完按时间倒序展示。

    随机的是"选哪几篇"，不是"怎么排"——这样每个页面的互链组合都不一样，
    但列表看上去仍像一条按时间排列的"最新动态"。
    """
    cand = [e for e in pool if e.get("id") != current_id]
    if not cand or count <= 0:
        return []
    rnd = random.Random(seed)
    picked = cand if len(cand) <= count else rnd.sample(cand, count)
    return sorted(picked, key=lambda e: (e.get("datetime", ""), e.get("id", "")), reverse=True)


def short_id(title: str, salt: str = "") -> str:
    h = hashlib.sha1((salt + title).encode("utf-8")).digest()
    n = int.from_bytes(h[:8], "big")
    out = []
    for _ in range(8):
        n, r = divmod(n, len(BASE62))
        out.append(BASE62[r])
    return "".join(out)

TITLE_RE = re.compile(r"^\s*标题\s*[:：]\s*")
# 稿件里若混进了稿外说明，从这一行起不再算正文（防止制作过程用语被带进网页）
STOP_RE = re.compile(
    r"^\s*(?:[-—=─]{3,}|稿外|附\s*[:：]?\s*稿外|来源清单|官网启用依据"
    r"|缺料与限制|自检(?:结论|说明)?\s*[:：]|字数\s*[:：]|相似度\s*[:：])"
)
SKIP_NAME_HINTS = ("稿外", "说明", "来源", "科普")

SHARE_ENDPOINTS = {
    "weibo": "http://service.weibo.com/share/share.php?url={url}&title={title}",
    "qq": "http://connect.qq.com/widget/shareqq/index.html?url={url}&title={title}",
    "qzone": "http://sns.qzone.qq.com/cgi-bin/qzshare/cgi_qzshare_onekey?url={url}&title={title}",
    "douban": "http://shuo.douban.com/!service/share?href={url}&name={title}",
}


class DraftError(Exception):
    pass


# --------------------------------------------------------------------------- #
# 稿件读取
# --------------------------------------------------------------------------- #
def discover(target: Path, pattern: str = "稿件") -> list[Path]:
    """取稿规则：文件名含 `pattern`（默认「稿件」），排除稿外/说明/来源/科普一类副文件。

    科普稿用 `--pattern 科普` 取；此时「科普」不再当排除词。
    """
    if target.is_file():
        return [target]
    if not target.exists():
        raise DraftError(f"路径不存在：{target}")
    hints = [k for k in SKIP_NAME_HINTS if k != pattern]
    out: list[Path] = []
    for p in sorted(target.rglob("*.txt")):
        name = p.name
        if pattern not in name or any(k in name for k in hints):
            continue
        out.append(p)
    return out


def parse_draft(path: Path) -> tuple[str, list[str], bool]:
    """返回 (标题, 正文段落列表, 是否截断过稿外内容)。"""
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    idx = next((i for i, l in enumerate(lines) if l.strip()), None)
    if idx is None:
        raise DraftError("文件是空的")

    title = TITLE_RE.sub("", lines[idx].strip()).strip()
    if not title:
        raise DraftError("第一行没有可用的标题")

    paras: list[str] = []
    truncated = False
    for line in lines[idx + 1:]:
        s = line.strip()
        if not s:
            continue
        if STOP_RE.match(s):
            truncated = True
            break
        paras.append(s)

    if not paras:
        raise DraftError("标题之后没有正文段落")
    return title, paras, truncated


def draft_datetime(path: Path) -> str:
    ts = _dt.datetime.fromtimestamp(path.stat().st_mtime)
    return ts.strftime("%Y-%m-%d %H:%M:%S")


# --------------------------------------------------------------------------- #
# 片段拼装
# --------------------------------------------------------------------------- #
def esc(s: str) -> str:
    return html.escape(s, quote=True)


def nav_html(site: dict) -> str:
    origin = site["site_origin"].rstrip("/")
    rows = []
    for item in site.get("nav", []):
        children = item.get("children") or []
        if children:
            href = 'javascript:void(0)'
        else:
            path = item.get("path") or "/"
            href = path if path.startswith(("http://", "https://", "javascript:")) else origin + path
        rows.append(
            f'          <li class="menu-item">\n'
            f'            <a href="{esc(href)}" target="_self">{esc(item["label"])}</a>'
        )
        if children:
            subs = []
            for ch in children:
                path = ch.get("path") or "/"
                chref = path if path.startswith(("http://", "https://")) else origin + path
                subs.append(
                    f'                <li class="menu-item">'
                    f'<a href="{esc(chref)}" target="_self">{esc(ch["label"])}</a></li>'
                )
            rows.append("            <ul>\n" + "\n".join(subs) + "\n            </ul>")
        rows.append("          </li>")
    return "\n".join(rows)


def footer_html(site: dict) -> str:
    f = site["footer"]
    lines = [
        f'      <div class="f-line f-tip">{esc(f["tip"])}</div>',
        '      <div class="f-line f-links">'
        + "".join(
            f'<a href="{esc(l["href"])}" rel="nofollow" target="_blank">{esc(l["text"])}</a>'
            for l in f.get("links", [])
        )
        + "</div>",
        f'      <div class="f-line f-org">{esc(f["org"])}</div>',
        f'      <div class="f-line f-addr">{esc(f["addr"])}</div>',
        f'      <div class="f-line f-domain">{esc(f["domain"])}</div>',
        f'      <div class="f-line f-copy">{esc(f["copyright"])}</div>',
    ]
    return "\n".join(lines)


def share_html(site: dict) -> str:
    out = []
    for s in site.get("share", {}).get("sites", []):
        if s in SHARE_ENDPOINTS:
            out.append(
                f'<a class="social-share-icon icon-{s}" data-share="{s}" '
                f'href="javascript:void(0)" target="_blank" rel="nofollow"></a>'
            )
        else:  # wechat 等需要运行时生成二维码的站点：只保留图标，不做弹窗
            out.append(
                f'<a class="social-share-icon icon-{s}" data-share="{s}" '
                f'href="javascript:void(0)"></a>'
            )
    return "".join(out)


def sidebar_html(site: dict, related: list[dict], category_url: str,
                 more_url: str | None = None) -> str:
    """侧栏：随机抽取的其它页面（跨批次）+「下一篇」。

    `related` 已经排除本页、已按时间倒序；为空时不生成卡片（宁缺毋滥）。
    """
    if not related:
        return ("          <!-- 站点索引里还没有其它页面：不生成「最新动态」列表与「下一篇」，"
                "避免出现虚构条目 -->")

    lis = [
        f'                <li class="uk-clearfix"><a href="{esc(e["file"])}" title="{esc(e["title"])}">'
        f'{esc(e["title"])}</a><span class="element element-text">{esc(e.get("datetime", ""))}</span></li>'
        for e in related
    ]
    cards = [
        '          <div class="card">\n'
        '            <div class="new-list">\n'
        f'              <h3 class="uk-h4">{esc(site["recent_heading"])}</h3>\n'
        '              <ul>\n' + "\n".join(lis) + "\n              </ul>\n"
        '            </div>\n'
        f'            <a href="{esc(more_url or category_url)}" class="footer-tip">'
        f'{esc(site["more_text"])}</a>\n'
        '          </div>'
    ]

    nxt = related[0]
    cards.append(
        '          <div class="card next">\n'
        f'            <h3>{esc(site["next_heading"])}</h3>\n'
        f'            <a href="{esc(nxt["file"])}" class="next" title="{esc(nxt["title"])}">'
        f'{esc(nxt["title"])}</a>\n'
        '          </div>'
    )
    return "\n".join(cards)


def _rel_href(target: Path, base: Path) -> str:
    rel = os.path.relpath(str(target), str(base))
    return rel.replace(os.sep, "/")


def _seed_for(mode: str, item: dict):
    """互链随机种子：page=按页面 ID 固定（每页组合不同且可复现）；random=每次重新抽；数字=全局固定。"""
    if mode == "random":
        return None
    if mode == "page":
        return str(item.get("id") or item.get("title") or "")
    try:
        return int(mode)
    except ValueError:
        return str(item.get("id") or "")


def body_html(paras: list[str]) -> str:
    return "\n".join(f"            <p>{esc(p)}</p>" for p in paras)


def share_script() -> str:
    return """<script>
/* 分享目标地址在运行时按本页实际地址生成，本地打开或部署后都指向本页。 */
(function () {
  var u = encodeURIComponent(location.href);
  var t = encodeURIComponent(document.title);
  var map = {
    weibo: 'http://service.weibo.com/share/share.php?url=' + u + '&title=' + t,
    qq: 'http://connect.qq.com/widget/shareqq/index.html?url=' + u + '&title=' + t,
    qzone: 'http://sns.qzone.qq.com/cgi-bin/qzshare/cgi_qzshare_onekey?url=' + u + '&title=' + t,
    douban: 'http://shuo.douban.com/!service/share?href=' + u + '&name=' + t
  };
  var list = document.querySelectorAll('.social-share a[data-share]');
  for (var i = 0; i < list.length; i++) {
    var s = list[i].getAttribute('data-share');
    if (map[s]) { list[i].setAttribute('href', map[s]); }
  }
})();
</script>
"""


# --------------------------------------------------------------------------- #
# 渲染
# --------------------------------------------------------------------------- #
def data_uri(path: Path) -> str:
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".woff": "font/woff", ".woff2": "font/woff2"}.get(path.suffix.lower())
    if not mime:
        raise DraftError(f"不支持的资源类型：{path}")
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def render(item: dict, site: dict, template: str, css: str,
           logo_uri: str, icon_uri: str, related: list[dict] | None = None,
           no_sidebar: bool = False) -> str:
    category_url = site["site_origin"].rstrip("/") + site["category_path"]

    tokens = {
        "page_title": f'{item["title"]}_{site["category"]}_{site["site_name"]}',
        "site_name": site["site_name"],
        "site_domain": site["site_domain"],
        "site_home": site["site_origin"].rstrip("/") + "/",
        "source_page": site["source_page"],
        "logo_data_uri": logo_uri,
        "iconfont_data_uri": icon_uri,
        "nav_html": nav_html(site),
        "article_title": esc(item["title"]),
        "category": site["category"],
        "category_url": category_url,
        "datetime": item["datetime"],
        "body_html": body_html(item["paras"]),
        "share_html": share_html(site),
        "sidebar_html": "" if no_sidebar else sidebar_html(
            site, related or [], category_url),
        "footer_html": footer_html(site),
        "icp_href": site["footer"]["icp_href"],
        "icp_text": site["footer"]["icp_text"],
    }

    doc = template.replace("{{site_css}}", css)
    for key, value in tokens.items():
        doc = doc.replace("{{" + key + "}}", value)

    left = re.findall(r"\{\{[a-z_]+\}\}", doc)
    if left:
        raise DraftError(f"模板里有未提供的占位符：{sorted(set(left))}")

    doc = doc.replace("</body>", share_script() + "</body>")
    return doc


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(
        description="每篇稿件生成一个自包含 HTML 页面（版式复刻参考页 article-detail）。"
    )
    ap.add_argument("files", nargs="*", type=Path, help="稿件 .txt；也可只用 --dir")
    ap.add_argument("--dir", dest="folder", type=Path, help="项目文件夹（递归取「*稿件*.txt」）")
    ap.add_argument("--out-dir", type=Path, help="输出目录，默认与稿件同目录")
    ap.add_argument("--datetime", help="统一写死的页面时间，如 \"2026-10-16 09:00:00\"；"
                                       "默认取各稿件文件的最后修改时间")
    ap.add_argument("--category", help="分类名，默认取站点配置里的 category")
    ap.add_argument("--site", type=Path, help="站点外观配置 JSON（默认 assets/page/site.json）")
    ap.add_argument("--assets", type=Path, default=DEFAULT_ASSETS,
                    help="素材目录（默认 assets/page）")
    ap.add_argument("--no-sidebar", action="store_true",
                    help="不生成「最新动态 / 下一篇」侧栏卡片")
    ap.add_argument("--index", type=Path, default=DEFAULT_SITE_INDEX,
                    help=f"站点级互链索引（默认 {DEFAULT_SITE_INDEX}）；每次生成都会把本批页面写进去")
    ap.add_argument("--sidebar-count", type=int, default=10,
                    help="侧栏「最新动态」最多列几篇，默认 10")
    ap.add_argument("--pool-latest", type=int, default=0,
                    help="只用索引里最新的 N 篇做抽样池，0=全部（默认）")
    ap.add_argument("--seed", default="page",
                    help="互链随机种子：page=按页面固定（默认，每页组合不同且可复现）、"
                         "random=每次重新抽、或给一个整数")
    ap.add_argument("--pattern", default="稿件",
                    help="按文件名里的关键词取稿，默认「稿件」；科普稿用 --pattern 科普"
                         "（清单会写成 _pages-科普.json，不和案例稿的 _pages.json 互相覆盖）")
    ap.add_argument("--short-id", action="store_true",
                    help="文件名改用由标题哈希得到的 8 位短 ID（article-detail/W5d6gADN 那种形式），"
                         "并在输出目录写 _pages.json 记录 ID ↔ 稿件对应关系；"
                         "不给 --out-dir 时默认输出到「稿件目录/article-detail/」")
    ap.add_argument("--dry-run", action="store_true", help="只报告将生成哪些文件，不写盘")
    args = ap.parse_args()

    try:
        targets = list(args.files)
        if args.folder:
            targets.append(args.folder)
        if not targets:
            ap.error("至少要给一个稿件文件或用 --dir 指定文件夹")

        drafts: list[Path] = []
        for t in targets:
            drafts.extend(discover(t, args.pattern))
        # 去重并保持稳定顺序
        seen, ordered = set(), []
        for d in drafts:
            key = str(d.resolve())
            if key not in seen:
                seen.add(key)
                ordered.append(d)
        drafts = ordered

        if not drafts:
            print("没有找到任何稿件（文件名需含「稿件」）", file=sys.stderr)
            return 1

        assets = args.assets if args.assets.is_absolute() else (SKILL_DIR / args.assets)
        site_path = args.site or (assets / "site.json")
        site = json.loads(Path(site_path).read_text(encoding="utf-8"))
        if args.category:
            site["category"] = args.category

        template = (assets / "template.html").read_text(encoding="utf-8")
        css = (assets / "site.css").read_text(encoding="utf-8")
        logo_uri = data_uri(assets / "site-logo.png")
        icon_uri = data_uri(assets / "iconfont.woff")

        items, problems = [], []
        used_ids: dict[str, str] = {}
        out_dir = args.out_dir
        if args.short_id and not out_dir:
            base = args.folder if (args.folder and args.folder.is_dir()) else drafts[0].parent
            out_dir = base / "article-detail"
        for path in drafts:
            try:
                title, paras, truncated = parse_draft(path)
            except DraftError as exc:
                problems.append(f"{path.name}：{exc}")
                continue
            if args.short_id:
                sid, salt = short_id(title), 0
                while sid in used_ids and used_ids[sid] != title:
                    salt += 1
                    sid = short_id(title, str(salt))
                used_ids[sid] = title
                stem = sid
            else:
                stem = path.stem
            items.append({
                "path": path,
                "title": title,
                "paras": paras,
                "truncated": truncated,
                "datetime": args.datetime or draft_datetime(path),
                "html": ((out_dir or path.parent) / (stem + ".html")).resolve(),
                "id": stem if args.short_id else None,
            })
        for i, it in enumerate(items):
            it["index"] = i

        if not items:
            for p in problems:
                print("  FAIL  " + p, file=sys.stderr)
            return 1

        # 站点索引：本批页面先入索引，再渲染——这样同批页面之间、以及与本批之前的
        # 所有页面之间，都能互相被抽到（跨批次互链）。
        index = load_site_index(args.index)
        kind = "case" if args.pattern == "稿件" else args.pattern
        upsert_index(index, items, kind, site.get("site_origin", ""))
        pool = index_pool(index, args.pool_latest)
        print(f"  索引  {args.index}（共 {len(index['items'])} 篇可用作互链）")

        written = []
        for it in items:
            seed = _seed_for(args.seed, it)
            related = pick_related(pool, it["id"], args.sidebar_count, seed)
            doc = render(it, site, template, css, logo_uri, icon_uri,
                         related=related, no_sidebar=args.no_sidebar)
            if args.dry_run:
                print(f"  DRY   {it['path'].name}  ->  {it['html']}  "
                      f"({it['title']})  互链 {len(related)} 篇")
                continue
            it["html"].parent.mkdir(parents=True, exist_ok=True)
            it["html"].write_text(doc, encoding="utf-8")
            size = it["html"].stat().st_size
            written.append((it, size))
            print(f"  ok    {it['path'].name}  ->  {it['html'].name}  "
                  f"{len(doc)} 字符 / {size // 1024} KB  互链 {len(related)} 篇  |  {it['title']}")

        if not args.dry_run:
            save_site_index(args.index, index)

        for it in items:
            if it["truncated"]:
                print(f"  注意  {it['path'].name}：正文之后出现「稿外」一类标记，"
                      f"该行起的内容没有写入网页。")

        # 短 ID 模式下额外写一份清单：ID ↔ 稿件 ↔ 标题，供核对与上传使用。
        # 按 ID **合并**而不是覆盖：清单描述的是「这个目录里有哪些页面」，
        # 覆盖会让上一批的页面从清单里消失，page_check 就漏查了。
        if args.short_id and not args.dry_run and items:
            out_parent = items[0]["html"].parent
            mpath = out_parent / (
                MANIFEST_NAME if args.pattern == "稿件" else f"_pages-{args.pattern}.json")
            old = {}
            if mpath.exists():
                try:
                    for e in json.loads(mpath.read_text(encoding="utf-8")).get("pages", []):
                        if e.get("id"):
                            old[e["id"]] = e
                except Exception:
                    old = {}
            for it in items:
                old[it["id"]] = {
                    "file": it["html"].name,
                    "id": it["id"],
                    "title": it["title"],
                    "source": str(it["path"]),
                    "datetime": it["datetime"],
                }
            kept = {k: v for k, v in old.items() if (out_parent / v.get("file", "")).exists()}
            manifest = {
                "generated_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "site": site["site_name"],
                "pages": [kept[k] for k in sorted(kept)],
            }
            mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                             encoding="utf-8")
            print(f"  清单  {mpath}（合并后 {len(manifest['pages'])} 页）")

        if problems:
            print("", file=sys.stderr)
            for p in problems:
                print("  FAIL  " + p, file=sys.stderr)

        if args.dry_run:
            print(f"\nDRY-RUN：将生成 {len(items)} 个页面（未写盘）"
                  f"{'，' + str(len(problems)) + ' 篇解析失败' if problems else ''}")
        else:
            print(f"\nRESULT: {'PASS' if not problems else 'PARTIAL'} — "
                  f"生成 {len(written)} 个页面"
                  f"{'，' + str(len(problems)) + ' 篇失败' if problems else ''}")

        return 1 if problems else 0

    except DraftError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
