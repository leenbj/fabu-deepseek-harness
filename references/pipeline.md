# 端到端流程：表格 → 稿件(+科普) → 网页 → 自媒体 → FTP → 回写

一句话：**读表格文档 → 生成案例稿与配套科普稿、各出一份网页 → 用脚本把两篇成对发到自媒体
→ 把网页传到 FTP → 把自媒体链接和网页链接写回表格。**

七个步骤（其中 2b 是科普稿），每一步都有硬门禁；**门禁不过就不进下一步**。
任何一步失败都停在原地报告，不要"跳过继续"，也不要手工补一个看起来对的值。

| # | 阶段 | 用什么 | 产出 | 通过标准 |
|---|---|---|---|---|
| 1 | 读表格 | 只读打开（openpyxl） | 内部资料摘要 | 列义已逐列样例核对；编号/域名/企业名称齐 |
| 2 | 写稿 | §4–§6 的检索与写作规则 | 每篇一个 `NN-*-稿件.txt` | `draft_check.py` 全绿 + `similarity_check.py` 达标 |
| 2b | 配科普稿 | §6.4 + `kepu_plan.py` + `kepu_check.py` | 每篇一个 `NN-科普-方向K-*.txt` | 方向按序轮换；`kepu_check.py` 全绿 |
| 3 | 出网页 | `render_pages.py --short-id`（两次）+ `page_check.py` | 两类页面 + `_pages.json` / `_pages-科普.json` | `page_check.py` 全绿 |
| 4 | 发自媒体 | `publish_params.py` → `assign_accounts.py` → `media_publish` | 每个账号：案例稿 + 科普稿两条链接 | 案例稿先、科普稿后，**同一账号**；各自 dry run；链接过审后回查 |
| 5 | 传网页 | `ftp_upload.py` | 远端 `article-detail/*.html`（两类页面） | 用**域名**能打开，字节数与本地一致 |
| 6 | 回写与记账 | `table_writeback.py` + `kepu_plan.py links` | 表格：状态+媒体列+两列链接；台账：科普的四个链接 | 先 `--dry-run` 复核，再真写；脚本自带回读验证 |

作业清单是贯穿第 4–6 步的中间产物：`scripts/publish_params.py --out jobs.json` 一次把
**表格（编号/域名/企业名称）+ 案例稿（标题/正文）+ 配对科普稿（标题/正文/方向）+ 两份网页清单（短 ID）**
合成一份 JSON，后面每一步都从它取数，**不要手抄标题或正文**——手抄必然导致稿件、网页、发布内容三处不一致。

---

## 1. 阶段一：读表格（只读）

```bash
python - <<'PY'
import openpyxl
wb = openpyxl.load_workbook("<来源表格.xlsx>", read_only=True)
ws = wb[wb.sheetnames[0]]
for c in range(1, ws.max_column + 1):
    col = [ws.cell(r, c).value for r in range(1, min(ws.max_row, 6) + 1)]
    print(openpyxl.utils.get_column_letter(c), col)   # 逐列打印样例，核对真实列义
PY
```

- **必须先逐列打印样例再决定读哪一列**：本类表格的表头会写错、会残留数据值，
  媒体列的表头是账号代号（`a-1`/`b-1`/`c-1`）而不是中文列名。
- 必填：企业全称、完整域名；选填：简称、企业资料、服务对象。
- 出现"尚未启用／域名与企业对不上／后缀不是 .网址"等冲突，**先问再写**（§2.5）。
- 篇数由用户指定；资料行数多于要求时说明选了哪几行，不自行增补企业。

## 2. 阶段二：写稿

按 §3–§6 执行：确认启用口径 → 检索行业近期消息 → 为每篇定独有报道角度 → 写 → 自检。

```bash
python scripts/draft_check.py     --dir "<项目文件夹>"   # H1/H2/H3–H7 字面门禁
python scripts/similarity_check.py --dir "<项目文件夹>"  # 正文 ≤10%、标题 ≤15% + 标题骨架/句式部件
python scripts/style_check.py     --dir "<项目文件夹>"   # 人化（去 AI 腔）+ 去广告腔
```

缺企业资料时，**先自动补料再问**：`scripts/anysearch_fill.py --company "<全称>"`。

两条都过才进下一步。**不要为了适配平台去改标题**（见阶段四）。

## 2b. 阶段二之半：配科普稿（每篇案例稿一篇）

每篇案例稿都要配一篇中文域名科普稿，**6 个方向按序号轮换**，用完回到第 1 个。
方向不靠人记，靠台账发号：

