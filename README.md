# 🎈 AstrBot 桌面助手 —— Live2D 看板娘 + 桌面 AI 陪伴

<div align="center">

[![Python](https://img.shields.io/badge/Python-3.9%2B-blue)](https://www.python.org/)
[![PySide6](https://img.shields.io/badge/PySide6-6.5%2B-green)](https://wiki.qt.io/Qt_for_Python)
[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

**基于 [muyouzhi6/Astrbot-desktop-assistant](https://github.com/muyouzhi6/Astrbot-desktop-assistant) 的 Live2D 增强 Fork**

*在桌面角落放一个会动、有表情、能聊天的 Live2D 看板娘*

[✨ 新增功能](#-本-fork-新增功能) · [⚡ 快速安装](#-快速安装) · [🎭 Live2D 配置](#-live2d-看板娘配置) · [🍎 平台说明](#-平台特别说明)

</div>

---

## 🆚 与上游版本的区别

本仓库 Fork 自 [muyouzhi6/Astrbot-desktop-assistant](https://github.com/muyouzhi6/Astrbot-desktop-assistant)，上游版本提供悬浮球形态的桌面 AI 助手。本 Fork 在保留全部原有功能的基础上，增加了 **Live2D 看板娘** 显示模式。

| 特性 | 上游版本 | 本 Fork |
|------|---------|---------|
| 显示形态 | 悬浮球 | 悬浮球 **+ Live2D 看板娘** |
| 桌面交互 | 悬浮球点击 | 看板娘点击 + 鼠标穿透透明区域 |
| 状态指示 | 悬浮球颜色/呼吸灯 | **Live2D 表情变化**（笑眯眯/眼泪等） |
| 设置界面 | 标签页式 | **侧边栏导航** + 图标 |
| 对话框 | 紧贴悬浮球 | **屏幕居中弹出** + 气泡阴影 |

---

## ✨ 本 Fork 新增功能

### 🎭 Live2D 看板娘

在桌面角落放置一个 Live2D 角色，替代传统悬浮球：

- **透明窗口渲染** — OpenGL 实时渲染，透明背景融入桌面
- **视线追踪** — 角色眼睛跟随鼠标移动
- **鼠标穿透** — 透明区域完全穿透，不阻碍桌面操作（macOS 通过 `setIgnoresMouseEvents_` 实现）
- **点击交互** — 仅模型不透明区域可点击，触发随机动作
- **表情状态** — 通过 Live2D 参数控制表情：
  - 正常 → 自然表情
  - 未读消息 → 笑眯眯
  - 连接断开 → 流泪

### 💬 对话框优化

- 屏幕居中弹出，类似 QQ 聊天窗口
- 消息气泡带柔和投影阴影
- 输入区分隔线视觉分层
- 可拖拽、可调整大小

### 🎨 设置界面重构

- 左侧栏导航 + 右侧内容面板（替代原版 Tab 页）
- 8 个功能图标（SVG 动态着色）
- Live2D 专属配置区（模型路径、缩放比例）
- 所有控件 `:disabled` 状态样式

### 🔧 其他改动

- 移除流式输出（`enable_streaming`），避免影响钩子函数运行
- 截图时 Live2D 窗口移至屏幕外（保留 GL 上下文），截图完成后恢复
- 对话窗口与 Live2D 位置解耦，可独立移动

---

## ⚡ 快速安装

### 前置条件

- ✅ Python 3.9+（macOS 推荐 3.10+）
- ✅ AstrBot 服务端已部署并运行
- ✅ 已安装服务端插件 [astrbot_plugin_desktop_assistant](https://github.com/muyouzhi6/astrbot_plugin_desktop_assistant)

### 安装

```bash
git clone https://github.com/ArisaTaki/Astrbot-desktop-assistant.git
cd Astrbot-desktop-assistant

# 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate  # macOS/Linux
# 或 .venv\Scripts\activate  # Windows

# 安装依赖（含 Live2D）
pip install -r requirements.txt

# 启动
python -m desktop_client
```

### macOS 打包版启动

本仓库现在默认以正式 `.app` 作为 macOS 日常入口：

```bash
./scripts/build_macos_app.sh
open -n "dist/AstrBot Desktop Assistant.app"
```

说明：

- `python -m desktop_client` 仍然适合开发调试
- `dist/AstrBot Desktop Assistant.app` 适合日常使用、截图权限授权、开机自启
- `start.command` 与桌面快捷方式都会优先打开 `dist` 里的正式 app

### 连接服务端

1. 右键看板娘 → 选择「设置」
2. 填写服务器地址（如 `http://127.0.0.1:6185`）
3. 填写 AstrBot 管理员账号密码
4. 保存设置

点击看板娘即可开始对话。

---

## 🎭 Live2D 看板娘配置

### 切换显示模式

在设置 → 外观 中选择：

| 模式 | 说明 |
|------|------|
| 悬浮球 | 原版小圆球（默认） |
| Live2D 看板娘 | Live2D 角色窗口 |

### 模型配置

1. 准备一个 Live2D Cubism 3.0+ 模型（`.model3.json`）
2. 在设置 → 外观 → Live2D 模型路径 中选择模型文件
3. 通过缩放滑块调整模型大小

> 模型需包含 `.moc3`、纹理文件和 `.model3.json`。推荐从 [Live2D 官方示例](https://www.live2d.com/learn/sample/) 或模型社区获取。

### 表情参数（可选）

本 Fork 使用以下 Live2D 参数控制表情状态，模型若包含这些参数会自动生效：

| 参数 ID | 用途 |
|---------|------|
| `ParamExpression_1` | 眼泪（断连时） |
| `ParamExpression_2` | 泪珠（断连时） |
| `ParamExpression_3` | 笑眯眯（未读消息时） |

---

## 🍎 平台特别说明

### macOS

- 系统要求：macOS 10.14+ / Python 3.10+
- Live2D 鼠标穿透需要 `pyobjc-framework-Cocoa`（安装脚本自动处理）
- 首次启动打包版 app 时，需要在系统设置中授权“录屏与系统录音”
- 开机自启现在以 `dist/AstrBot Desktop Assistant.app` 为准，不再依赖 `.command` 或终端后台命令

### macOS 开发 / 打包 / 发布工作流

开发调试：

```bash
cd /path/to/Astrbot-desktop-assistant
./.venv/bin/python -m desktop_client
```

一键重新打包：

```bash
cd /path/to/Astrbot-desktop-assistant
./scripts/build_macos_app.sh
```

生成 GitHub Release 产物并发布：

```bash
cd /path/to/Astrbot-desktop-assistant
./scripts/release_macos_app.sh v1.0.0
```

`release_macos_app.sh` 会执行：

- 重新打包 `dist/AstrBot Desktop Assistant.app`
- 生成 `dist/AstrBot-Desktop-Assistant-<version>-macos.zip`
- 创建并推送 git tag
- 如果本机已安装并登录 `gh`，自动创建 GitHub Release

如果只想本地打包，不发 release，只执行 `./scripts/build_macos_app.sh` 即可。

### Linux

```bash
# Ubuntu/Debian
sudo apt install libgl1-mesa-glx libxcb-xinerama0 libxcb-cursor0 libegl1

# Fedora
sudo dnf install mesa-libGL libxcb
```

### Windows

开箱即用，无特殊依赖。

---

## 📦 目录结构

```
desktop_client/
├── gui/
│   ├── floating_ball.py     # 悬浮球 + 对话窗口
│   ├── live2d_widget.py     # Live2D 看板娘窗口
│   ├── settings_window.py   # 设置界面（侧边栏导航）
│   ├── themes.py            # 主题系统
│   ├── icons.py             # SVG 图标管理
│   └── markdown_utils.py    # Markdown 渲染
├── handlers/                # 消息处理器
├── controllers/             # 设置控制器
├── platforms/               # 平台适配（Win/Mac/Linux）
├── services/                # 截图、桌面监控等服务
├── config.py                # 配置管理
├── bridge.py                # 消息桥接层
└── main.py                  # 程序入口
```

---

## 🔗 相关链接

| 资源 | 链接 |
|------|------|
| 🔌 上游项目 | [muyouzhi6/Astrbot-desktop-assistant](https://github.com/muyouzhi6/Astrbot-desktop-assistant) |
| 🔌 服务端插件 | [astrbot_plugin_desktop_assistant](https://github.com/muyouzhi6/astrbot_plugin_desktop_assistant) |
| 🎭 live2d-py | [live2d-py](https://github.com/Arkueid/live2d-py) |
| 🤖 AstrBot 主项目 | [AstrBot](https://github.com/Soulter/AstrBot) |

---

## 📄 许可证

MIT License — 基于 [muyouzhi6/Astrbot-desktop-assistant](https://github.com/muyouzhi6/Astrbot-desktop-assistant) 的 Fork。

原项目作者及开发 QQ 群：215532038

---

<div align="center">

*让 Live2D 看板娘成为你的桌面 AI 伙伴*

[报告问题](https://github.com/ArisaTaki/Astrbot-desktop-assistant/issues) · [上游项目](https://github.com/muyouzhi6/Astrbot-desktop-assistant)

</div>
