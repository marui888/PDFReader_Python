# 最近查询输入：手动验收

本次只做静态检查，未运行 APP，也未改写现有 PDFReaderSetting.json。

- FreeText 和 Search Annotations 的 Including 4 框、Excluding 2 框，共 12 框，右键第一项均为“最近输入…”，后面有分隔线，原编辑菜单仍可用。
- 仅输入、Load 查询条件或从历史填入，不改变历史。
- 成功点击 Find / Search 或按回车查询后，非空输入加入历史；零结果也记录。
- 无 PDF 而无法 Find、非法正则或其他查询错误不记录；Sources 未选而阻止 Search 时不记录。
- 两处共用同一列表，去首尾空白后按文本精确去重，保留大小写差异。
- 重复使用移到最前，同次查询按 Include 1–4、Exclude 1–2 顺序排列；超过 50 条保留最近 50 条。
- 历史窗口相对 APP 居中，可筛选、预览和复制完整文本；双击、Enter 或“填入”只替换发起右键的输入框，不自动查询，不改变 W/R 等选项。
- 空历史、筛选无结果时不能填入；取消不改原输入。
- 最近列表随成功查询保存至 PDFReaderSetting.json 的 recent_search_terms；重启后恢复，两处显示一致。
- 旧设置文件没有该字段时显示空历史；其他设置、原 Save/Load 查询文件、PDF 保存和索引功能不受影响。
