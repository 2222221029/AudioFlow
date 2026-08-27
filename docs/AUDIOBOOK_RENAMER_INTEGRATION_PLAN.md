# AudioFlow 有声书重命名能力改造计划

> 本文档是有声书重命名能力接入 AudioFlow 的完整改造计划，供后续实施时直接参照执行。
> 规则权威来源：`C:\Users\22222\.zcode\skills\audiobook-renamer\SKILL.md`（下称"技能"）。
> 文中所有文件路径、行号、函数名基于 2026-08 的 AudioFlow-main 代码调研，实施时如有偏移请以实际代码为准。

**目标**：把 audiobook-renamer 技能的规则体系与工作流完整接入 AudioFlow Docker 项目，使重命名产出与技能执行效果一致。

**已确认的三个方向**：

1. 接入 AudioFlow（不新建项目，随其现有 Docker 镜像发布）；
2. 新增「扫描挂载目录」手动处理入口（处理非 AudioFlow 下载的已有有声书文件夹）；
3. AI 两档可选（默认风险项复核 + 可开全量 AI 清洗）。

**核心原则**：AudioFlow 已有完整的 RenamePlanManager 确认门控体系（两阶段改名、回滚、BOM 映射 TSV、Agent 安全规则都挂在这套体系上），改造方式是**补齐差距、扩展入口**，绝不另写第二套重命名引擎。

---

## 一、现状结论（改造基础）

### 1.1 AudioFlow 已对齐技能的部分（不要动）

| 技能规则 | AudioFlow 实现（core/audiobook_renamer.py） |
|---|---|
| 中文数字章节号→阿拉伯（1–9999） | `_cn_number()`（行 98，实际支持到 99999，含万） |
| 章节号解析含「第+中文数字」分支 | `_CHAPTER_RE`（行 48） |
| 补零 3 位/千位自然 4 位 | `zfill(chapter_width)`（行 435） |
| 缺集预留前缀空号、不压缩 | `prefix_strategy="chapter"` + `reserve_missing_prefixes`（行 657–661）+ `missing_chapters` 报告 |
| 特殊文件占前缀位、不占集号、保持原位 | `special_files_keep_position`（行 704–717） |
| 分册检测与《总书名·分册名》建议 | 章节号回退检测 volume_index（行 654）+ `suggested_volumes`（行 724–747） |
| 全书完/大结局保留（判定与删除同机制） | `_remove_ad_blocks` 的 preserve token 机制（行 173–179）——天然规避技能「琴帝坑」 |
| "无题"保留仅去广告 | `_clean_title`（行 243–244） |
| 质量标记 `[无损]` 先剥后补 | `_parse_file_chapter`（行 254）+ 模板 `{quality}` 字段 |
| 连续同名加序号主体（连续集号分段/风格保持/前导零归一/上中下 3 段保留 4 段转数字/无后缀补缺/base 空白归一含 U+00A0/孤立上中下提示） | `_normalize_repeated_titles`（行 337–400）+ `_variant`（行 316）+ `_group_key`（行 325） |
| 符号规范（全半角/括号成对/半角标点转全角） | `_normalize_punctuation`（行 144–158） |
| 章节号-标题智能分隔（带《》无空格/不带单空格） | `smart_title_separator`（行 438–442） |
| 无标题不留扩展名前空格、Windows 非法字符 | `_format_chapter_name` + `_safe_filename`（行 78–86） |
| 计划 TSV（UTF-8 BOM、表头、旧名含扩展名） | `_write_mapping`（行 525–538） |
| 两阶段改名+回滚+幂等+mtime/size 校验 | `confirm()`（行 1107–1214） |
| 重名冲突预检 | `_refresh_plan`（行 828–875） |
| 非章节文件必列清单逐项确认、片花预告保留原标签/运营内容隔离 | `_special_kind` + special_file issue + rename_rules.py 默认动作 |
| 一次性集中确认、存疑即问（blocking） | `configure()`（行 877）+ `needs_review` 状态机 |
| AI 复核走「建议→勾选→应用→确认」门控 | `analyze_rename_plan` + `save_ai_analysis` + `apply_ai_suggestions`（行 974–1056） |
| 下载完成自动生成计划 | `schedule_rename_plan`（web_server.py 行 501） |

