"""python -m app 的入口。

输入：无。输出：把 main 的退出码交给解释器。
用途：从项目根目录启动窗口。
边界：配置错误时由 main 抛出，不在这里吞掉。
"""

from app.main import main

raise SystemExit(main())
