# 稿件对应的网页（每篇一页）

每交付一篇稿件，同时交付一个和它对应的网页文件。版式复刻自参考页：

```
参考页：http://xn--wlqj54a59k1y5a5dwqjai2ot90c.xn--ses554g/article-detail/W5d6gADN
        （北京知识产权研究会官网 · 文章详情页）
```

做法只有一句话：**把参考页的标题、分类、时间、正文换成稿件的，其余版式照搬。**

---

## 1. 一条命令

```bash
# 批量：文件夹里每篇「*稿件*.txt」出一个同名 .html
python scripts/render_pages.py --dir "<项目文件夹>"

# 统一指定页面时间（默认取各稿件文件的最后修改时间）
python scripts/render_pages.py --dir "<项目文件夹>" --datetime "2026-10-16 09:00:00"

# 要放到服务器上：用短 ID 命名（article-detail/W5d6gADN 那种 8 位 ID），并写 _pages.json 清单
python scripts/render_pages.py --dir "<项目文件夹>" --short-id
#   → 默认产出到「<项目文件夹>/article-detail/<8位ID>.html」

# 只报告将生成什么，不写盘
python scripts/render_pages.py --dir "<项目文件夹>" --dry-run

# 换分类名 / 换站点外观配置 / 不要侧栏互链
python scripts/render_pages.py --dir "<项目文件夹>" --category 业界动态 --site my-site.json --no-sidebar
```

- 输出默认落在**稿件同目录**，文件名与稿件一一对应（`01-某企业-稿件.txt` → `01-某企业-稿件.html`）。
- `--short-id` 时文件名改成 **8 位短 ID**（由标题哈希得到，同一标题永远得到同一个 ID），
  并额外写一份 `_pages.json`，记录 `文件 ↔ ID ↔ 标题 ↔ 源稿件`，供核对与上传使用。
  **这份清单不要上传到公开目录**——里面是本机绝对路径。
- 稿件格式与 `scripts/draft_check.py` 完全一致：第一行 `标题：…`，其后每个非空行是一段正文。
- 稿件里若混进了「稿外说明 / 来源清单 / 官网启用依据」一类标记，该行起的文字**不会**写进网页，
  脚本会在结束时提示。

---

## 2. 页面里放什么（字段映射）

| 页面位置 | 取自 | 说明 |
|---|---|---|
| 浏览器标题 | `标题_分类_站名` | 与参考页同样的拼法 |
| `<h1 class="article-title">` | 稿件标题 | 逐字，只做 HTML 转义 |
| 分类 / 时间行 | 站点配置的 `category` + 稿件文件时间 | 见 §5 第 5 条的时间语义 |
| 正文段落 | 稿件正文 | 每段一个 `<p>`，逐字，不增删改写 |
| 右侧「最新动态」 | **站点索引**（所有已生成/已上线的页面） | 随机抽 10 篇（排除本页），按页面时间倒序展示；跨批次互链 |
| 右侧「下一篇」 | 本次抽到的第一篇 | 跟着随机结果走，保证一定链得通 |
| 「点击阅读更多内容」 | 站点配置的 `category_path` | 指向参考站对应的栏目页 |
| 导航 / 页脚 / 备案号 | 站点配置 `site.json` | 与参考页相同的 7 个栏目与页脚文案 |

**侧栏互链 = 站点索引 + 随机抽样**。索引在 `~/.dsh/fabu-deepseek-harness/site-index.json`，
每次 `render_pages.py` 都先把本批页面 upsert 进去、再渲染，所以：

- 同一页面在不同批次里出现时，能与**所有历史页面**互链，不只是同批；
- 每页抽到的 10 篇组合都不同（扩大内链覆盖），抽完仍按时间倒序排，看起来仍是一条"最新动态"；
- 随机种子默认 `--seed page`（按页面 ID 固定）→ 同一页面重复生成结果一致，
  重传不会让侧栏无故变化；`--seed random` 则每次重抽；
- `--sidebar-count N` 改条数（默认 10）、`--pool-latest M` 只用最新 M 篇做抽样池、`--index` 换索引文件。

索引里没有其它页面时，两张卡片都不生成——宁可侧栏空着，也不编造条目（对照 §12 禁止清单：不虚构）。

---

## 3. 站点外观怎么改

改 `assets/page/site.json` 就行，不用碰 CSS：

