# 归档目录

`archive/deprecated/` 只保存不再参与主流程的历史代码，便于回滚和查阅。

- 不会被技能自动发现
- 不会被 `main.py` 导入
- 不保证与当前接口兼容

当前归档内容包括：

- `new-ppt-skill/`：已停用的旧 PPT 技能
- `make_deck_from_excel.py`：旧版 Excel → JSON 契约生成器
