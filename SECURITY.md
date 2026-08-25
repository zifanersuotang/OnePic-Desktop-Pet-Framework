# 安全策略 / Security Policy

## 支持范围

安全修复以 `main` 分支当前版本为准。旧仓库、旧 Release、个人分支和未合并的实验功能不保证获得修复。

## 私下报告

请使用仓库 **Security → Advisories → Report a vulnerability** 私下提交：

<https://github.com/zifanersuotang/OnePic-Desktop-Pet-Framework/security/advisories/new>

请包含受影响版本、复现条件、实际影响和可行的缓解方式。不要先建立公开 Issue，也不要附上真实令牌、客户原图、真人照片、聊天记录或包含本机路径的完整日志。

我们会尽快确认收到报告，在验证后协调修复和披露时间。请在修复公开前保密。

## 重点风险面

- 本地密钥与可选网络陪伴功能；
- ZIP 路径穿越、符号链接和发布包身份校验；
- `user_assets/`、QA 报告和烟测截图的隐私泄漏；
- PyInstaller 与第三方运行库的供应链和许可证边界；
- 可选 Live2D/QtWebEngine 研究路线。

本仓库不分发 Live2D Cubism Core 或模型。发现此类文件进入公开树时，请按漏洞处理。
