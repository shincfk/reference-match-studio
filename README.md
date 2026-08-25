# Reference Match Studio

用于开发、预览和部署 DaVinci Resolve Reference Match DCTL 的本地 Look 开发工作台。它把源静帧与参考图的统计匹配保存为版本化 Profile，并通过稳定的通用 DCTL 在 Resolve 中调用。

> `shotMatch` 是针对相似光线、曝光和场景条件的全局统计迁移，不是可自动适配所有镜头的语义调色。

## 当前推荐入口

- WebUI：`dctl_webui/server.py`
- 通用引擎：`dctl/ReferenceMatch.dctl`
- 当前激活配置：`dctl/ReferenceMatchProfile.h`
- Profile schema：`profiles/rmatch-profile.schema.json`
- 示例 Profile：`profiles/Mediterranean_Olive.rmatch.json`
- 产品需求：`docs/Reference_Match_Studio_PRD_v0.2.md`

旧的 `CityStreet_to_Mediterranean_Olive_V5_Debug.dctl` 仍保留在 `dctl/current/`，用于回归对照；新工作流不再按场景复制 DCTL 文件。

## `.rmatch.json + Bridge + ReferenceMatch.dctl`

```text
可编辑配置                编译桥接                  Resolve 执行
*.rmatch.json  ───────▶  Reference Match Bridge ─▶ ReferenceMatchProfile.h
                                                       +
                                                 ReferenceMatch.dctl
```

JSON 是唯一配置源；Bridge 负责校验并生成编译期常量；DCTL 是稳定的通用图像处理引擎。这解决了 Resolve DCTL 不能在运行时直接解析 JSON 的限制，也让一个引擎可以复用多种风格和场景配置。

## 快速开始

```bash
git clone https://github.com/shincfk/reference-match-studio.git
cd reference-match-studio
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python dctl_webui/server.py
```

打开 <http://127.0.0.1:8766/>。Windows 下请使用 `.venv\\Scripts\\python.exe` 替代 `.venv/bin/python`。

在 WebUI 中保存或导入 Profile、点击“安装到 Resolve”后，将以下两个文件一同复制到 Resolve 的 LUT/DCTL 目录并刷新列表：

- `dctl/ReferenceMatch.dctl`
- `dctl/ReferenceMatchProfile.h`

发行说明见 [v0.1.1](docs/releases/v0.1.1.md)，完整操作说明见 [WebUI 文档](dctl_webui/README.md)。

## 目录

```text
dctl/              通用引擎与激活头文件
dctl_webui/        本地专业监看、分析、Profile 与 Bridge 服务
profiles/          schema 和可版本管理的 .rmatch.json
scripts/           DCTL 引擎/独立文件生成工具
docs/              PRD 与发布说明
```

## 验证

```bash
/Users/luoo666/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  -m unittest discover -s dctl_webui/tests -v
node --check dctl_webui/app.js
```
