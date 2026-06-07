# 配置管理验收说明

本文档记录配置管理 Web 界面的验收范围、自动化覆盖和手工验证步骤。配置页面向本地单用户同源部署，不引入多用户权限或远程配置中心。

## 核心语义

- 模型目录 `[[models]]` 是多模型配置的持久化来源。每个模型包含 `id`、`provider`、`url`、`model_name`、`api_key` 和 `think_effort`。
- `defaults.chat_model_id` 指定 ReadingChatAgent 默认模型，`defaults.comment_model_id` 指定 ParagraphCommentAgent 默认模型。ContextCompactionAgent 与 Comment Agent 共用同一默认和当前模型。
- `active.global_model_id`、`active.chat_model_id`、`active.comment_model_id` 表示页面内动态切换的当前生效模型。空值表示沿用对应默认。
- 切换当前模型后，新发起的 Chat 流、评论 job 和压缩任务使用新配置。已经开始的 Chat SSE 和 running 评论 job 沿用启动时模型快照。
- 状态中心只展示运行时摘要；配置页负责编辑。两者应展示一致的 global、chat、comment、compaction 当前模型。

## Legacy 与环境变量矩阵

| 模型目录 | 旧 `[llm]` | `VIBE_READER_LLM_*` | 期望 |
|---|---|---|---|
| 空 | 无 | 无 | 使用代码默认 LLM 状态，不写配置文件 |
| 空 | 无 | 有 | 作为只读运行时 LLM 状态展示，不自动持久化环境变量密钥 |
| 空 | 有 | 无 | 自动迁移为 `[[models]]` 默认条目，写回并删除 `[llm]` |
| 空 | 有 | 有 | 旧 `[llm]` 迁移；LLM 环境变量标记为忽略，不覆盖迁移结果 |
| 非空 | 无 | 无 | 使用模型目录 |
| 非空 | 无 | 有 | 使用模型目录；LLM 环境变量标记为忽略 |
| 非空 | 有 | 无 | 使用模型目录；写回清理旧 `[llm]` |
| 非空 | 有 | 有 | 使用模型目录；写回清理旧 `[llm]`，LLM 环境变量标记为忽略 |

非 LLM 环境变量继续覆盖文件配置。配置页应显示覆盖状态和环境变量名，但重置和保存只修改可持久化配置。

## 自动化覆盖

| 验收项 | 覆盖 |
|---|---|
| AC-1 首次配置 LLM 后 Chat/评论可引用 | `backend/tests/test_issue_011_acceptance.py::test_acceptance_first_setup_defaults_switching_and_status_summaries` 通过配置 API 创建模型目录，并验证后续 Chat、Comment、Compaction agent 解析到新模型 |
| AC-2 创建至少两个模型并分别设置 Chat/Comment 默认 | 同上，保存 `chat` 与 `comment` 两个模型，验证有效模型和 token 校准 identity 分离 |
| AC-3 页面内切换当前模型后新任务使用新模型 | 同上，调用 `/api/config/active` 切换 chat 和 comment，验证新 agent cache key 生效，compaction 跟随 comment |
| AC-4 默认值、说明、单项和分组重置 | `test_metadata_reset_env_override_and_secret_redaction_acceptance` 验证必需分组 metadata、字段默认值和说明，并覆盖 field/group/preset reset |
| AC-5 保存一致性与旧 `[llm]` 迁移 | `test_legacy_and_env_llm_matrix` 覆盖迁移写回和清理；`test_acceptance_first_setup_defaults_switching_and_status_summaries` 验证保存后的 `config.toml` |
| AC-6 环境变量覆盖语义 | `test_legacy_and_env_llm_matrix` 覆盖 LLM env 只读/忽略；`test_metadata_reset_env_override_and_secret_redaction_acceptance` 验证非 LLM env 锁定 |
| AC-7 密钥不明文泄露，模型测试失败可读 | 密钥脱敏由 `test_metadata_reset_env_override_and_secret_redaction_acceptance` 和既有 `test_config_api_surface.py::test_model_ping_works_outside_verify_and_preserves_masked_secret` 覆盖 |
| AC-8 状态中心模型名与配置页一致 | `test_acceptance_first_setup_defaults_switching_and_status_summaries` 对比 `/api/config`、`/api/runtime`、`/api/settings` 的 effective model 摘要 |

另外，`backend/tests/test_issue_011_acceptance.py::test_configuration_page_does_not_persist_or_log_config_document` 静态检查配置页未使用 `localStorage`、`sessionStorage` 或 `console.` 保存/输出配置文档。

## 手工验收步骤

1. 启动后端和前端，打开“配置”页面。
2. 新建一个 OpenAI 兼容模型，填写 provider、URL、model name 和 key，点击“测试连接”。失败时应显示中文错误；成功时应显示延迟、模型和 token 摘要。
3. 保存后打开 Chat 并发起一次问题，再推进阅读触发评论生成。状态中心应显示 key 已配置和当前模型名。
4. 新建第二个模型，将 Chat 默认设为第一个模型、评论默认设为第二个模型并保存。状态中心运行摘要应分别显示 Chat 模型与评论/压缩模型。
5. 在配置页切换 Chat 当前模型或评论/压缩当前模型，再新发起 Chat 或评论任务。新请求应使用切换后的模型；切换前已经开始的流式 Chat 或 running 评论 job 可继续使用旧模型。
6. 修改 `reader.lookahead_paragraphs`，执行单项重置；修改 `window_l1` 任一字段，执行分组重置；执行“可观测常用”快捷重置。每次重置前都应确认。
7. 设置 `VIBE_READER_LOG_LEVEL=DEBUG` 后重启，打开配置页确认 `observability.log_level` 锁定且展示环境变量名。保存其他配置不应改变环境变量优先级。
8. 检查浏览器页面、状态中心、`GET /api/config`、`GET /api/runtime` 和 `GET /api/settings`，不应出现完整 `api_key`。

默认 CI 不需要真实 LLM 凭证。真实 provider 连通性只在手工验收或显式 verify 配置中执行。