```json
{
  "site_name": "北京知识产权研究会",
  "site_domain": "北京知识产权研究会.网址",
  "site_origin": "http://xn--wlqj54a59k1y5a5dwqjai2ot90c.xn--ses554g",
  "category": "业界动态",
  "category_path": "/hangyedongtai",
  "nav": [ { "label": "网站首页", "path": "/" }, ... ],
  "footer": { "org": "...", "addr": "...", "copyright": "...", "icp_text": "..." }
}
```

`path` 以 `/` 开头的会拼上 `site_origin`，因此导航与页脚指向参考站的**真实地址**；
页面本身仍然是本地单文件，不依赖同目录的其它文件。

---

## 4. 素材目录（`assets/page/`）

| 文件 | 是什么 | 怎么来的 |
|---|---|---|
| `site.json` | 站名 / 导航 / 页脚 / 分类等文本配置 | 照抄参考页 |
| `template.html` | 页面骨架（`{{…}}` 占位符） | 按参考页的 DOM 结构重写为静态骨架 |
| `site.css` | 全部样式，数值取自参考页的计算样式 | 见 §5，实测值不是估值 |
| `site-logo.png` | 站点标识 226×226 | 参考页原图 5906×5906（382KB）→ 裁掉透明边 → 缩到 2 倍显示尺寸（113px×2）→ 23KB |
| `iconfont.woff` | 分享按钮的图标字体 | 参考页 `@font-face: socialshare` 的 woff，6.4KB |

**为什么必须内联**：参考站的 CDN（`aipage-resource-gz.cdn.bcebos.com`）对 CSS 和图片都做了
防盗链——不带该站 `Referer` 直接请求一律 **403**。所以生成的页面不能外链这些资源，
站标与图标字体必须转成 data URI 内联，产物才能离线打开、放到任何地方都不掉样式。
（抓素材时要带 `Referer: <参考页地址>`，否则下载不下来。）

---

## 5. 保真度：与参考页逐项一致的实测值

下列数值由 Playwright 在 **1440px 视口**下读参考页的 `getComputedStyle` 与
`getBoundingClientRect` 得到，`site.css` 按这些数值写，`scripts/page_check.py` 按这些数值核对。

| 元素 | 实测（x, y, w, h） | 关键样式 |
|---|---|---|
| 头部区块 | 0, 0, 1440, 200 | 白底 |
| 站点标识 | 119, 32, 113, 113 | 内容区左起 −1px |
| 站名块 | 232, 67, 327, 48 | 33px / 加粗 / #333 |
| 官方网址块 | 239, 115, 339, 21 | 14px / 加粗 / letter-spacing 4px |
| 导航条 | 120, 150, 1200, 50 | 红 `#c02437`，悬停 `#bb1a1a`；下拉 `#333`，13px `#dedede` |
| 正文区 | padding 70px 0 | 容器 1200 居中；左栏 860、右栏 300、栏距 40 |
| 文章标题 | 120, 270, 860, 34 | 24px / 行高 1.4 / #333 / 下边距 20px |
| 分类时间行 | 120, 324, 860, 20 | 14px / #999 |
| 正文段落 | 120, 353 起 | 14px / 行高 2 / #666 / 段间距 10px |
| 分享图标 | 32×32 圆形 | 字形 20px；微博 `#ff763b`、微信 `#7bc549`、QQ `#56b6e7`、空间 `#fdbe3d`、豆瓣 `#33b045` |
| 侧栏卡片 | 1020, 270, 300 | 1px `#ececec` + 上边框 3px `#00a0e9`，圆角 3px，内边距 15/16/40 |
| 侧栏标题 | 高 37px | 14px / 行高 25px / #262626；蓝色小竖条 2×10px；标题下 0.5px 短横线（含 10px 上边距） |
| 侧栏条目 | — | 标题 14px/20px `#262626`，时间 12px/18px `#a7a7a7`，`li` 内边距 5px 0 15px |
| 「点击阅读更多」 | 高 40px | 蓝底 `#00a0e9`、白字、`padding-bottom: 4px` |
| 「下一篇」卡片 | — | 同款卡片；标题 16px/25px，链接 14px/20px |
| 页脚 | 1440, 250 高 | 深灰 `#414141`；顶部通栏红条 52px `#c02437` |
| 页脚文案 | 距页脚顶 72 / 104 / 137 / 168 / 215px | 15px 白色；版权行 14px `#8b8b8b`，从内容区左起 348px |
| 备案条 | 1440×24 | 底 `#f8f8f8`，居中 12px，链接 `#888` |

