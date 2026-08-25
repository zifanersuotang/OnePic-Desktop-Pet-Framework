# QA 与发布清单

检查：

- `review/transparent-actions/`；
- `review/action-source-gate.json` 包含八个 passed 动作，每个来自 2–3
  个候选，五项肢体/动作质检全为 true，且角色与输出哈希尚未过期；
- `all-actions-contact-sheet.jpg` 和 `.png`；
- `walk-runtime-preview.gif`；
- `sit-sleep-wake-preview.gif`；
- `touch-feedback-preview.gif`；
- `review/touch-transitions.json` 的角色、惊讶、开心和两张输出哈希均匹配当前文件；
- `assets-qa.json` 为 passed；基础款新宠物为 40 帧，标准进阶示例为 65 帧，
  追加侧面走路后为 73 帧，实际总数必须与所选模块精确相符；
- 走路朝向、步幅、起伏、非整体平移和首尾接缝；
- 摸头为惊讶→害羞/开心→过渡→idle，头部命中不覆盖身体；新宠物为 40
  帧，旧宠物 38–40 帧回退均须通过；
- 进阶扩展逐批检查 GIF、联系表、逐帧哈希、身份一致性、相位、循环和触发抢占；
  未确认候选不得进入运行清单，旧版本不得覆盖；
- 六帧微表情必须保持身体、姿势、镜头、比例、服装和配饰不变，只改变眼眉嘴与
  适量腮红；六种语义齐全且整包哈希已确认；
- 侧面走路必须先有已确认侧身模型，再检查八相位的左右落脚、下压、迈步、抬升、
  基线、体宽和循环；正面走路必须保留且默认启用，菜单切换、按宠物持久化及
  安全镜像均需通过；
- 互动台词检查动作映射、防重复、冷却、屏幕边缘定位、按宠物自定义和恢复默认；
  台词不得读取键盘内容，不得依赖网络，用户覆盖不得进入分享包；真实气泡预览与
  当前规范化配置哈希必须已获用户确认；
- 活动区域默认关闭且分享包中 `movement_region` 为 null；开启后自主走动与
  追鼠标不能越界，手动拖出矩形和松手均不跳回，关闭后恢复当前屏幕范围；检查
  本机重启持久化、多屏求交和完全离屏配置的安全回退；
- Windows 双击角色必须打开只保留聊天页的精简晶体 UI，右键菜单仍能
  恢复完整陪伴中心；回复气泡保持纯文本、半透明晶体样式和边缘避让；
  断言 `network_on_open=false`，并验证打开窗口不产生网络请求、关闭开关后安全回退表情；
- 用户未明确要求八向视线时，运行清单不得出现 `directional_mouse_follow`；
  若明确选配，检查九帧身体稳定、双瞳平行无斗鸡眼、中心死区、切换延迟，以及
  只在 idle 生效；
- 全部隔离测试通过，源码与打包程序烟测退出码为 0。
- 混合 Live2D 项目必须有 `live2d-runtime-smoke.png`，烟测报告为 ready，且
  `nontransparent_pixels > 0`；错误、透明空帧或超时都必须保留精灵回退并阻止
  将 Live2D 标记为通过；
- 混合包只含所选模型引用文件，不含 PSD、CMO3、旧 MOC 导出或 authoring 目录。

未点名模块必须保持默认关闭：基础模板中的 `extension_animations` 为空、
`dialogue_system.enabled=false`、`directional_mouse_follow.enabled=false`。
进阶版请求本身不能推断启用侧面走路、互动台词、八向视线或 Live2D。

发布模式：

- `private-local` 可含自拍；
- `share-with-approved-selfie` 需要匹配的自拍许可哈希；
- `share-no-selfie` 不含自拍。

最终视觉验收必须通过 `approve-runtime` 绑定同一 `build_id`、构建输入指纹、
runtime manifest 和 assets QA 哈希。准备报告必须为 schema v2，含随机
`preparation_id`、`prepared_at`、QA、包内 active pointer、最小 workflow 与可选
自拍 SHA-256；finalize 时当前 workflow/QA/runtime 必须仍相同，share-with 自拍的
配置许可、源文件与包内文件必须逐级匹配该 hash。

finalize 必须实际读取以下 schema v1、`status=passed` 的 JSON，不能只看退出码或
命令行 passed 开关：

- UnitOnly 测试报告；
- 源码启动和成品启动报告；
- 互动菜单报告；
- 源码双击聊天和成品双击聊天报告。

除 UnitOnly 测试外，其余五份报告必须匹配当前 `build_id` 与
`runtime_manifest_sha256`；Hybrid 报告也必须绑定当前 pet/runtime，不能复用同一
pet-id 的旧烟测。成品启动、成品双击两份报告必须同时绑定最终 EXE SHA-256、完整 one-dir
`distribution_sha256`、pet-id 与 preparation_id；完成烟测后替换 `_internal` 中任何
文件都应导致 finalize 失败。release report 还必须显示隐私扫描、逐文件 CRC/SHA、
ZIP CRC 和归档 SHA 均通过。

隐私扫描只可忽略 `_internal/PySide6` 随附 vendor 源里的 `source` 目录名；
`_internal/user_assets` 下的 source、candidate、drafts、backups、failed-builds、
staging 仍是硬失败。烟测退出后检查包括 `ONEPIC_TESTS_PASSED`、
`ONEPIC_INCLUDE_USER_ASSETS`、`ONEPIC_PACKAGE_USER_ROOT`、
`ONEPIC_INCLUDE_SELFIE_ASSET` 在内的所有隔离变量均恢复原值。
ZIP 顶层是完整 `OnePicDesktopPet/` 目录，不能只交付 EXE。
