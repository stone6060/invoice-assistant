# 贡献指南

## 环境准备

```bash
pip install -r requirements.txt
```

PDF 解析需要 **poppler 版** `pdftotext`（xpdf 构建会吞中文，程序会自动跳过）。

## 本地开发

```bash
python serve_api.py      # 启动后浏览器自动打开
python test_all.py       # 需服务在运行，覆盖 20 项
```

## 提交前自检

1. `python -m py_compile *.py` 无报错
2. `python test_all.py` 全部 PASS
3. **不要提交任何发票原件**、`.发票助手/`、`config.json`、`web/demo-data.js`
4. 不要硬编码绝对路径——统一走 `paths.py`

## 代码约定

- 只用标准库 + openpyxl，不引入重依赖
- 路径与外部工具一律通过 `paths.py` 解析
- 新增解析逻辑请同时补 `test_all.py` 用例