```bash
# 1) 发号：本轮该写哪几个方向（不改台账）
python scripts/kepu_plan.py plan --count <本批篇数> --out plan.json

# 2) 按 kepu-directions.md 里对应方向那一节写：专家视角、只写正面、以该方向论点为骨架、
#    至少 3 处数据、900–1150 字、标题 ≤30 字、不写企业名
#    文件名：NN-科普-方向K-<关键词>.txt（NN 与案例稿同期号）

# 3) 门禁 + 科普稿之间的相似度（同一脚本，显式传文件、阈值 15%）
python scripts/kepu_check.py --dir "<项目文件夹>"
python scripts/similarity_check.py 01-科普-*.txt 02-科普-*.txt --threshold 15 --title-threshold 15

# 4) 登记：把方向写进台账并推进指针（幂等，同一 plan 只记一次）
python scripts/kepu_plan.py commit --plan plan.json --dir "<项目文件夹>"
python scripts/kepu_plan.py status        # 核对 6 个方向各用了几次
```

- **不 commit 就等于这批方向没发出**，下批会重复同一个方向——这一步不能省。
- 负面表述、跳方向、手挑方向是硬错误；`kepu_check.py` 的 K1 专拦负面表述。
- 数据口径见 [`kepu-directions.md`](kepu-directions.md) §0.10：
  可给具体数字并用具名引用（用户已授权，无需另作标注），只需量级合理、同批不重复同一组数字。

## 3. 阶段三：出网页

```bash
# 案例稿
python scripts/render_pages.py --dir "<项目文件夹>" --short-id
# 科普稿（清单会写成 _pages-科普.json，不会覆盖上面那份）
python scripts/render_pages.py --dir "<项目文件夹>" --pattern 科普 --short-id
# 两类页面一起查
python scripts/page_check.py   --dir "<项目文件夹>"
```

产出 `article-detail/<8位ID>.html` 与两份清单（案例稿 `_pages.json`、科普稿 `_pages-科普.json`，
记录 文件 ↔ ID ↔ 标题 ↔ 源稿件）。版式与内容要求见 §7.5 与 [`article-page.md`](article-page.md)；
清单是本机文件，**不要上传**。

## 4. 阶段四：发布到自媒体（脚本）

### 4.1 先合成作业清单

```bash
python scripts/publish_params.py --dir "<项目文件夹>" --xlsx "<来源表格.xlsx>" \
    --manifest "<项目文件夹>/article-detail/_pages.json" \
    --kepu-manifest "<项目文件夹>/article-detail/_pages-科普.json" \
    --site-base "http://<站点域名>/article-detail/" --out jobs.json
```

它做三件事，并**在编号匹配到表格行后做身份交叉核验**：稿件里若既没有该行的企业名称、
也没有该行的域名，直接报 FAIL 停下——表传错了会把链接回写到别的企业那一行。
表格域名与稿件里的域名不一致时给"注意"提示（不留痕不算完成：交付说明里要写明这个差异）。

### 4.2 分配账号

账号池**从注册表派生**，不写死清单；按**累计已发布篇数**最小的账号分配，并列时取账号代号升序。

```bash
python scripts/assign_accounts.py --accounts accounts.json --ledger 发布台账.jsonl --numbers 1,2
```

规则与负载来源分级见 §7.2；**一稿一账号**：绝不为了覆盖更多平台把同一篇稿发到多个账号。

### 4.3 平台差异（实测，配置在 recipe 里）

| 平台 | 账号 | 标题上限 | 额外参数 | headless | 链接什么时候能拿到 |
|---|---|---|---|---|---|
| 搜狐号 | `a-1` | 72 字 | `declaration`（默认「无需声明」） | 可用 | **过审后**回查（`later_backfill`） |
| 头条号 | `a-2`、`b-1` | **30 字** | `keyword`（免费图库检索词，必配封面图） | **必须 false** | **过审后**回查 |
| CSDN | `c-1` | 100 字 | `tag` | 可用 | **发布即得**（`immediate`） |

- 稿件标题按 §6 H2 本来就在 30 字以内，三平台可直接用；`publish_params.py` 会逐平台标出是否超限。
- `a-1` 每日限 5 篇（recipe `daily_limit`）；头条的 `headless` 必须是 `false`，headless 发布会**静默拦截**。
- CSDN recipe 里写着 `c-2`，但注册表里没有 `c-2` —— 以注册表为准，不要用未注册的账号。

### 4.4 先彩排，再真发（两篇都要彩排）

一个"发布单元" = 案例稿 + 它配对的科普稿，**两篇发到同一个账号，案例稿先发、科普稿随后**。

