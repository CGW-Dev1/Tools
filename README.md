# 全能开发工具箱 Windows EXE 桌面版

本项目是一个本地离线运行的 Windows 开发工具箱 V1.2.0，包含 JSON、Cron、Base64、加密哈希、金融文件阅读、正则表达式、文档对比和 PDM 数据库结构查看等常用开发小工具。

## 直接使用

下载仓库中的单文件 EXE 后双击运行：

```text
dist\DevToolbox.exe
```

也可以直接下载当前版本：

[下载 DevToolbox.exe](https://github.com/CGW-Dev1/Tools/raw/main/dist/DevToolbox.exe)

无需安装，无需联网。

## 运行源码

```powershell
python -m pip install -r requirements.txt
$env:PYTHONPATH="src"
python run.py
```

## 打包单文件 EXE

```powershell
.\build.ps1 -Clean
```

打包产物位于：

```text
dist\DevToolbox.exe
```

## 功能

- JSON 格式化、压缩、校验、语法高亮、树形折叠查看
- Cron 可视化生成、中文解析、未来 10 次执行时间预览、常用模板
- Base64 文本编解码、图片转 Base64、Base64 还原图片预览与保存
- 加密哈希：MD5、SHA、SHA3、BLAKE2、HMAC、文本/文件摘要
- 金融文件阅读器：识别固定分隔符、字段定长、OFD/基金接口文件线索，表格查看、搜索、CSV导出
- 正则测试、匹配高亮、匹配位置、中文释义、常用模板
- 文档对比：双文档导入/粘贴、逐行差异、HTML/文本导出
- PowerDesigner PDM 文件本地解析、表/字段/索引查看、搜索、Markdown/文本导出
- 深色/浅色主题、工具状态本地缓存、一键复制/清空
