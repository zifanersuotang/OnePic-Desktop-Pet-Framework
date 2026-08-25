# 贡献指南 / Contributing

感谢你改进 OnePic Desktop Pet Framework。修复、测试、文档、可复用工具和无隐私风险的演示都欢迎提交。

## 开始前

1. 从 Issue 描述问题或提案；安全漏洞请按 [SECURITY.md](SECURITY.md) 私下报告。
2. 使用 Python 3.12，在项目 `.venv` 中安装 `.[dev]`。
3. 不提交客户素材、真人照片、私有聊天、密钥、本机路径或 `user_assets/**`。
4. 美术素材必须提供作者、来源、许可文本、允许的使用范围和必要的书面授权证据。

## 开发检查

```powershell
.\.venv\Scripts\python.exe .\scripts\check_framework.py
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe .\main.py --smoke-test-ms 1500
```

涉及角色比例、动作、关键帧、透明边缘、键鼠/UI 对位或窗口效果的改动，还要附上必要的高清截图或预览；自动测试不能替代视觉验收。

## Pull Request

- 每个 PR 聚焦一个问题；
- 同步更新相关测试和文档；
- 说明用户可见变化、验证方式与残余风险；
- 勾选 PR 模板中的隐私和许可检查；
- 不要提交构建产物、虚拟环境、依赖缓存或专有 Live2D 运行文件。

代码贡献按项目 MIT License 提交。素材不会自动采用 MIT，必须在 [ASSETS_LICENSE.md](ASSETS_LICENSE.md) 中单独登记。