```bash
# 0) 配方自检（改过 recipe 或平台改版后必跑）
media_recipe {op:"validate", recipeId:"sohu__publish-article"}

# 1) 彩排案例稿：dryRun 默认 true，会填完表单并截图，但**跳过提交**
media_publish {op:"run", recipeId:"sohu__publish-article", accountCode:"a-1",
               params:{title:"<案例稿标题>", body:"<正文，\\n 分段>"}, dryRun:true}
# 2) 彩排科普稿（同一账号；标题/正文取 jobs.json 的 kepu.title / kepu.body）
media_publish {op:"run", recipeId:"sohu__publish-article", accountCode:"a-1",
               params:{title:"<科普稿标题>", body:"<科普正文>"}, dryRun:true}

# 3) 真发案例稿（必须显式 dryRun:false），成功后再真发科普稿
media_publish {op:"run", recipeId:"sohu__publish-article", accountCode:"a-1",
               params:{...}, dryRun:false}
media_publish {op:"run", recipeId:"sohu__publish-article", accountCode:"a-1",
               params:{kepu 的 title/body}, dryRun:false}

# 头条必须带 headless:false，并给配图检索词（两篇各自给，关键词可以不同）
media_publish {op:"run", recipeId:"toutiao__publish-article", accountCode:"a-2",
               params:{title:"<标题>", body:"<正文>", keyword:"工业制造"},
               dryRun:false, headless:false}
```

- `title` / `body` **逐字取自 `jobs.json`**：案例稿用 `title`/`body`，科普稿用 `kepu.title`/`kepu.body`；
  `body` 就是稿件的各段用 `\n` 连接。
- **不要为了省事只彩排一篇**：两篇的标题长度、配图关键词、正文体型都不同，彩排结果不能互相替代。
- 每个账号**串行**执行，同一账号不要并发（见 §7.4）；一个单元给该账号增加 2 篇的负载
  （搜狐日限 5 篇，一批两个单元就占满了）。
- 失败时读 run 目录里的 `run.json` 与失败步截图；`LOGIN_REQUIRED` 先
  `media_session {op:"handoff"}` 让用户扫码，再 `{op:"resume"}` 继续。
- 科普稿失败不影响案例稿已经发布成功的事实：**两篇的成败各自判断**，别把一对当成一笔。

### 4.5 拿链接：过审后回查

```bash
media_publish {op:"backfill", recipeId:"sohu__publish-article", accountCode:"a-1", title:"<标题>"}
```

- 搜狐/头条发布后是**审核中**，此时只有预览链，不能当公开链接用
  （搜狐要拒掉 `articlepreview`，只收 `www.sohu.com/a/`）。
- 还在审核的行：**状态停在 `已写作`，发布链接列留空**，交付说明写"待过审后回填"。
  不要为了把状态推到"已发布"而填一个预览链或猜一个链接。
- **科普稿要单独回查一次**（标题不同），拿到的链接用 `kepu_plan.py links` 记进台账。
- 发布记录（时间/编号/账号/平台/链接）追加到台账，供下次分配账号算负载。

### 4.6 一条合规提醒

搜狐号的创作声明默认是「无需声明」。如果稿件是 AI 生成、是否如实选择「含有AI生成内容」，
**由用户决定并留痕**；不得为了过审而虚假声明。

## 5. 阶段五：上传网页到 FTP

```bash
python scripts/ftp_upload.py --local-dir "<项目文件夹>/article-detail" --remote-dir "/article-detail" --dry-run
FTP_PASS=... python scripts/ftp_upload.py --local-dir "<项目文件夹>/article-detail" --remote-dir "/article-detail"
```

- 目标主机／端口／账号／远端目录可以存在 `~/.dsh/ftp-target.json`（用 `--save-config` 生成），
  **密码永远不进配置**，只从 `FTP_PASS` 环境变量取。
- 上传后脚本逐文件核对远端 SIZE；**再自己用域名打开一次**：
  `http://<域名>/article-detail/<ID>.html` 必须 200，且与本地 md5 一致。
- 只按 IP 访问可能落到建站面板的默认站点（404），验收一律以域名为准。
- 两个实测坑（被动端口没放行、FTP 家目录 ≠ 网站根目录）见
  [`article-page.md`](article-page.md) 第 10 节。

## 6. 阶段六：回写表格（用户授权后）

**两个链接列**：

| 列 | 内容 | 来自 |
|---|---|---|
| 「发布链接」 | 自媒体文章链接 | 阶段四 backfill 的 URL |
| 「网页链接」 | FTP 上的网页地址 | 阶段五验证通过的 URL |

