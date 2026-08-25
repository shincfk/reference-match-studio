# Reference Match Studio WebUI

面向调色与视频工作流的本地 Reference Match 工具。它把“分析参考图、调整匹配参数、用示波器复核、保存配置、在 Resolve 中调用”组织成一条可审计的工作流。

## 架构

```text
Reference Match Studio
        │ 保存 / 导入
        ▼
*.rmatch.json             可版本管理的唯一配置源
        │ 校验 / 激活
        ▼
Reference Match Bridge    生成 ReferenceMatchProfile.h
        │                 并可复制到 Resolve LUT 目录
        ▼
ReferenceMatch.dctl       通用、稳定的计算引擎
        +
ReferenceMatchProfile.h   当前激活 Profile 的编译期常量
```

- `.rmatch.json` 保存颜色管线、源/参考统计、控制值、校验哈希和版本信息；
- Bridge 校验 schema 与引擎兼容性，并原子更新当前配置头文件；
- `ReferenceMatch.dctl` 不再按风格复制重命名，通过同目录的 `ReferenceMatchProfile.h` 获取配置；
- WebUI 预览与 DCTL 使用同一套 OKLab 统计迁移、亮度分区、色相/色度与保护逻辑。

Resolve 的 DCTL 运行环境不能在每帧执行时读取任意 JSON，因此 JSON 不能由 DCTL 直接解析。Bridge 把 JSON 编译为 DCTL 可用的常量头文件，这是该架构存在的原因。

## 启动

```bash
python3 dctl_webui/server.py
```

打开 <http://127.0.0.1:8766/>。服务只监听本机回环地址。

如果希望“安装到 Resolve”直接复制文件，可显式指定 LUT 目录：

```bash
python3 dctl_webui/server.py --resolve-lut-dir "/path/to/DaVinci Resolve/LUT"
```

未指定目录时，“安装到 Resolve”只会安全地激活工作区配置，并明确提示手动复制以下两个文件：

- `dctl/ReferenceMatch.dctl`
- `dctl/ReferenceMatchProfile.h`

复制后在 Resolve 中刷新 LUT/DCTL 列表。

## 专业工作流

1. 导入源静帧与参考图，确认输入编码与 Resolve 节点输入一致；
2. 在 Source / Reference / Result / Split / Difference 间切换监看；
3. 调整 Match、Color、Protection，并用 Waveform、RGB Parade、Vectorscope 与 Match Delta 复核；
4. 保存 `.rmatch.json`，作为可追溯、可复用、可评审的配置；
5. 通过 Bridge 激活 Profile，再在 Resolve 中加载稳定的 `ReferenceMatch.dctl`。

### Viewer 监看与缩放

- 默认 `Fit` 始终以完整画面适配 Viewer，不裁切横图或竖图；
- 将鼠标停在画面上滚动滚轮或触控板，即可连续缩放，并以指针所在位置为缩放锚点；
- 放大后可直接拖拽平移；Split 模式中拖动分割线仍可调整比较位置，按住 `Space` 拖拽可平移画面；
- `100%` 切换到原始像素大小，`Fit`、双击画面或按 `0` 回到完整画面；`+` / `-` 可用键盘逐级缩放。

其他快捷键：`1–5` 切换监看模式，按住 `B` 旁路，`Cmd/Ctrl+S` 保存，`Shift+F` 全屏 Viewer。

## API

- `GET /api/health`：服务、引擎和激活 Profile 状态；
- `GET /api/profile/schema`：Profile schema；
- `POST /api/upload/source|reference`：上传静帧；
- `POST /api/analyse`：生成示波器与统计数据；
- `POST /api/preview`：生成同链路预览与 Difference；
- `POST /api/profile/import|save|activate`：导入、保存和激活配置。

## 边界

当前实现是全局统计匹配，不识别人脸、天空或物体。Warm Tone Protect 是 OKLab 色域规则，不是肤色检测。参考图与源镜头在曝光、光源、场景结构上越接近，结果越可控；最终仍应由调色师结合上下镜头和示波器判断。
