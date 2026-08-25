# OnePic Android 悬浮桌宠

本目录把已经通过 OnePic 角色、走路和扩展审批的精灵运行包移植到 Android。首版
面向 Android 8（API 26）及以上，目标 Android 16（API 36），并使用已安装的
Android 16 QPR2 编译 SDK（API 36.1）。

## 当前能力

- 用户主动授予“显示在其他应用上层”权限；
- 以前台服务显示透明桌宠，并保留不可隐藏的运行通知；
- 正面八相位走路与已批准侧面八相位走路切换；
- 水平移动、边缘转向、拖拽和位置保存；
- 单击随机播放招牌挥手、跳跃、微表情、等待等已批准动作；
- 双击悬浮角色直接打开智能陪伴；使用系统双击阈值，拖拽不会触发聊天；
- 12 秒专注陪伴动作；
- 从已确认台词包显示本地气泡；
- 通知栏提供聊天、互动和停止操作；
- 可选 BYOK 智能陪伴：OpenAI-compatible 文本聊天、按宠物隔离的人设、用户档案、
  可选记忆和本地历史；
- 云聊天默认关闭，只有用户保存 HTTPS 服务、模型和 API Key 后主动点击发送才联网；
- 模型回复只能触发当前运行包中已装配且列入硬白名单的动作；
- 不访问位置、通讯录、麦克风、按键内容或其他 App 数据，也不上传角色图片与动画帧。

Android 的“陪打字”和追逐鼠标不适用于普通手机交互，首版没有申请无障碍权限，也
不会读取其他 App 的输入。

## 智能陪伴 MVP

装配好宠物素材后，可双击正在运行的悬浮角色，或从主界面、桌宠常驻通知进入
“智能陪伴”。打开界面本身不会产生网络请求：

1. 填写 OpenAI-compatible HTTPS 服务地址和模型名称；
2. 填写自己的 API Key，勾选“启用 BYOK 云聊天”并保存；
3. 编辑角色人设。用户档案、记忆和最近 30 条历史默认不发送，只有勾选后才进入请求；
4. 点击“发送”。打开页面、编辑档案和查看历史本身都不会发起网络请求。

服务地址可以是 `https://api.openai.com/v1`、兼容服务的 `https://host/v1`，或完整的
`https://host/v1/chat/completions`。首版拒绝 HTTP、URL 中的用户名/密码、查询参数、
片段、相对路径段与自动重定向；不要把 API Key 写进 URL。

安全与数据边界：

- API Key 使用 Android Keystore 生成的不可导出 AES-256-GCM 密钥加密；密文同时
  认证绑定 `pet_id` 与规范化服务 origin，不写入 SQLite、APK、日志或构建报告；
- 陪伴设置页启用 `FLAG_SECURE`，避免系统截图和最近任务缩略图捕获密钥输入页；
- 角色人设、用户档案、记忆和消息保存在应用私有 SQLite/SharedPreferences 中，
  按 `pet_id` 隔离，并排除云备份和设备迁移；
- 单宠物最多保留 500 条消息；请求最多取最近 30 条，并再限制为 24,000 个字符；
- 响应最多读取 1 MiB，只显示和保存前 12,000 个字符；请求可立即取消；
- “删除本宠物全部陪伴数据”会删除该宠物的档案、记忆、历史、设置、密文和
  Keystore 条目，不影响其他宠物。

当前是文字聊天 MVP：非流式 JSON，不含语音、图片上传、模型工具调用、任意 URL、
后台主动聊天或 Android 版小游戏。服务需要兼容 `POST /chat/completions` 的
`choices[0].message.content` 响应结构。

## GitHub 参考与许可证边界

实现前参考了以下公开项目的模块拆分；本仓库没有复制它们的源代码、资源或文案：

