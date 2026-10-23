# fabu-deepseek-harness

把一张企业名单表格，变成「能发的稿子 + 上线的网页 + 填好的表格」的端到端技能（DSH skill）。

它把发布这件事拆成一条有门禁的流水线：**表格 → 稿件(+科普稿) → 网页 → 自媒体 → FTP → 回写**，
每一步都有机器检查，不通过就不许进下一步——目的是让"发出去的东西"和"表格里记的东西"始终对得上。

## 它能做什么

| # | 环节 | 产出 | 门禁 |
|---|---|---|---|
| 1 | 读表格 | 内部资料摘要 | 逐列核对列义；编号/域名/企业名称齐 |
| 2 | 写案例稿 | `NN-企业-稿件.txt` | `draft_check.py` 18 项 + 同批相似度 ≤10% + `style_check.py`（人化/去广告腔） |
| 2b | 配科普稿 | `NN-科普-方向K-*.txt` | `kepu_check.py`（正面性/篇幅/数据/标题）+ 相似度 ≤15% |
| 3 | 出网页 | 单文件 `.html` + 页面清单 | `page_check.py` 47 项（自包含/内容/版式/互链） |
| 4 | 发自媒体 | 搜狐号 / 头条号 / CSDN 链接 | 先彩排再真发；同账号成对发布 |
| 5 | 传网页 | FTP 上的公开页面 | 用**域名**打开且 md5 一致 |
| 6 | 回写表格 | 状态 + 媒体列 + 两列链接 | 备份 → 预演 → 真写 → 回读验证 |

### 几条硬规则（技能的灵魂）

- **案例稿**：企业全称＋「官网已启用」＋完整域名必须逐字出现在第 1 或第 2 自然段，
  且要**融进正文**、不许独立成段喊一句；标题必须含企业名（全称放不下才用简称）。
- **科普稿**：每篇案例稿配一篇；6 个方向（应用环境／SEO／主域名／易记／邮箱／价格）**按序号轮换**，
  台账记着指针，用完回到第 1 个。**只准写正面**，负面表述由正则拦下。
  标题必须出现**「中文域名」**＋**应用／价值／有用**一类价值用语，并建议写成**提问式**问价值点。
- **网页**：单文件、零外链（参考站 CDN 有防盗链），版式按实测像素复刻；
  侧栏「最新动态」从**站点索引**里随机抽 10 篇做跨批次互链，每页组合都不同。
- **发布与回写都要授权**：没明确要求就不发、不传、不改表格；回写只动白名单单元格。

## 安装

把整个目录放到 DSH 的 skills 目录下，目录名必须与 `SKILL.md` 里的 `name:` 一致：

```bash
git clone https://github.com/<你>/fabu-deepseek-harness.git \
  ~/.dsh/skills/fabu-deepseek-harness
```

依赖：

- Python 3.12+，`openpyxl`（读写表格）
- **AnySearch 技能（必需 · 联网查询的唯一通道）**：行业近期消息、政策与数据、原文核对、缺料补料
  全部通过它完成（`search` / `batch_search` / `extract`）。**不使用宿主内置的 `web_search` / `web_fetch`**——
  内置搜索依赖 DeepSeek 凭据，在只配了其它模型供应商（如 volcengine）的机器上必然失败。
  缺它时按 SKILL §4.8 排查并如实记录限制，不拿内置搜索顶替。
- `playwright` + chromium（可选，仅网页版式组需要；缺失时自动跳过并在结论里写明）

环境变量：

- `FTP_PASS` —— FTP 密码（技能不会把密码写进任何文件）

## 快速开始

