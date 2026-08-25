# Agent 制作控制闭环

`agent-status` 是 OnePic 制作流程的只读控制面。它把文件、审批、素材哈希和 QA
重新观察为一个确定性快照，让 Agent 每次只做一个安全动作：

```powershell
.\.venv\Scripts\python.exe .\tools\pet_workspace.py agent-status <pet-id>
```

在接手已有工作区、恢复中断任务、执行任何文件变更、收到用户审批、命令失败或
完成构建后，都重新运行。不要凭聊天历史猜测当前阶段。

## 论文原则到工程落点

本闭环参考 Fei-Fei Li 等人的
[《Agent AI: Surveying the Horizons of Multimodal Interaction》](https://arxiv.org/abs/2401.03568)
做工程映射：grounded observation 落为从真实工作区重算的 `agent-status`；
memory/evidence 落为每宠物 `workflow.json` 与哈希报告；planning/next action 落为
单一 `decision`；action 落为既有受控脚本；feedback/learning 落为 CAS 绑定的人工
批准/否决、原始反馈与构建失败诊断。这里是受论文原则启发的 OnePic 实现映射，并非
论文作者提供的原始代码或指定架构。

## 输出契约

- `phase`：从真实环境派生的当前制作阶段；
- `must_pause`：为 `true` 时必须等待报告指定的用户输入或视觉审批；
- `observation`：角色、动作、走路、过渡帧、运行目录与门禁的事实；
- `blockers[]`：尚未满足的前置条件或失效证据，含稳定 `code`、类别、原因和恢复点；
- `allowed_next_actions`：当前唯一允许的生产动作；
- `decision`：责任人、目标、理由、postcondition；无占位符的 `command`、
  `approval_command`、`rejection_command`、`recovery_command` 是可原样执行的完整
  命令；`command_template`、`rejection_command_template`、
  `recovery_command_template` 含待填占位符，`required_inputs` 是唯一允许收集的值；
- `evidence`：配置、workflow、角色、动作门禁、走路束、runtime 和 QA 哈希；
- `safety`：明确自动审批与“自动视觉检查代替用户验收”始终为 false。

快照只观察，不写 `workflow.json`，也不创造第二份审批状态。真实审批继续只保存
在每宠物 `workflow.json`，并绑定当前素材哈希。

## 执行规则

1. `decision.actor=agent`：只执行 `allowed_next_actions` 中的一个动作；完成后立即
   重新观察，不串行猜测后续步骤。
2. `decision.actor=user` 或 `agent_then_user`：可以打开本地预览或确认窗口，但随后
   必须暂停。不得把用户此前对另一张图、另一帧束或另一版本的意见当作当前批准。
   展示证据后，只有用户对本轮证据明确通过或否决时，才执行对应字段。完整 command
   原样执行；template 绝不能直接交给 shell，只能收集 `required_inputs` 明列的原始
   反馈或路径，把每项安全转义为单个参数后渲染。不得手写、删参、引入新变量或复用
   旧快照；执行后重新观察。
3. `decision.kind=repair`：只修复 blocker 指向的当前阶段。肢体、身份或道具粘连
   回到已批准标准角色；走路问题回到当前八相位；QA/配置问题保留角色素材并回到
   staging。不得用失败帧作为下一轮底图。
4. 同一证据下不要盲目重复同一失败命令。进入 `build_failure_recovery` 后先读取
   blocker、`latest-failure.json`、日志或失败 staging；只有已诊断为瞬态失败时才
   执行完整 `decision.recovery_command`，或用 `required_inputs.diagnostic_reason`
   安全渲染 `recovery_command_template`，归档该 build/输入指纹的诊断并开放一次
   重试。不能形成新观察时停止并报告，而不是回滚或扩大修改范围。
5. `awaiting_final_visual_review` 仍需展示当前 build 对应的 GIF、联系表、QA 和真实
   窗口证据。只有用户明确验收该版本后，才执行当前
   `decision.approval_command`；用户否决则用 `required_inputs.user_feedback` 安全
   渲染 `rejection_command_template`（或执行完整 `rejection_command`）并保留原始
   反馈。重新观察必须进入 `ready_to_release` 或对应修订阶段，不能用聊天记忆
   代替绑定 build、视觉证据、manifest 与 QA 哈希的验收记录。
6. `runtime_rebuild_required` 表示旧 runtime、旧 QA 或当前输入指纹不再属于同一次
   构建。保留已激活版本，按报告命令重新构建；不得把 `status=passed` 的旧 QA
   单独当作成功证据。
7. `static` 与 `vehicle-seated` 没有八相位走路时，
   `interaction_preview_generation` 会给出带 mode 和预览路径的
   `interaction-review` `command_template`；只用声明的 `preview_path` 渲染。提交后
   重新观察，展示当前互动预览，再用报告生成的
   批准或否决命令；CAS 同时绑定预览哈希和 mode。否决反馈会进入
   `interaction_revision_required`，修订后必须提交新预览，不能直接写 approved。
   `full-body`、`animal` 只允许走路；若走路与互动都批准，验证与发布最小
   workflow 以走路为准，互动只作为允许模式的无走路后备。
8. 构建指纹排除纯发布授权字段 `sharing`，因此只改自拍分享授权不废止 runtime；
   应用源码、打包资源、配方、运行配置或已批准素材变化会改变指纹并要求重构建。
9. 迁移项目声明需要摸头过渡帧但缺少来源取证时，按报告运行
   `touch-transitions`：旧帧先进入可恢复备份，新帧从当前批准素材确定性重建；其
   runtime 必须重新展示和验收，不能静默采信旧文件。

## 边界

- 高层 Agent 只选择语义动作；素材入库、构建、激活、回滚和发布继续由项目脚本
  执行，不自由拼接绕过门禁的文件操作。
- `agent-status` 不是肢体、身份、走路自然度或真实窗口体验的视觉判官。
- 它不会启用进阶动作、侧走、台词、八向视线或 Live2D；这些仍只由当前用户明确
  点名后进入各自审批。
- 发布继续由 `release_pet.ps1` fail-closed 完成。schema-v2 preparation 用随机
  `preparation_id` 绑定时间、QA、包内指针、最小 workflow 和自拍哈希；finalize
  重查当前 workflow、QA、runtime 与 share-with 自拍授权。它还读取 schema-v1
  passed 的测试、源码/成品启动、互动菜单、源码/成品双击六份 JSON；除测试外五份
  报告都匹配当前 `build_id` 和 `runtime_manifest_sha256`，Hybrid 报告也绑定当前
  pet/runtime。两份成品报告必须匹配最终 EXE SHA-256、完整 one-dir
  `distribution_sha256`、pet-id 和
  preparation_id，烟测后替换任何 `_internal` 文件都会失败。
- 隐私扫描只豁免 `_internal/PySide6` vendor 源中的 `source` 目录名；
  `_internal/user_assets` 下所有私密目录名继续拒绝。环境恢复还覆盖
  `ONEPIC_TESTS_PASSED`、`ONEPIC_INCLUDE_USER_ASSETS`、
  `ONEPIC_PACKAGE_USER_ROOT`、`ONEPIC_INCLUDE_SELFIE_ASSET`。
- 报告中的本机路径和私有哈希只用于本地控制，不进入分享包或对外报告。