### 1.2 AI 接入现状（工作流对齐的基准）

- `core/agent_manager.py`：12 个 provider（含智谱 GLM，行 36，OpenAI 兼容路径 `https://open.bigmodel.cn/api/paas/v4`，默认模型 glm-4-flash），API key 在 Web UI 的 Agent 设置配置、Fernet 加密落盘 `config/agent.json`，无环境变量要求。
- `_openai()`（行 642–694）：**硬编码 `max_tokens=1024`、temperature 0.2、timeout (10,45)、无重试**——这是全量清洗的两个前置障碍（G9/G10）。
- `analyze_rename_plan`（行 339–402）：只取 blocking 未解决 issue 对应的文件（上限 120 条）发 LLM，prompt 仅 3 句话——判断知识远少于技能沉淀（G3）。
- 结构化输出靠「prompt 约定 + `_json_object` 容错解析」（行 320–337），无 JSON mode。

### 1.3 Docker 现状

多阶段 Dockerfile（node:22-alpine 构建前端 → python:3.12-slim 运行），compose 挂载 `./downloads:/app/downloads`、`./config:/app/config` 等，端口 8082，CI 自动构建 GHCR。**本次改造纯 Python/JS 代码级改动，Docker 配置零变更。**

---

## 二、差距清单（改造点来源）

| 编号 | 差距 | 技能依据 |
|---|---|---|
| G1 | 只能对 AudioFlow 下载任务生成计划，无法处理挂载目录里已有的专辑文件夹 | 技能用法：用户给出文件夹路径即处理 |
| G2 | 广告块清理只匹配圆括号 `（）/()`，不匹配方头括号 `【】` | 实测案例 `红狼群（上）【新年快乐】`、`【全书完丨感谢…】` |
| G3 | AI 风险复核 prompt 缺技能判断知识（多段式标题/整段吐槽/结尾标识/非章节分类） | SKILL.md 第一~四步 |
| G4 | AI 只覆盖风险项，无全量清洗档 | 已确认做「两档可选」 |
| G5 | 分集标记前空格不吸附：`准备 （上）` 不会变成 `准备（上）` | 2025·活人禁忌实测 |
| G6 | 双标记 `（N）（上）` 未归一为 `（N）（一）` | 2025·赘婿实测（低频，P1） |
| G7 | 分册「上/下册」建议无全角括号写法 `《总书名·分册名（上）》` | 盗墓笔记分册规则 |
| G8 | 执行成功后无收尾验证报告（残留广告扫描/结尾标识数核验/空号列表/格式校验） | SKILL.md「验证与收尾」 |
| G9 | `max_tokens=1024` 硬编码，批量场景输出必截断 | — |
| G10 | LLM 调用无重试/退避 | — |
| G11 | 部分技能实测案例无回归测试 | SKILL.md 各「实测踩坑」 |

---

## 三、改造方案

### 改造 A：规则引擎补齐（core/audiobook_renamer.py + core/rename_rules.py）

#### A1 方头括号【】广告块（对应 G2）

`_remove_ad_blocks()`（行 161–224）中，在 `_PAREN_BLOCK_RE.sub(replace_block, text)` 之后追加：

```python
_SQUARE_BLOCK_RE = re.compile(r"【([^【】]*)】")
text = _SQUARE_BLOCK_RE.sub(replace_block, text)   # 复用同一 replace_block：内容含广告词才删整块
# 空括号收尾清理（行 220 已有圆括号版，补方头）：
text = re.sub(r"[（(]\s*[）)]|【\s*】", "", text)
```

要点：结尾标识保护依赖已有的 preserve token 机制（行 173–179 在最前面执行），`【全书完丨感谢所有小伙伴的支持】` 会先把「全书完」换成保护 token 再删广告块——**勿为标识另写判定正则**，这正是技能「琴帝坑」的教训。

验收：`第350章 红狼群（上）【新年快乐】` → `红狼群（上）`；`来生我爱你（五）【全书完丨感谢所有小伙伴的支持】` → 标题保留且全书完不丢。

#### A2 分集标记前空格吸附（对应 G5）

