# AI 辅助分层与绑定实验

## 目标

本实验验证以下非破坏性链路：

```text
公开或已明确授权的单张角色图
→ See-through 语义拆层 PSD
→ Stretchy Studio 骨骼与网格绑定
→ Spine 4.0 / 图像序列
→ OnePic 现有精灵运行与审批流程
```

实验不能绕过 OnePic 的角色、走路、扩展动作和视觉确认门禁，也不能直接覆盖
`assets/pet/`、`user_assets/pets/<pet-id>/runtime/` 或当前激活宠物。

## 已核对的上游

| 项目 | 固定审计版本 | 许可证 | 用途 |
|---|---|---|---|
| See-through | `58a1cb11d13f85acec9bbddb8cd4b6487843d4cf` | Apache-2.0 | 单图拆成最多约 23 个语义层并补全遮挡区域 |
| Stretchy Studio | `24a83a27ba43e43e9d2e3de5e33994594e6199c2` | MIT | 导入 PSD、启发式/DWPose 绑定、网格动画和 Spine 4.0 导出 |

本地 Stretchy Studio 使用 Node 24 与锁文件依赖完成生产构建。上游
`npm run lint` 当前会检查构建后的 `dist/` 及大量既有 JSX，存在上游存量错误，
因此本项目只把“锁文件安装 + 生产构建 + 实际页面导入”作为接入烟测，不宣称其
上游 lint 通过。

## 8GB 显存边界

当前机器为 RTX 5060 Laptop、8GB 显存。See-through 官方针对 8GB GPU 提供
`inference_psd_quantized.py`、NF4 权重量化和 group offload；首次运行仍需下载
多个模型，速度会比完整显存路线慢。不得把 See-through 的 PyTorch、PyQt6 和
训练依赖直接安装进 OnePic 根 `.venv`，以免污染 PySide6 桌宠环境。

正式本地推理应在被 Git 忽略的上游克隆内建立独立 `.venv`，只安装推理层依赖；
若依赖或显存不满足，应改用官方 Hugging Face / ModelScope 演示，并且只能上传
公开或用户明确授权传输的素材。

## 无模型烟测代理

为了在不下载大模型、不读取私人图片时验证完整导入流程，项目提供：

```powershell
.\.venv\Scripts\python.exe .\tools\assisted_rigging.py prepare-proxy `
  --source .\assets\pet\idle\idle_01.png `
  --output-dir .\.tmp\assisted-rigging-runs\public-demo
```

工具会把每个非透明像素唯一分配到 `face`、`topwear`、左右手、下装、左右腿和
左右鞋等 Stretchy 可识别图层，生成：

- `onepic-proxy-rig.psd`
- `layers/*.png`
- `composite-preview.png`
- `assisted-rigging-report.json`

报告不记录绝对路径，必须显示：

- `artifact_kind = pipeline-smoke-proxy-psd`
- `roundtrip_alpha_delta = 0`
- `roundtrip_visible_rgb_delta <= 1`（PSD 8 位合成允许一次舍入）
- `user_assets_read = false`
- `runtime_modified = false`

该 PSD 只用于测试 Stretchy 的导入、骨骼向导和导出按钮，不代表 See-through
拆层效果，不得作为客户成品或审批素材。

验证真实 See-through PSD：

```powershell
.\.venv\Scripts\python.exe .\tools\assisted_rigging.py validate-psd `
  --psd <see-through-output.psd>
```

至少四个像素层必须使用 Stretchy 可识别的语义标签。

## 真实官方 Space 推理

项目提供公开素材专用调用器：

```powershell
.\.venv\Scripts\python.exe .\tools\run_see_through_space.py `
  --source .\assets\pet\idle\idle_01.png `
  --output-dir .\.tmp\assisted-rigging-runs\see-through-public-demo `
  --resolution 768 `
  --seed 42
```

调用器使用 See-through 官方公开 Gradio API，不要求把 PyTorch、bitsandbytes、
PyQt6 或模型权重安装进 OnePic 根 `.venv`。它会拒绝：

- `user_assets`、项目外路径和符号链接输入；
- 写入 `.tmp/assisted-rigging-runs` 之外的目录；
- 从非官方 Space 同源地址下载输出；
- 把本机绝对路径写入审计报告。

ZeroGPU 可能排队、限额或临时失败，调用器不会尝试绕过这些限制。真实推理输出包括：

- `see-through-output.psd`
- `layers/*.png`
- `see-through-space-report.json`

2026-07-31 的公开演示运行使用 768 分辨率和种子 42，约 76 秒完成，得到
24 张语义层预览。PSD 为 768×768、15 个像素层，基础验证命中 7 个 Stretchy
已知标签；Stretchy 自身的导入映射识别 15/15 层，并在“拆分合并部件”开启后
形成 22 个绑定部件。

## Stretchy 与 Spine 实际验收

同一份真实 See-through PSD 已在固定版本的 Stretchy Studio 页面中走完：

1. 15/15 输入层映射；
2. 自动拆分左右眼、眉毛、睫毛、耳朵和合并四肢；
3. 关节启发式定位、全件网格化和 Live2D 参数向导；
4. `.stretch` 工程保存与重新载入；
5. Spine 4.0 ZIP 导出与内容结构验证。

本次 Spine 包包含 14 根骨骼、22 个插槽、22 张纹理、1 个皮肤和 2 个动画。
这些结果证明“单图 → 语义 PSD → 自动绑定 → Spine 数据”链路可以直接运行，
但不表示动作动画已经完成视觉验收，也不授权把输出写入当前宠物运行目录。

## 接入 OnePic 的边界

1. Stretchy/Spine 输出先写入独立实验目录，生成 GIF、联系表和哈希报告。
2. 只有当前宠物已通过角色确认，才可把实验动作登记为扩展候选。
3. 只有用户直接确认对应版本帧束后，才可写入配置并运行 `build_pet.ps1`。
4. 实验默认不启用八向视线，不自动切换 `hybrid-live2d`，不修改 Android 能力声明。
5. 分享包仍只能通过 `release_pet.ps1` 生成，并继续执行隐私、CRC 和 SHA 校验。
