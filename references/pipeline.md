# 端到端流程：表格 → 稿件(+科普) → 网页 → 自媒体 → FTP → 回写

一句话：**读表格文档 → 生成案例稿与配套科普稿、各出一份网页 → 用脚本把每篇都发到搜狐号＋头条号
（分散到不同账号）→ 把网页传到 FTP → 把自媒体链接和网页链接写回表格。**

七个步骤（其中 2b 是科普稿），每一步都有硬门禁；**门禁不过就不进下一步**。
任何一步失败都停在原地报告，不要"跳过继续"，也不要手工补一个看起来对的值。

| # | 阶段 | 用什么 | 产出 | 通过标准 |
|---|---|---|---|---|
| 1 | 读表格 | 只读打开（openpyxl） | 内部资料摘要 | 列义已逐列样例核对；编号/域名/企业名称齐 |
| 2 | 写稿 | §4–§6 的检索与写作规则 | 每篇一个 `NN-*-稿件.txt` | `draft_check.py` 全绿 + `similarity_check.py` 达标 |
| 2b | 配科普稿 | §6.4 + `kepu_plan.py` + `kepu_check.py` | 每篇一个 `NN-科普-方向K-*.txt` | 方向按序轮换；`kepu_check.py` 全绿 |
| 3 | 出网页 | `render_pages.py --short-id`（两次）+ `page_check.py` | 两类页面 + `_pages.json` / `_pages-科普.json` | `page_check.py` 全绿 |
| 4 | 发自媒体 | `publish_params.py` → `assign_accounts.py` → `egolite_publish` / `egolite_script` | 每个**单元**（=1 篇案例稿＋配对科普稿）× 搜狐号＋头条号各一条，共 4 条链接 | **每篇都发两个平台**；**每账号每天 3 案例 + 2 科普**（当天最多 10 个单元，受 5 搜狐号 × 2 科普槽限制）；配对优先同号、槽位不够才拆号；逐条 dry run；链接过审后按平台回查 |
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
  若存在媒体列，其表头是账号代号（如 `a-1`/`b-1`）而不是中文列名；
  **当前这批表格没有媒体列**（表头只有 编号/状态/域名/公司名称/公司简称/企业信息资料/发布链接/网页链接），
  账号归属就记在**台账**里，**不要为了记账号擅自加列**。
- 必填：企业全称、完整域名；选填：简称、企业资料、服务对象。
- 出现"尚未启用／域名与企业对不上／后缀不是 .网址"等冲突，**先问再写**（§2.5）。
- 篇数由用户指定；资料行数多于要求时说明选了哪几行，不自行增补企业。

## 2. 阶段二：写稿

按 §3–§6 执行：确认启用口径 → 检索行业近期消息 → 为每篇定独有报道角度 → 写 → 自检。

**所有联网查询一律走 AnySearch**（`scripts/anysearch_fill.py` 或官方 CLI 的 `search` / `batch_search` /
`extract`），不用内置 `web_search` / `web_fetch`。

```bash
python scripts/draft_check.py     --dir "<项目文件夹>"   # H1/H2/H3–H7 字面门禁
python scripts/similarity_check.py --dir "<项目文件夹>"  # 正文 ≤10%、标题 ≤15% + 标题骨架/句式部件
python scripts/style_check.py     --dir "<项目文件夹>"   # 人化（去 AI 腔）+ 去广告腔
```

缺企业资料时，**先自动补料再问**：`scripts/anysearch_fill.py --company "<全称>"`；
需要读原文就用 AnySearch CLI 的 `extract "<url>"`。

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

### 4.2 分配账号（**一稿多平台 ＋ 每账号每天 3 案例 + 2 科普**）

账号池**从注册表派生**，不写死清单。分配单位是**发布单元**（案例稿＋配对科普稿），
但落位颗粒度是**篇 × 平台**：

- **每篇都要发到搜狐号和头条号各一次**（一次任务覆盖多个媒体）；同一平台内一篇稿只发一个账号；
- **搜狐号每账号每天 5 篇，按 3 篇案例 + 2 篇科普切分**：一个号当天最多 3 个单元的案例、2 个单元的科普；
- **配对优先同号**（案例先发、科普随后）；那个号某类槽位满了，脚本自动**拆号**并在报告标出；
- 当天放不下的单元进 `blocked_units`，**必须写进交付说明**（顺延次日或加账号）；要直接报错就加 `--no-wrap`；
- 这一批**用哪几个**账号，按**累计已发布篇数**最小者优先，并列取账号代号升序（可复现）。