**踩过的坑（改样式时注意）**：侧栏两个标题下面的短横线来自 `.sidebar h3::after`
（0.5px 边框 + 10px 上边距），它同时决定「最新动态」标题块高 37px。
只给「下一篇」加这条、漏掉 `.uk-h4` 那个 `h3`，整个列表会**整体上移 12px**。

---

## 6. 有意与参考页不同的地方（六处，都是取舍不是失误）

1. **站名字体**：参考页从百度 CDN 载入 `FZKai-Z03S`（方正楷体，2MB）。
   本模板不引外链，字体栈写成 `FZKai-Z03S, KaiTi, STKaiti, 楷体, serif`：
   装了同名字体则完全一致，没装则退到系统楷体（字形接近，不是同一款字）。
2. **正文首行缩进**：参考页靠正文里 3 个不换行空格（约 1.3em）实现；
   本模板用 CSS `text-indent: 2em`（中文排版常规，且不往正文里塞不可见字符）。
3. **微信分享二维码弹窗**：参考页用运行时 canvas 生成二维码；本模板保留微信圆形图标，
   但不做弹窗（其余四个图标照参考页给出分享链接）。
4. **分享链接的 url 参数**：参考页写死该页绝对地址；本模板在运行时取 `location.href`，
   本地打开或部署后都指向本页，不会把分享指向参考站的那篇文章。
5. **页面时间的语义**：取**稿件文件的最后修改时间**（或用 `--datetime` 统一指定），
   它是页面生成时间，**不是发布时间**。稿件尚未发布时，交付说明里不得把它说成发布时间。
6. **导航与页脚链接**：指向参考站的真实绝对地址（点得动），而不是参考页里的相对路径
   （本地文件用相对路径必然 404）。

---

## 7. 核对门禁（生成后必须跑）

```bash
python scripts/page_check.py --dir "<项目文件夹>"
python scripts/page_check.py --dir "<项目文件夹>" --txt-dir "<稿件目录>"   # 页面与稿件不在同一目录时
python scripts/page_check.py "<某个页面.html>" --no-layout                # 只跑静态检查
```

三组检查：

| 组 | 查什么 | 为什么 |
|---|---|---|
| **A 自包含** | 页面不得有 `<link>` / `<script src>` / 外部 `img src` / CSS `url()` / `@import` 外链；站标与图标字体必须是 data URI | 掉一个外链就会出现「本地打开样式全丢」（CDN 防盗链 403） |
| **B 内容** | 标题与每一段正文**逐字**来自同名稿件；段落数一致；侧栏互链都有对应页面（本地存在，或在站点索引里登记，允许跨批次）；页面无制作过程用语 | 防止页面与稿件脱节、防止侧栏出现编造或失效的条目 |
| **C 版式** | 关键元素的坐标/尺寸与 §5 的实测值一致（容差 ±1px）；页脚各行距页脚顶的偏移；红条高度 52px | 样式被误改或参考页改版时会立刻暴露 |

退出码 0 = 全部通过，1 = 有未通过项。没装 Playwright 时 C 组自动跳过并在结论里写明，
A、B 两组照跑。

---

## 8. 参考页改版了怎么重测

版式数值是**测出来的**，不是估出来的；参考页改版后按同样办法重测，不要凭感觉调 CSS。

1. 用 Playwright 打开参考页，读计算样式与几何：

   ```python
   cs = getComputedStyle(el); r = el.getBoundingClientRect()
   # 需要的字段：fontSize, lineHeight, fontWeight, color, backgroundColor,
   #            padding, margin, borderTop, borderRadius, letterSpacing, textAlign
   # 以及 r.x / r.y / r.width / r.height
   ```

   元素定位用参考页上的元素 id（如文章区 `#a6d25a3d68a90bc .article-title`、
   导航 `#aec4daf691ab798 .menu-list`、侧栏 `#a6d25a3d68a90bc .sidebar .border-1`、
   页脚 `#a59b6a2d17af7c6`）——这些 id 是建站平台生成的，改版后可能变，变了就按新的
   DOM 结构重新定位。
2. 把差异落到 `assets/page/site.css` 的对应规则上（文件里的数值都标了来源）；
   若差异来自元素结构本身，再动 `template.html`。
