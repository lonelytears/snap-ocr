# snap-ocr 设计方案

> 截图取字托盘工具：快捷键/托盘截图 → 本地 OCR 服务识别 → 置顶浮窗取词。
> 终局对标 iShot 型截图工具链，OCR 是第一环，标注/贴图按里程碑演进。

## 一、v1 范围

| 能力 | v1 | 说明 |
|---|---|---|
| 区域截图（系统交互拖框） | ✅ | `screencapture -i`，免屏幕录制权限 |
| 全屏/窗口截图 | ✅ | 系统截图 UI（空格选窗口）同一入口 |
| 全局快捷键触发 | ✅ | 默认 ⌥⇧O，可配置（M2） |
| 托盘菜单触发 | ✅ | 双通道之一 |
| 结果浮窗 + 一键复制 | ✅ | 置顶无边框，文本可选中，Esc 关闭 |
| 历史（内存最近 20 条） | ✅ | 托盘菜单回看 |
| 质量档切换 | ✅ | fast(80ms)/accurate(380ms)，托盘切换 |
| 服务未运行诊断 | ✅ | healthz 探测 + 明确提示 |
| 开机自启 / .app 打包 | ✅ | PyInstaller + 登录项 |
| 标注/贴图/长截图/录屏 | ❌ | 见演进路线 |

## 二、技术选型

**Python 3.12 + uv + PySide6 + httpx + pynput**，与 OCR 服务（`../ocr-service`）同构。

- PySide6 托盘/浮窗 + 未来 QGraphicsScene 标注体系（图元=对象，可编辑可序列化）；
- 截图走系统 CLI `screencapture -i`：交互式截取无需屏幕录制权限，v1 零权限成本；
- 识别 = 一次 HTTP：`POST {base}/api/v1/ocr?quality=fast`，同步 ~80ms；
- 备选记录：rumps(超轻但浮窗弱) / Swift 原生(v3 候选) / Tauri(排除)。

## 三、架构

```
全局热键(⌥⇧O) ──┐
               ├→ CaptureBackend.capture()  (v1: SystemBackend=screencapture -i)
托盘菜单 ───────┘         │ Esc 取消 → 静默结束
                         ▼
             OCRClient.recognize_file()  ── 失败 → healthz 诊断 → 系统通知
                         ▼
             ResultWindow(置顶浮窗: 文本/复制全部/耗时与置信度)
                         ▼
             History(deque 20) → 托盘子菜单回看
```

**采集双后端**（v1.5 演进的关键抽象）：

```
capture/CaptureBackend          接口: capture() -> (image_path, geometry)
├── SystemBackend   v1   系统交互截图，免权限
└── OverlayBackend  v1.5 全屏截取→冻结→Qt 透明窗自绘选区(需屏幕录制权限)
                      解锁: 尺寸提示/固定比例/多选区/贴图
```

## 四、权限演进（按功能解锁，分阶段申请）

| 权限 | 引入版本 | 解锁功能 |
|---|---|---|
| 辅助功能 | v1(M2) | 全局热键 |
| 屏幕录制 | v1.5 | 自绘选区/贴图/窗口截图/长截图 |
| 输入监控 | v2 | 非聚焦快捷键（贴图窗 Tab 切工具等） |

规划 `PermissionsService`（pyobjc 预检 + 引导跳设置页）。

## 五、iShot 功能对照路线

| iShot 功能 | 我们的版本 |
|---|---|
| 区域/窗口/全屏截图 | v1（系统）→ v1.5（自绘） |
| OCR 取字 | v1（自家服务，引擎自控 = 差异化） |
| 贴图（钉屏） | v1.5 |
| 标注全家桶（箭头/画笔/文字/马赛克/高亮/序号） | v2（QGraphicsScene + QUndoStack） |
| 截图后直接标注 | v2（选区完成 → 工具栏就地出现） |
| 延时截图 | v1.5 |
| 取色/标尺 | v2.x |
| 长截图 | v3（滚动拼接，难度高） |
| 录屏/GIF | v3+（Python 天花板，届时评估 Swift 采集辅助进程，UI 层不动） |

## 六、里程碑

- [x] M0 骨架：config + OCRClient（对 :8000 真联调）+ CaptureBackend(System) + History
- [x] M1 托盘 + 贴图式结果浮窗（图片+文本并排、可拖动）+ 历史
- [x] M3+M4（提前合入）OverlayBackend 自绘选区 + 标注工具栏：
      矩形/椭圆/直线/箭头/像素(马赛克)/文本 六类图元 + 5 色板 + 撤销
      + OCR(点按时才调接口) + 保存 + 取消；Esc 语义分层（编辑中=结束编辑，否则=取消）
      （2026-09-20 真正接通并验收：此前 items/toolbar 为未接线状态；
       同日修复选区镂空 drawPixmap source 物理像素语义导致的截取偏移）
- [ ] M2 全局热键(pynput) + PyInstaller 打包 .app + 登录项
- [ ] 后续: 贴图钉屏 / 延时截图 / 图元选中移动 / 长截图 / 取色标尺

## 七、风险与预案

| 风险 | 预案 |
|---|---|
| macOS 26 上 screencapture -i 行为变化 | 换 mss+自绘选区（OverlayBackend 提前），接口已隔离 |
| 热键冲突 | 可配置 + 托盘双通道 |
| OCR 服务未启动 | 启动 healthz 探测，托盘置灰 + 提示 |
| PyInstaller+PySide6 体积/签名 | 预期 ~60MB，本地自用不做公证 |
| pynput 新版 macOS 兼容 | 备选 pyobjc Carbon RegisterEventHotKey |