```bash
python scripts/assign_accounts.py --accounts accounts.json --ledger 发布台账.jsonl \
    --numbers 1,2,3 --only-media sohu,toutiao
python scripts/assign_accounts.py --accounts accounts.json --numbers 1,2,3 --no-wrap   # 放不下就报错
```

看报告里的 **"分布判定"** 与 **"每账号当日额度"** 两行：要求没有人超 3 案例 / 2 科普，
且没有 `⚠ 放不下的单元`。
规则与负载来源分级见 SKILL §7.2 与 `references/account-assignment.md`。

**当前注册表：12 个账号**（搜狐号 `a-1`/`b-1`/`c-1`/`e-1`/`f-1`，头条号 `a-2`/`b-2`/`c-2`/`e-2`/`f-2`/`d-2`/`g-2`）。
因为每篇案例必配 1 篇科普，**搜狐侧的科普槽（5 × 2 = 10）是瓶颈**：
**当天最多落 10 个单元**，5 个搜狐号各接 2 个单元（2 案例 + 2 科普 = 4 篇），每号还剩 1 个案例槽。
这些账号刚加进注册表、**尚未登录**，没有登录态就派不了活——先登录再分配。
写稿前先算：要写 15 篇就得接受第 11–15 篇顺延到次日（或加搜狐号）。

### 4.3 平台差异（实测，配置在 recipe 里）

| 平台 | 账号 | 标题上限 | 额外参数 | headless | 链接什么时候能拿到 |
|---|---|---|---|---|---|
| 搜狐号 | 注册表 `media: sohu`（当前 5 个） | 72 字 | `declaration`（默认「无需声明」） | 可用 | **过审后**回查（`later_backfill`） |
| 头条号 | 注册表 `media: toutiao`（当前 7 个） | **30 字** | `keyword`（免费图库检索词，必配封面图） | **必须 false** | **过审后**回查 |
| CSDN | 注册表里确有 `media: csdn` 的已登录账号时才用（当前**没有**） | 100 字 | `tag` | 可用 | **发布即得**（`immediate`） |

- **账号代号以注册表为准**：`b-1`/`c-1` 现在是搜狐号、`c-2` 是头条号；
  旧的"`a-1` 搜狐 / `a-2`、`b-1` 头条 / `c-1` CSDN"已过时，**不要照旧派活**。
- 稿件标题按 §6 H2 本来就在 30 字以内，搜狐号与头条号都能直接发；`publish_params.py` 会逐平台标出是否超限。
- 搜狐号每账号每日限 5 篇（recipe `daily_limit`），按 **3 篇案例 + 2 篇科普** 切分使用
  （`assign_accounts.py` 默认即此口径，`--case-daily 3 --kepu-daily 2`）；
  头条的 `headless` 必须是 `false`，headless 发布会**静默拦截**。
- recipe 里的 `account_codes` 是录制时的元数据，**不是派活白名单**；派哪个账号看 §4.2 的分配结果。

### 4.4 先彩排，再真发（逐平台 × 两篇，共 4 条）

一个"发布单元" = 案例稿 + 它配对的科普稿，**每篇都要发到搜狐号与头条号各一次**；
同一账号内**案例稿先发、科普稿随后**。
**每个单元用 §4.2 分配结果里的 `accounts.sohu` / `accounts.toutiao`**——不要所有单元都用同一个账号
（见 §4.2 的分散规则）。下例的 `accountCode` 一律写占位符，真实执行时替换：