```bash
# 单行
python scripts/table_writeback.py --xlsx "<来源表格.xlsx>" --domain "某某.网址" \
    --status 已发布 --account a-1 --link "<自媒体链接>" --site-link "<网页链接>"

# 批量（推荐：由 jobs.json 生成 batch.json，见 references/table-writeback.md 的形状）
python scripts/table_writeback.py --xlsx "<来源表格.xlsx>" --batch batch.json --dry-run
python scripts/table_writeback.py --xlsx "<来源表格.xlsx>" --batch batch.json
```

**状态口径**

- `已写作`：稿件与网页已交付。
- `已发布`：自媒体已发出**且**网页已上线，两个链接都已回填。
  只到位一侧 → 状态留在 `已写作`，把已到手的那一侧链接先写上，交付说明写明缺哪一侧。
- 只前进不回退；已 `已发布` 的行不会被 `已写作` 覆盖。

**硬要求**：先 `--dry-run` 复核将改的单元格 → 真写（脚本自动备份）→ 回读验证。
定位到多行、状态口径冲突、链接列已有不同值 → **停下报告，不猜不兜底**。

### 6b. 科普稿的链接：记台账，不进表格

表格一行对应一家企业、只有两个链接列，留给**企业案例稿**。科普稿的四个链接
（自媒体 + 网页，各一对）用配对台账记录：

```bash
# links.json: [{kepu_file, account, platform, kepu_media_url, kepu_page_url,
#               company_media_url, company_page_url}, ...]
python scripts/kepu_plan.py links --json links.json
python scripts/kepu_plan.py status --limit 3      # 复核
```

这样既不动回写白名单，也不会让一行里塞四个 URL；交付说明里把科普链接一并列出即可。

## 7. 中断与续跑

每一阶段都能单独重跑，重跑是幂等的：

| 情况 | 从哪继续 | 注意 |
|---|---|---|
| 稿件写完、网页未生成 | 阶段三 | 稿件不再改动后才生成，否则网页要重出 |
| 案例稿写完、科普稿没写 | 阶段二之半 | 先 `kepu_plan.py plan` 看方向，写完 `commit`；**没 commit 下批会重复同一方向** |
| 科普稿写完但没过门禁 | 阶段二之半 | 按 `kepu_check.py` 的 FAIL 组改（负面表述/篇幅/数据/标题），别改方向 |
| 网页已生成、自媒体未发 | 阶段四 | 用 `jobs.json`，别重新手抄正文 |
| 案例稿已发、科普稿没发 | 阶段四 | 同账号补发科普稿即可；**顺序不能倒**（案例稿在前） |
| 自媒体审核中 | 阶段四的 backfill | 先回写网页链接，发布链接等过审；状态留 `已写作` |
| 网页已传、表格未回写 | 阶段六 | 先 `--dry-run`，确认只改白名单单元格 |
| 表格已回写到一半失败 | 阶段六重跑 | 脚本按行定位 + 幂等；已写入的格子报 unchanged |
| 科普链接没记 | 阶段 6b | `kepu_plan.py links` 幂等覆盖，可随时补 |

**不要重做的事**：已发布成功的稿件不要再发一次（会产生重复内容）；已回写为 `已发布` 的行不要改回；
`commit` 过的科普稿不要再 `plan` 一次（会重复占用方向号）。

## 8. 交付说明模板

```
交付批次：<批次名>    共 <N> 个发布单元（每单元 = 案例稿 + 科普稿）
每单元：
  编号 <n>  企业 <全称>  域名 <域名>
  案例稿：<文件路径>（正文 <字数> 字；draft_check 全绿；与同批最相似一篇 <x>%）
    网页：<本地路径> → <公开链接>（page_check 全绿）
    自媒体：<平台> / 账号 <代号> → <公开链接 或 「审核中，待回填」>
  科普稿：<文件路径>（方向 <k>：<方向名>；正文 <字数> 字；kepu_check 全绿；
          与同系列最相似一篇 <x>%；标题 <字数> 字）
    网页：<本地路径> → <公开链接>
    自媒体：<平台> / 账号 <代号> → <公开链接 或 「审核中，待回填」>
  表格回写：状态 <已写作|已发布>；发布链接 <有|待回填>；网页链接 <有>
  台账：科普链接已记（kepu_plan.py links）
稿外：来源清单、启用依据、行业是否推断、联网情况
差异与待办：<例如：表格域名 途度检测.网址 与稿件里的 tudu.网址 不一致，待用户确认>
```
