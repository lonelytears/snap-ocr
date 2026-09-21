# snap-ocr

截图取字托盘工具：快捷键/托盘截图 → 调用本地 [ocr-service](../ocr-service) 识别 → 置顶浮窗取词。

设计文档：[docs/PLAN.md](docs/PLAN.md)（含 iShot 型演进路线与权限演进表）。

## 快速开始

```bash
uv sync                                # 安装依赖
uv run python -m app.main              # 启动（托盘常驻）
```

前置：ocr-service 在 `:8000` 运行（`cd ../ocr-service && uv run uvicorn app.main:app --port 8000`）。

## 状态

- [x] M0 核心骨架（OCR 客户端/截图/历史）
- [x] M1 托盘 + 结果浮窗
- [ ] M2 全局热键 + 打包
- [ ] M3+ 自绘选区 / 贴图 / 标注（见 PLAN.md）