```bash
# 本机发布工具是 ego-publish 插件（egolite_*），流程 id 与老版 recipe 同名
# 0) 先看账号与流程是否就绪（不启动浏览器）
egolite_status

# 1) 彩排案例稿（搜狐）：dryRun 默认 true，会填完表单并截图，但**跳过提交**
egolite_publish {accountCode:"<accounts.sohu>", flow:"sohu__publish-article",
                 params:{title:"<案例稿标题>", body:"<正文，\\n 分段>"}}
# 2) 彩排科普稿（同一搜狐账号；标题/正文取 jobs.json 的 kepu.title / kepu.body）
egolite_publish {accountCode:"<accounts.sohu>", flow:"sohu__publish-article",
                 params:{title:"<科普稿标题>", body:"<科普正文>"}}

# 3) 真发（搜狐）：案例稿先、科普稿随后；必须显式 dryRun:false
egolite_publish {accountCode:"<accounts.sohu>", flow:"sohu__publish-article",
                 params:{...}, dryRun:false}
egolite_publish {accountCode:"<accounts.sohu>", flow:"sohu__publish-article",
                 params:{kepu 的 title/body}, dryRun:false}

# 4) 换到头条号，把同一个单元的两篇再走一遍（头条必须 headless:false + 配图检索词）
egolite_publish {accountCode:"<accounts.toutiao>", flow:"toutiao__publish-article",
                 params:{title:"<案例稿标题>", body:"<正文>", keyword:"工业制造"},
                 dryRun:false}
egolite_publish {accountCode:"<accounts.toutiao>", flow:"toutiao__publish-article",
                 params:{kepu 的 title/body, keyword:"..."}, dryRun:false}

# 同一个账号连发多篇时，用 egolite_script 一个脚本循环最快，核心步骤固定：
#   goto 发文列表 → click「发布内容」→ waitFor addarticle → fill 标题 → fill 正文
#   → click「无需声明」（不点则发布会静默失败、URL 不跳转）→ scroll 到底 → click「发布」
#   → waitFor 回到 contentManagement/first/page（回到列表才算发成功）
```

- `title` / `body` **逐字取自 `jobs.json`**：案例稿用 `title`/`body`，科普稿用 `kepu.title`/`kepu.body`；
  `body` 就是稿件的各段用 `\n` 连接。**同一个平台的两条内容必须一致**，不要为某个平台改写。
- **不要为了省事只发一个平台**：这是用户明确要求的"一次发多个媒体"，缺一个平台就要在交付说明里
  写明"该平台未发、原因"，不得静默少发。
- **不要为了省事只彩排一篇**：两篇的标题长度、配图关键词、正文体型都不同，彩排结果不能互相替代。
- 每个账号**串行**执行，同一账号不要并发（见 §7.4）；一个单元给该账号增加 2 篇的负载
  （搜狐每天 5 篇、按 3 案例 + 2 科普切分，所以一个账号一天通常接 2 个单元，最多 3 个单元的案例）。
- 失败时读 run 目录里的 `run.json` 与失败步截图；登录失效先
  `egolite_account {op:"login", code:"<账号代号>"}` 请用户扫码，再继续。
- 科普稿失败不影响案例稿已经发布成功的事实：**两篇的成败各自判断**，别把一对当成一笔；
  **两个平台也各自判断**——搜狐成功不代表头条成功。

### 4.5 拿链接：过审后回查

```bash
# ego-publish 没有独立 backfill 命令：过审后到该账号「我的内容」列表读 h3 a 的 href
egolite_act {accountCode:"a-1", action:"goto",
             url:"https://mp.sohu.com/mpfe/v4/contentManagement/first/page"}
egolite_act {accountCode:"a-1", action:"eval",
             expression:"...读取 li 里 h3 a 的 href 与状态..."}
# 已发布条目 href 形如 https://www.sohu.com/a/<id>_<accountId>；审核中仍是 articlepreview 预览链
```

- 搜狐/头条发布后是**审核中**，此时只有预览链，不能当公开链接用
  （搜狐要拒掉 `articlepreview`，只收 `www.sohu.com/a/`）。
- 还在审核的行：**状态停在 `已写作`，发布链接列留空**，交付说明写"待过审后回填"。
  不要为了把状态推到"已发布"而填一个预览链或猜一个链接。
- **回查要按「编号＋平台」各做一次**（同一篇在搜狐、头条各有一个链接）；
  **科普稿也要单独回查**，拿到的链接用 `kepu_plan.py links` 记进台账。
- 发布记录（时间/编号/平台/账号/链接）追加到台账，供下次分配账号算负载。

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
交付批次：<批次名>    共 <N> 个发布单元（每单元 = 案例稿 + 科普稿，每篇发搜狐号＋头条号）
账号分配（每账号每天 3 案例 + 2 科普，搜狐号上限 5 篇）：
  <n> → 搜狐 案例:<号> 科普:<号> ／ 头条 案例:<号> 科普:<号>
  分布结论：<N> 个单元用到 <M> 个账号；当天容量 <C> 个单元（= 账号数 × 科普槽）
  拆号：<无 | 编号 x 在搜狐侧拆号，原因：<号> 的科普槽已满>
  顺延：<无 | 编号 y、z 放不下，顺延到 <次日>，原因：当天科普槽用尽>
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