```bash
S=~/.dsh/skills/fabu-deepseek-harness/scripts

# 1) 写稿（自己或 agent 按 SKILL.md §4–§6 写），然后过门禁
python $S/draft_check.py --dir "<项目文件夹>"
python $S/similarity_check.py --dir "<项目文件夹>"

# 2b) 科普稿：先发方向号，写完登记
python $S/kepu_plan.py plan --count 2 --out plan.json
python $S/kepu_check.py --dir "<项目文件夹>"
python $S/kepu_plan.py commit --plan plan.json --dir "<项目文件夹>"

# 3) 出网页 + 门禁（案例稿与科普稿各跑一次）
python $S/render_pages.py --dir "<项目文件夹>" --short-id --out-dir "<输出目录>"
python $S/render_pages.py --dir "<项目文件夹>" --pattern 科普 --short-id --out-dir "<输出目录>"
python $S/page_check.py --dir "<输出目录>"

# 4) 合成发布作业清单
python $S/publish_params.py --dir "<项目文件夹>" --xlsx "<表格.xlsx>" \
    --manifest "<输出目录>/_pages.json" --kepu-manifest "<输出目录>/_pages-科普.json" \
    --site-base "https://<你的站>/article-detail/" --out jobs.json

# 5) 传网页（先 --dry-run 看清单）
python $S/ftp_upload.py --local-dir "<输出目录>" --skip-existing

# 6) 回写表格（先 --dry-run，再真写；脚本自动备份 + 回读验证）
python $S/table_writeback.py --xlsx "<表格.xlsx>" --batch batch.json --dry-run
python $S/table_writeback.py --xlsx "<表格.xlsx>" --batch batch.json
```

## 目录结构

```
SKILL.md                 技能主文件（流程、写作规则、禁止清单）
references/
  pipeline.md            端到端作业手册：每步命令、失败处理、续跑
  article-page.md        网页契约：字段映射、实测版式、FTP 两个坑
  kepu-directions.md     科普稿 6 个方向库（论点/论证链/数据锚点/负面禁区/标题范式）
  table-writeback.md     回写白名单与状态机
  editorial-checklist.md 事实层/单篇/跨稿检查表
  similarity-metric.md   5-gram Jaccard 口径与整改方法
  editorial-examples.md  九类反例与修正
scripts/
  draft_check.py         案例稿门禁（H1–H7，18 项）
  similarity_check.py    同批/同系列相似度（含标题骨架/句式部件检测）
  style_check.py         风格门禁：人化（去 AI 腔）+ 去广告腔
  anysearch_fill.py      缺企业资料时自动检索补料（调用本机 AnySearch）
  kepu_check.py          科普稿门禁（K1–K6）
  kepu_plan.py           科普方向发号 + 配对台账
  render_pages.py        稿件 → 单文件网页（+ 站点索引互链）
  page_check.py          网页门禁（自包含/内容/版式/互链）
  publish_params.py      表格+稿件+页面 → 发布作业清单
  assign_accounts.py     按累计负载分配发布账号
  ftp_upload.py          FTP 上传（NatSafe 被动模式 + 字节校验）
  table_writeback.py     表格回写（备份/预演/回读）
  calibrate_metric.py    相似度阈值校准
assets/page/             站点外观：模板、CSS、logo、图标字体、站点配置
```

运行时数据（不进仓库）：

- `~/.dsh/fabu-deepseek-harness/kepu-ledger.json` —— 科普方向指针与配对记录
- `~/.dsh/fabu-deepseek-harness/site-index.json` —— 站点级互链索引
- `~/.dsh/ftp-target.json` —— FTP 主机/账号/远端目录（**不含密码**）

## 设计取舍

- **单文件网页**：样式、logo、图标字体全部内联成 data URI。参考站 CDN 对 CSS 与图片做了防盗链，
  留一个外链就会出现"本地打开样式全丢"。
- **随机互链 + 固定种子**：`--seed page`（默认）让每页的组合不同、但同一页重复生成结果不变，
  避免重传时侧栏无故变化；`--seed random` 则每次重抽。
- **相似度用字符 5-gram Jaccard**：中文没有空格，按 5 字滑窗比重合度，比"同义词替换"更难糊弄。
- **门禁宁可误拦**：`draft_check.py` 与 `kepu_check.py` 都是启发式的，误报时按提示改**稿件本身**
  （比如把企业全称放到句子开头），而不是改阈值——阈值一松，后面就没人管了。

## 已知限制

- 三个平台都需要已登录的账号；登录态失效时流程停在原地（`LOGIN_REQUIRED`），需要人工扫码。
- 搜狐号/头条号的公开链接要**过审后**才有，审核中只有预览链；技能规定预览链不算数。
- 平台标题上限不同（搜狐 72 / 头条 30 / CSDN 100）；本技能**不为平台改写标题**，
  放不下就换一篇或跳过该平台。
- 网页版式是按某个参考站在 1440px 下的实测值写死的，参考站改版需要重跑 `calibrate` 类的实测。

## License

仅供内部使用；转载或商用请先与作者确认。