`_normalize_punctuation()`（行 144–158）末尾、多空格归一之后追加（此时半角括号已转全角，顺序天然满足技能「先转全角再吸附」）：

```python
# 标题尾部全角括号短标记吸附前置空白：`准备 （上）` → `准备（上）`
_MARKER_ABSORB_RE = re.compile(
    r"\s+（(上|中|下|[0-9]+|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+|[壹贰叁肆伍陆柒捌玖拾]+|"
    r"[一二三四五六七八九十]+|完)）$")
text = _MARKER_ABSORB_RE.sub(lambda m: f"（{m.group(1)}）", text)
```

开关进 `rename_rules.py` 的 `DEFAULT_RULE_VALUES["cleanup"]["absorb_marker_space"] = True`，`sanitize_rules()`（行 179 起）加一行 bool 校验；`_normalize_punctuation` 需透传该开关（签名加可选参数，默认 True，注意行 460 等现有调用点）。

#### A3 双标记归一（对应 G6，P1 低优先级）

`_variant()`（行 316–322）对 `标题（2）（上）` 只会剥到 `（上）`、base 变成 `标题（2）`，导致分组错位。改法：分组前预处理匹配 `^(.+)[（(](\d+)[）)]\s*[（(]([上下])[）)]$` 的标题；仅当所在组最终走「4 段以上上中下混用转数字」路线时，改写为 `base（N）（一/二）`。**拿不准的组按技能铁律列入确认项（blocking issue）询问用户，不自动改。**

#### A4 分册上下册全角括号建议（对应 G7）

`create_plan` 的 `suggested_volumes` 生成处（行 726–742），对推断出的分册名 prelude 增加尾部上/下检测：

```python
m = re.match(r"^(.+?)([上下])$", prelude)
if m:
    suggested = f"{title}·{m.group(1)}（{m.group(2)}）"   # 盗墓笔记·大结局（上）
```

#### A5 收尾验证报告（对应 G8，新方法）

`RenamePlanManager` 新增 `verify_completed_plan(plan)`，在 `confirm()` 成功分支（行 1180 附近、写 profile 之前）调用，结果写 `plan["verification"]`：

```python
{
  "passed": True/False, "checked_at": <ts>,
  "checks": [
    {"name": "目标格式",   "passed": …, "details": [...]},  # 磁盘实际文件名 == item.target_name 全量比对
    {"name": "残留广告",   "passed": …, "details": [...]},  # 最终文件名跑 ad_keywords + ad_patterns 全量扫描，命中清零
    {"name": "结尾标识",   "passed": …, "details": [...]},  # 全书完/大结局/全书终/完结 的文件数，>1 或 0 列出（技能要求恰为 1）
    {"name": "预留空号",   "passed": …, "details": [...]},  # missing_chapters 对应前缀号确实空出、未被占用
    {"name": "扩展名前空格","passed": …, "details": [...]},  # re.search(r"\s+\.(mp3|m4a|aac|flac|wav|ogg|caf)$", name)
    {"name": "章节号标题分隔","passed": …, "details": [...]},# 带《》无空格 / 不带单 ASCII 空格，全量正则校验
    {"name": "无重名",     "passed": …, "details": [...]},  # 磁盘层重名扫描
  ],
}
```

验证失败**不回滚**（文件已落盘），仅报告；摘要并入完成通知（`schedule_rename_plan` 的 auto_safe 通知与 Web `confirm` 返回值）。开关 `validation.verify_after_execute`（默认 True）。残留广告文件用户可重新生成计划二次处理（计划流天然支持）。

---

### 改造 B：AI 两档模式（core/agent_manager.py + web_server.py）

#### B1 max_tokens 参数化（对应 G9，B3 的前置）

`_openai/_anthropic/_gemini` 与统一入口 `_complete`（行 569–576）全部加 `max_tokens: int = 1024` 参数并透传到 payload（现硬编码在行 690 附近）。现有调用点不传参，行为不变。

#### B2 档 1：风险项复核 prompt 增强（对应 G3）

替换 `analyze_rename_plan` 的 prompt（行 390–395），把技能判断知识沉淀进去：

