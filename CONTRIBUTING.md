# 参与开发

使用 Python 3.9 或更新版本。核心程序没有第三方运行依赖：

```sh
python3 -m unittest discover -s tests -v
python3 app.py --demo
```

默认启动为空库；`--demo` 显式载入标有历史日期的演示方向，追踪与采集许可默认关闭。请在临时目录测试，不将个人数据库、知识库、凭据或网页原文提交到仓库。

浏览器验收是可选开发依赖，需要安装 Node.js 和 Playwright：

```sh
npm install --no-save --package-lock=false playwright
npx playwright install chromium
node tests/browser_smoke.cjs
```

可通过 MARKETINTEL_PYTHON 指定 Python 可执行程序，MARKETINTEL_CHROME 指定自己的 Chrome。浏览器测试只使用临时数据目录；任何测试连接凭据都是模拟值，不应调用付费账号。

提交前运行 `python3 tools/prepare_release.py` 检查发布文件。该检查只检测部分已知凭据形式和个人路径，不代替人工检查与专门的 secrets 扫描。新增供应商连接需要说明保存位置、传输范围、计费方式，并测试凭据不回显、不导出和配置相互隔离。

代码使用 MIT；第三方软件依赖与资料各自保留原有许可。不要添加无权分发的网页全文、产品图标、字体或其他资产。
