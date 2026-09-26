# 现行回归与保留样本

从项目根执行 `python -X utf8 aic.py test`，或用 `aic.py test test_platform_score` 运行指定模块。
fixtures/prelim 保留基础计分回归仍需要的六个初赛反馈 JSON 和一份修复前样本。
可选的完整初赛修复运行不在 Git 内，缺少 runs/platform_fix/result.json 时该项明确跳过。
退役实验内核及对应测试在完整工作区归档，未把它们当作当前复赛必需环境。