- [MiniiChat](https://github.com/Minis233/miniichat)（MIT）：参考原生 Android
  BYOK、设置与聊天状态分层；
- [Agora](https://github.com/newo-ether/Agora)（MIT）：参考本地历史、服务适配器与
  可取消网络层的职责划分；
- [ClawDroid](https://github.com/KarakuriAgent/clawdroid)（MIT）：参考悬浮角色与
  聊天界面分离；
- [Android security-samples](https://github.com/android/security-samples)
  （Apache-2.0）：核对 Android Keystore 的平台用法；
- [Maskan](https://github.com/humammalhas/maskan)（GPL-3.0）：只作功能架构观察，
  不复制代码，避免许可证传染边界不清。

当前 Android 模块继续使用原生 Java、系统 SQLite、`HttpsURLConnection` 与
Android Keystore，没有新增第三方运行时依赖。

## 素材装配

不要把整个 `user_assets` 或宠物工作区复制到 Android 工程。执行：

```powershell
.\scripts\prepare_android_pet.ps1 -PetId gugugaga2
```

装配工具会再次检查：

1. 标准角色和八相位走路均为 `approved` 且由用户确认；
2. 当前 runtime 中每个扩展动作都有匹配的批准记录；
3. 台词启用时存在已激活的语义配置审批；
4. 八向视线仍为关闭且帧数为零；
5. 每张 PNG 被运行清单明确引用、可解码并含透明通道；
6. 输出不含原始自拍、`selfie_photo`、候选、审批文件、草稿、备份或本机路径。

生成目录是 `app/src/main/assets/pet/`，已被 Git 忽略。工具同时生成
`package-report.json`，记录源清单哈希、逐文件哈希和隐私检查结果。

## 构建环境

本项目不会自动安装系统软件。需要：

- JDK 17；
- Android SDK Platform 36 或 36.1；
- Android SDK Build Tools 36.0.0；
- Gradle 9.5，或由 Android Studio 生成的完整 Gradle Wrapper；
- 真机调试时需要 Platform Tools/ADB。

只读检查：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\check_android_environment.ps1
```

安装 Android Studio 后，也可以直接在 Android Studio 中打开本目录，同步 SDK，
然后运行 `app`。

自动验证：

```powershell
.\scripts\test_android.ps1
```

该入口依次运行 Android JUnit、Lint 和 Debug APK 装配，不安装应用、不调用 ADB。
Gradle 9.5 在 Windows 非 ASCII 工程路径下可能无法加载已经编译的测试类；脚本只在
需要时创建临时 ASCII 盘符映射，并在 `finally` 中移除。只跑单元测试可增加
`-UnitOnly`。

## Debug APK

```powershell
.\scripts\build_android.ps1 -PetId gugugaga2 -Configuration Debug
```

Android 应用身份已经与角色素材解耦。已有安装包可以继续保留
`com.onepic.desktop.pet`；其他 `PetId` 在未显式传入包名时，会生成
`com.onepic.desktop.pet.<pet-id>` 形式的独立身份，使多只桌宠可以并存。
正式发布时建议明确记录以下参数：

```powershell
.\scripts\build_android.ps1 `
  -PetId next-pet `
  -DisplayName "下一只桌宠" `
  -ApplicationId "com.onepic.desktop.pet.next_pet" `
  -VersionCode 1 `
  -VersionName "0.1.0" `
  -Configuration Debug
```

构建目录会额外生成按宠物和版本命名的 APK 以及
`onepic-build-report.json`。应用首次正式发布后不要更改
`ApplicationId`；更新时增加 `VersionCode`。

输出位置：

```text
android/app/build/outputs/apk/debug/app-debug.apk
```

Debug APK 只用于自己的设备测试，不适合正式分享。

## Release 签名

Android 的更新身份绑定在签名密钥上。请把 `.jks` 和密码保存在项目外的安全位置，
并至少保留两份离线备份。不要把密钥、密码或 `keystore.properties` 提交到仓库。

构建脚本从以下进程环境变量读取 Release 签名：

```text
ONEPIC_ANDROID_KEYSTORE
ONEPIC_ANDROID_KEY_ALIAS
ONEPIC_ANDROID_KEYSTORE_PASSWORD
ONEPIC_ANDROID_KEY_PASSWORD
```

设置后运行：

```powershell
.\scripts\build_android.ps1 -PetId gugugaga2 -Configuration Release
```

输出位置：

```text
android/app/build/outputs/apk/release/app-release.apk
```

如果任一签名变量缺失，Release 构建会直接失败，不会偷偷生成未签名分享包。

`tools/OnePicReleaseSigner.exe` 是同一流程的本地界面。它可以为下一只宠物填写
独立包名和版本，并复用已有 JKS；只有选择的 JKS 不存在时才创建新密钥。密码只
传给 `keytool` 和 Gradle 子进程，不会保存到项目文件。

## 真机验收

自动测试不能替代以下视觉和行为检查：

- 授权前不能启动悬浮窗，拒绝权限后能安全返回；
- 桌宠透明边缘没有黑底、白边或矩形遮挡；
- 八相位走路循环自然，正面/侧面切换不覆盖已批准素材；
- 拖动不跳位，松手后恢复移动，横竖屏切换后仍在可见范围；
- 点击透明区域不会大面积阻挡底层 App；
- 单击只触发一次本地互动，双击只打开一次智能陪伴，快速拖动不会误开聊天；
- 双击打开聊天后桌宠仍保持运行，返回时位置和动作状态正常；
- 通知始终可见，点击“停止”会立即移除桌宠和前台服务；
- 打开陪伴中心、保存本地档案和查看历史时没有网络请求；关闭云聊天时不能发送；
- API Key 输入页不能被系统截图；改换服务 origin 后必须重新填写密钥；
- 用户档案、记忆和历史只有在对应复选框开启时才进入请求；取消请求后不落助手回复；
- 合法动作标签能播放已批准动作，未知动作、工具名和 URL 标签只作为无效文本丢弃；
- 锁屏、返回桌面、电量优化和厂商后台管理后的行为符合预期；
- APK 解包后不含自拍原图、候选、审批文件、草稿、备份或本机绝对路径。

不同品牌对悬浮窗和后台运行有额外限制，至少应在一台目标真机上完成 30 分钟运行、
旋转、锁屏、回桌面和重复启停测试后再分享。
