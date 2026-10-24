# Continuous 模式 FreeText 批量编辑：手动验收

本次只做静态检查，未运行 APP、未构建，也未修改实际 PDF、设置或索引数据。
保存方式不变：先修改当前内存文档，标记未保存，用户手动保存后关闭并重新打开 PDF。

- 在 Continuous 模式打开 FreeText TAB：Replace、Replace All、Preview、Add、Del Text、Del Note、Del All Notes 不再因阅读模式而变灰。
- 没有 PDF、索引执行或离线 Edit Annotation 处理期间，仍禁止批量编辑。
- Page/Document 两种查询范围执行 Find 后，Preview 可查看结果，不修改文档、不产生未保存标记。
- Replace 只处理选中结果，Replace All 只处理当前查询结果集合；With 为空时仍可删除匹配文字。
- Add、Del Text、Del Note 仍按选中结果处理；Del All Notes 只删除当前查询结果对应的注释，不扩大范围。
- 准备跨多个页面、包含屏幕外页面的结果，执行 Replace All 或 Del All Notes。
- 当前屏幕内的 FreeText 立即刷新；滚动到之前缓存过的离屏页面，应显示新内容或已删除状态。
- 验证注释仍在各自页面位置，不出现所有注释叠在连续场景第一页原点的问题。
- 修改后保留原缩放、当前页和横纵滚动位置，不自动跳到最后一个被修改的批注。
- 同一 PDF 在左右两侧同时打开，另一侧可见内容也刷新；不同 PDF 不受影响，垂直同步不产生意外跳动。
- 删除已选注释后选择状态清理；修改后原选择仍有效且可见时正常恢复，不引用已删除图元。
- 操作后保留原有的未保存标记、清除旧撤销记录和重新查询行为，不自动保存或重新打开 PDF。
- 手动 Save Incremental 后关闭并重新打开的行为不变，重新加载后文字修改和删除仍在。
- 切换回单页模式，原有替换、预览、添加和删除功能正常。