```text
你是中文有声书文件名复核器。逐条分析提供的风险项，判断真实章节名，不得编造文件、不得输出路径、不得决定最终执行。规则：
1. 广告/运营废话必须去除：求赞求评论求订阅求收藏求分享、卖货（微信号/QQ群/公众号/联系方式）、新书推广（搜：xxx、新书上架）、更新通知与日期排期（每天中午12点更新）、加更补更爆更、冠名打赏、节日祝福、平台出品、中奖名单、带货。
2. 全书完/大结局/全书终/完结是正文结束标识，必须保留；标识周围的广告照常去除（例：第250章 大结局（求订阅、分享、评论）→ 大结局）。
3. 多段式真标题是完整标题，不要因空格切分（例：「朝里 楼里 七国」「真真假假 假假真真」）。
4. 整段都是演播者吐槽、无法切出章节名的（例：「唉呀妈呀！川哥又可以出来领盒饭了！」）选 keep，在 reason 说明，交用户决定。
5. 分集标记是标题一部分，保留：（上）（中）（下）（一）（1）（Ⅰ）（壹）等尾部标记。
6. 「无题」标题保留为「无题」，只去广告。
7. 非章节文件：片花/预告/主题曲/剧情歌/番外/花絮/楔子/序章/彩蛋=专辑内容，建议 accept 且保留原标签（片花就叫「片花」）；更新通知/直播回听/求赞/带货/作者节目（如「小川有话说」）=运营内容，建议 quarantine。
8. 疑似其他书籍的文件（文件名含与本书无关的书名号）选 keep。
9. 不确定时一律 keep；只有能从文件名和相邻章节明确判断时才 rename。
只输出 JSON，不要 Markdown。
```

#### B3 档 2：全量 AI 清洗（对应 G4/G10）

1. `agent_manager` 新增 `clean_titles_batch(album, rules, entries, max_tokens=4096)`：
   - 入参 entries：`[{relative_source, source_name（原始文件名）, current_title（规则引擎清洗结果）, chapter, unit, neighbors: [前一文件名, 后一文件名]}]`（neighbors 跨批也要取全计划上下文，在 web_server 组装）。
   - prompt 复用 B2 知识的第 1–6 条，输出契约：

     ```json
     {"suggestions": [{"relative_source": "必须完全复制输入值", "clean_title": "清洗后纯标题(不含章节号/序号)", "changed": true, "reason": "中文理由", "confidence": 0}]}
     ```

   - 带重试包装（对应 G10）：最多 3 次尝试，退避 2s/5s；仅此路径加重试，不动现有 `_request` 行为。

2. `web_server.py` 新增 `POST /api/rename-plans/<id>/ai-clean`：
   - 前置校验：计划存在、状态 ∈ {needs_review, pending_confirmation}、LLM 已配置（未配置返回 400 提示先去 Agent 设置配 key）。
   - 后台 `threading.Thread`（daemon，参照 `schedule_rename_plan` 模式）：全部章节条目按 sequence 排序、**约 40 条/批顺序执行**（不并发，防限流），每批后把进度原子落盘：`plan["ai_clean"] = {status:"running", done, total, model, started_at}`。
   - 全部完成：建议写入 `plan["ai_analysis"] = {status:"completed", mode:"full_clean", suggestions:[…], summary, model, created_at}`（**覆盖式**——全量结果覆盖此前风险项复核，因其覆盖全部条目；`save_ai_analysis` 加 mode 字段透传）。suggestion 的 action 统一：`changed ? "rename" : "keep"`。
   - 失败：`ai_clean={status:"failed", error, done}`；续跑时跳过已完成条目（幂等）。
   - 通知：完成时 `notification_manager.notify("rename_confirmation", …)`。
   - **应用复用现有 `POST /api/rename-plans/<id>/ai-apply`（`apply_ai_suggestions`）**，走正常 configure→confirm 门控，Agent 安全规则无需改动。

---

### 改造 C：手动文件夹处理入口（web_server.py + 前端）

#### C1 `GET /api/rename-plans/folders`