3. 更新 `scripts/page_check.py` 顶部的 `EXPECT_BOX` / `EXPECT_FOOTER_OFFSET` 常量，
   让门禁跟着一起走——否则门禁会拦住正确的改动。
4. 重新生成一页、跑一次 `page_check.py`，并用截图与参考页并排看一眼。

**注意**：参考页的站名/导航/页脚属于该站，素材是从该页抓下来复刻版式用的。
换站点时改 `site.json`，不要把别的站点的标识混进这一套素材里。

---

## 9. 脚本清单

| 脚本 | 作用 |
|---|---|
| `scripts/render_pages.py` | 稿件 → 单文件 HTML（内联样式 / 站标 / 图标字体），`--short-id` 产出可上传的短 ID 命名 |
| `scripts/page_check.py` | 网页门禁：自包含 + 内容一致 + 版式与参考页一致 |
| `scripts/ftp_upload.py` | 上传到 FTP 服务器：建目录、按 `--pattern` 上传、逐文件核对远端 SIZE |

前两个是纯标准库；`page_check.py` 的 C 组需要 Playwright（读渲染后的真实几何）；
`ftp_upload.py` 也是纯标准库。

---

## 10. 上传到服务器（用户要求并给出目标时才做）

```bash
# 1) 生成 + 门禁（短 ID 模式）
python scripts/render_pages.py --dir "<项目文件夹>" --short-id
python scripts/page_check.py   --dir "<项目文件夹>/article-detail"

# 2) 先预览，再上传；密码只从环境变量取
python scripts/ftp_upload.py --local-dir "<项目文件夹>/article-detail" \
    --remote-dir "/article-detail" --host <主机> --user <账号> --dry-run
FTP_PASS=... python scripts/ftp_upload.py --local-dir "<项目文件夹>/article-detail" \
    --remote-dir "/article-detail" --host <主机> --user <账号>
```

上传后**必须用真实域名打开一次**，把公开链接写进交付说明。只报"已上传"不报链接、
或把没验证过的链接当可用链接交出去，都算未完成。凭据不落盘（脚本、配置、清单、交付说明都不写）。

### 实测踩过的两个坑

**坑 1：登录成功，但一传就超时（TimeoutError）**

FTP 的控制连接（21）与数据连接是两条独立的 TCP 连接。被动模式下，服务器会临时开一个高位端口
（实测落在 **39065–39924**）让客户端回来连；这段端口没在**云安全组 / 系统防火墙**放行时，
表现就是：`login()` 成功，接着 `nlst()` / `storbinary()` 全部 `TimeoutError`。
主动模式在这种情况下也救不了——本机在 NAT 后面，服务器会拒绝
（实测报 `I won't open a connection to 10.0.72.45 (only to 8.152.231.248)`）。

- 判据：连续取样多个 PASV 端口全部超时，而同主机普通端口（80/443/22）可连 → 是服务器侧过滤。
- 处理：让服务器侧放行被动端口段（腾讯云安全组入站规则 + firewalld/iptables），再重试。
- 顺带：以 IP 直连 FTP 时用 `NatSafeFTP`（`makepasv()` 改连控制主机）可防服务器在 NAT 后
  返回内网地址——`ftp_upload.py` 已内置。

**坑 2：文件传上去了，网址却 404（FTP 家目录 ≠ 网站根目录）**

FTP 账号被 chroot 在自己的家目录里（`CWD ..`、`/www`、`/home` 一律 550），
**这个家目录不一定是 Web 服务器的 docroot**。实测同一台服务器上两者都放着建站面板的默认
`index.html`，但**不是同一个文件**（707 字节 / 16:56 vs 732 字节 / 10-14，ETag 不同），
往 FTP 根目录传的探测文件在 80 端口取不到。

- 判据（一条就够）：往 FTP 根目录传个小文件，立刻用 HTTP 取；取不到就不是同一个目录。
- 追加判据：比较 FTP 里 `index.html` 与 `http://<host>/index.html` 的字节数 / md5 / `Last-Modified`。
- 注意**按 IP 访问可能走面板的默认站**，而 FTP 家目录是某个**域名**的站点根。
  用域名 + `Host` 头验证一遍，往往就正常了（本次即如此：IP 直连 404，域名访问 200）。
- 所以上传的验收标准不是"FTP 返回 226"，而是**域名能打开**。先验证再报链接。
