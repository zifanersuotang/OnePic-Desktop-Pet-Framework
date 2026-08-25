# OnePic Desktop Pet Framework

[English](README.en.md) · [贡献指南](CONTRIBUTING.md) · [安全策略](SECURITY.md)

[![CI](https://github.com/zifanersuotang/OnePic-Desktop-Pet-Framework/actions/workflows/ci.yml/badge.svg)](https://github.com/zifanersuotang/OnePic-Desktop-Pet-Framework/actions/workflows/ci.yml)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Windows 10/11](https://img.shields.io/badge/Windows-10%20%7C%2011-0078D4?logo=windows)](https://www.microsoft.com/windows/)
[![Code: MIT](https://img.shields.io/badge/code-MIT-green.svg)](LICENSE)
[![Demo art: CC BY--NC 4.0](https://img.shields.io/badge/demo%20art-CC%20BY--NC%204.0-lightgrey.svg)](ASSETS_LICENSE.md)

从一张**已获授权的角色图**开始，经过角色确认、动作候选、视觉验收和自动化测试，制作可交付的 Windows 桌面宠物。

![OnePic 演示素材与工作流](docs/assets/onepic-showcase.png)

> 代码采用 MIT；仓库自带的演示角色素材采用 CC BY-NC 4.0，不能用于商业项目。详见[素材授权说明](ASSETS_LICENSE.md)。

## 为什么做这个框架

OnePic 不是“把图片直接塞进窗口”的一次性脚本，而是一套可复用的制作与验收流程：

- **Sprite 默认路线**：38 帧基础动作配方，可追加 2 张摸头过渡帧组成 40 帧升级版；
- **人工门禁**：角色、走路或替代互动需要在哈希绑定的预览上明确通过；
- **隐私优先**：原图、客户素材、草稿和构建历史只进入被 Git 忽略的 `user_assets/`；
- **发布可审计**：测试、运行烟测、构建身份、包内清单和 SHA-256 互相绑定；
- **配置驱动**：动作清单、行为、休息提醒和可选陪伴功能由 JSON 配置控制。

Windows/PySide6 Sprite 是默认且可发布的产品路线。仓库保留 Live2D 适配层的研究源码，但**不包含** Live2D Cubism Core、预构建 Web bundle 或模型；Android 目录仅作为实验性源码。

## 快速开始

要求：Windows 10/11、Python 3.12、Git。

```powershell
git clone https://github.com/zifanersuotang/OnePic-Desktop-Pet-Framework.git
cd OnePic-Desktop-Pet-Framework
powershell -ExecutionPolicy Bypass -File .\scripts\setup_environment.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\run.ps1
```

验证源码：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\test.ps1
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe .\main.py --smoke-test-ms 1500
```

安装脚本仅创建项目内的 `.venv`，不会安装系统软件、修改系统环境变量或申请管理员权限。若任一步失败，脚本会以非零状态停止。

## 制作自己的桌宠

私人素材不要放入 `assets/`。先创建隔离工作区：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\new_pet.ps1 -PetId my-pet
```

然后按以下顺序工作：

1. 把已授权原图登记到 `user_assets/pets/my-pet/`；
2. 选择保留原画风、轻度 Q 版或完整 Q 版；
3. 只生成并确认一张标准角色；
4. 逐动作生成 2–3 个候选，并完成走路 GIF/联系表视觉验收；
5. 运行 `tools/pet_workspace.py agent-status my-pet`，每轮只执行报告允许的一步；
6. 测试通过且真实窗口验收后，再用 `scripts/release_pet.ps1` 生成完整目录 ZIP。

完整流程见[可复用桌宠制作流程](docs/可复用桌宠制作流程.md)与[角色和走路验收清单](docs/角色与走路验收清单.md)。

## 仓库结构

```text
src/onepic_desktop_pet/   PySide6 运行时、行为与工作流
assets/pet/               可运行的公开演示帧与 manifest
config/                   默认设置与动作配方 schema
tools/                    素材、审批、QA 与发布工具
scripts/                  Windows PowerShell 入口
tests/                    自动化测试
user_assets/              本地私有工作区（除说明文件外被 Git 忽略）
docs/                     制作、隐私与发布文档
```

## 能力边界

| 路线 | 仓库状态 | 默认发布 |
|---|---|---|
| PySide6 Sprite 桌宠 | 完整源码与演示素材 | 是 |
| 休息提醒、陪伴与扩展动作 | 源码存在，默认关闭 | 仅显式启用 |
| Android | 实验性源码 | 否 |
| Live2D | 仅适配/构建研究源码 | 否；需自行取得许可和运行库 |

不要把自动测试当作角色比例、肢体完整性、关键帧或窗口效果的人工验收。

## 隐私与安全

- `user_assets/**`、虚拟环境、构建产物和本机日志默认不跟踪；
- Issue 和 PR 中不要上传客户原图、真人照片、令牌、聊天记录或绝对本机路径；
- 网络型陪伴功能默认关闭，密钥不写入项目文件；
- 发布前运行仓库检查、完整测试和隐私扫描。

细节见[隐私说明](docs/隐私说明.md)、[安全策略](SECURITY.md)和[发布清单](docs/发布清单.md)。

## 开发与贡献

```powershell
.\.venv\Scripts\python.exe .\scripts\check_framework.py
.\.venv\Scripts\python.exe -m pytest -q
```

欢迎提交修复、测试、文档和通用工具。角色素材贡献必须同时提供作者、来源、授权范围与许可证明。请先阅读[贡献指南](CONTRIBUTING.md)。

## 许可证

- 程序代码和原创项目文档：[MIT](LICENSE)
- 公开演示角色、动作图集与图标：[CC BY-NC 4.0](ASSETS_LICENSE.md)
- 第三方组件：[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)

旧版公开记录保留在 [OnePic-Desktop-Pet](https://github.com/zifanersuotang/OnePic-Desktop-Pet)；本仓库是经过隐私与许可边界整理后的框架主线。