列出 `download_dir`（挂载目录）下的专辑文件夹：遍历目录（深度 ≤3，排除 `.audioflow-trash`、隐藏目录），目录内**直接**含音频扩展名文件即入选，返回 `{folders: [{relative_path, name, audio_count}]}`，上限 500 个。

#### C2 `POST /api/rename-plans/analyze-folder`

```python
body = {relative_path, album_title?（可选，默认取文件夹名）}
root = resolve_download_dir().resolve()
target = (root / relative_path).resolve()
# 路径安全：target 必须位于 root 内（Path.resolve 后 relative_to 校验），拒绝 ../ 穿越；必须存在且为目录
plan = rename_plan_manager.create_plan(
    task_id=f"folder:{relative_path}",
    album={"title": album_title or target.name, "platform": "", "id": ""},
    chapters=[], album_dir=target, origin_source="manual")
```

- 防重：同 `task_id`（或 album_dir）存在未完结计划时，行为对齐 `create_rename_plan_for_task`（默认返回已有，`replace=true` 时先 cancel 再重建）。
- 通知逻辑与 `create_rename_plan_for_task`（行 461–497）相同——建议把该段抽成公共函数两处复用。
- profile 隔离说明：手动计划 profile_key 为 `:书名`，下载计划为 `平台:id`，天然不冲突，无需处理。
- `create_plan` 本身已支持任意 album_dir（签名无需改），手动模式 chapters 传空即可——技能也是纯文件名解析。

#### C3 Agent 工具扩展（可选，工作量极小）

`_agent_create_rename_plan`（web_server.py 行 2797）增加可选 `folder` 参数，内部走 C2 同一实现，方便对话式触发。

---

### 改造 D：前端（frontend/src/hooks/useAudioFlowApp.js + components/Shared.jsx）

#### D1 新 actions（useAudioFlowApp.js）

- `renameFolders` state + `loadRenameFolders()`：GET folders。
- `analyzeRenameFolder(relativePath, albumTitle?)`：POST analyze-folder → 刷新 renamePlans → 打开计划详情（沿用 `runBusy` 模式）。
- `startRenameAIClean(planId)`：POST ai-clean → 开启轮询（setInterval 3s 拉 `GET /api/rename-plans/<id>`，`ai_clean.status` 到终态停止；参照现有下载页轮询模式，行 1346–1368）。
- 确认 `applyRenameSuggestions`（ai-apply 的 action）是否已存在，无则补。

#### D2 RenamePlansModal（Shared.jsx 行 2189–2285）扩展

1. **「整理本地文件夹」按钮**（弹层顶部）→ 文件夹列表（每行：相对路径、音频数、已有活跃计划则禁用按钮）→ 生成计划。
2. **「全量 AI 清洗」按钮**：LLM 未配置（agentStatus 无 key）时禁用并提示；运行中显示进度 `done/total`；完成后展示结果对照表，列：原始文件名 / 规则清洗标题（当前 clean_title）/ AI 建议标题 / 理由 / 置信度 / 勾选框，默认勾选「有改动的」，顶部「仅看有改动」过滤 + 全选，「应用所选」→ 现有 ai-apply 流程。`ai_analysis.mode` 加标签区分（风险复核 / 全量清洗）。现有风险项复核的复选框 UI 直接复用扩展。
3. **verification 报告区**：status=completed 的计划详情底部渲染 `plan.verification`（各 check 通过状态 + 空号列表 + 残留广告清单 + 结尾标识核验结果）。

---

### 改造 E：测试与文档

#### E1 tests/test_audiobook_renamer.py 增补用例（SKILL.md 实测案例回归）

| 用例 | 输入 → 期望 |
|---|---|
| 方头括号广告 | `第350章 红狼群（上）【新年快乐】` → `红狼群（上）` |
| 标识混广告（琴帝） | `来生我爱你（五）【全书完丨感谢所有小伙伴的支持】` → 标题保留、全书完不丢、verification 结尾标识=1 |
| 空格吸附（活人禁忌） | `准备 （上）`→`准备（上）`；`娶个媳妇 (上)`→`娶个媳妇（上）` |
| 前导零归一（开封志怪） | `嫁衣01…13` → `嫁衣（1）…（13）` |
| 罗马/大写数字组 | `钟楼Ⅰ…Ⅲ`→`钟楼（Ⅰ）…（Ⅲ）`；`擂台壹…叁`→`擂台（壹）…（叁）` |
| 上中下补缺（赘婿） | `秋末冬初`+`秋末冬初（下）` → `（上）`/`（下）` |
| 质量标记两种次序（遮天） | `菩提-有任何问题，请咨询主播V_xxx [无损]` 与 `五色祭坛-（搜：…） [无损]` → 广告清干净、`[无损]` 补回末尾 |
| 长句广告先于短句（赘婿） | `过年回老家暂停更新，下月一号恢复，大家新年快乐` 整段清除、无残留半截 |
| 分册上下册建议 | 分册名「大结局上/下」→ `盗墓笔记·大结局（上）`/`（下）` |
| verify_completed_plan | 正常通过；注入残留广告文件名 → 对应 check failed 且列出文件 |

#### E2 新 tests/test_rename_folder_api.py

文件夹列举（含排除 .audioflow-trash）、手动生成计划全流程（生成→确认→执行）、路径穿越 `../` 拒绝、防重复生成。Web 层测试参照 tests 目录下现有路由测试模式（Flask test_client + 临时目录）。

#### E3 新 tests/test_agent_full_clean.py

参照 test_agent_manager.py 的 mock 模式：mock `_complete` 验证分批边界（如 85 条 → 3 批）、重试触发与退避、进度落盘、建议写入 ai_analysis（mode=full_clean）、ai-apply 应用后 clean_title 生效。

#### E4 文档

README 增补「手动整理本地文件夹」「AI 复核/全量清洗两档」说明；`docs/PROJECT_STRUCTURE.md` 如有模块清单则同步。

---

## 四、实施顺序（每个里程碑独立可验证）

1. **M1 规则引擎差距**：A1 + A2 + A4 + 对应单测（纯函数级，见效最快）
2. **M2 收尾验证**：A5 + 单测
3. **M3 AI 层**：B1（必须先做，否则批量输出被 1024 截断）→ B2 → B3 + mock 单测
4. **M4 手动入口**：C1 + C2（+C3 可选）+ 单测
5. **M5 前端**：D1 + D2
6. **M6 收尾**：E1 全量回归 + 文档；`python -m unittest discover tests` 全绿

## 五、风险与实现要点（技能踩坑 → 本次实现映射）

1. **标识判定与删除必须同机制**：A1 扩方头括号时复用 preserve token 流程，勿另写判定正则（琴帝坑）。
2. **广告模式顺序约定**：MANDATORY_AD_PATTERNS 已按「长句在前/括号版在前」，后续新增模式保持该顺序（赘婿坑）。
3. **`_group_key` 用 `\s+` 归一**：Python `\s` 默认 Unicode 已覆盖 U+00A0，勿改回显式字符类（赘婿 NBSP 坑）。
4. **A3 改 `_variant` 时勿破坏「章节号差值==1 才同组」判断**（围城/家事误配对坑）。
5. **全量清洗 token/限流**：顺序批次 + 重试退避；plan JSON 若因千章建议超预期膨胀，可只存 changed 条目。
6. **手动入口安全**：resolve 后强制位于 download_dir 内，不引入任意路径参数。
7. **ai_clean 幂等续跑**：中断后 done 条目跳过；confirm 两阶段改名已保证执行幂等。

## 六、最终验收标准

- 用 SKILL.md 真实案例文件名（上文 E1 清单）造样例文件夹放入 `downloads` 挂载 → Web UI「整理本地文件夹」生成计划 → AI 复核/全量清洗 → 确认执行 → 验证报告全绿；**最终文件名与技能执行产出逐字一致**。
- 全书完恰保留 1 个、缺集空号预留、上中下补缺、前导零归一等实测案例全部通过；现有 22 个测试不回归。

## 附：明确不做的事（与技能默认一致）

- 「无题」章节不联网查原书补真实标题（SKILL.md 沉淀规则默认保留「无题」仅去广告）；后续如需可另行加。
- Docker 配置零改动：沿用现有 Dockerfile/compose/CI，AI key 仍在 Web UI 的 Agent 设置配置（智谱 GLM 为内置 provider）。
