# 回传字段与自检

从AI审查意见_填写模板.json复制顶层schema、project_id、project_version、source_fingerprint、bundle_id、review_scope_version。保留opinions、coverage、remaining、issue_reviews四个数组。输出示例只用于说明结构，不能当作本项目实际审查结论。

## 每条opinions的字段

- opinion_id：本轮唯一字符串。
- stable_id、episode、original、start_ms、end_ms、snapshot_hash：从本包entries复制；毫秒时间是原片时间。original为交接时当前基线，raw_original才是最初原件。
- decision：suggest / keep / unable。confidence：high / medium / low。
- suggestion：建议时给{text,start_ms,end_ms,deleted}；其他情况null。不把备注、问题编号塞进字幕。
- reason：清楚的一句话判断及依据；不要用文件读取报告替代具体问题。
- needs_human、chinese_audio、audio_checked：真实布尔值。不能直接试听不等于每条needs_human=true；未试听也不能audio_checked=true。
- evidence：非空[{path,detail}]；path为包内相对路径，detail说明本条实际依据。
- text_review：{performed:true,result:clear/correction/human/incomplete,method:实际核对方法,support:[{type:asr/script/context,path,quote:具体原句}]}。clear/correction至少应有识别或剧本支持，不能只把字幕自己与自己比较。
- review_summary：{problem,script_text,asr_text,recommendation,uncertainty,script_status}，字段值均为文字。script_status为matched / no_dialogue / not_located / not_provided。缺原句填空，不伪造；不确定问题写清楚要听什么。
- term_review（可选）：{canonical,observed,sense}，分别为剧本正式术语、本条写法、具体含义，均为字符串。重复术语集中供用户确认；King与Kingdom按不同含义分组。保留已有term_policy及人工取舍。
- 批量确定小修另给change_kind=spelling/punctuation/minor_wording/formal_name、meaning_unchanged=true；asr/script/context三类支持都要具体。formal_name还需minor_pronunciation=true，不能把明显另一人名当成小修。

## 其他数组

- issue_reviews：任务清单中每个范围内issue一项{issue_id,result:dismissed/retained/unreviewed,reason,evidence:[{path,detail}]}。本项目只审已有字幕，in_scope=false的无字幕片段不要求补写、不列入必做任务。
- coverage：每集一项，包含episode、实际text_reviewed_stable_ids、method、实际缺失材料、audio_checked、audio_listened_ranges_ms等。只做文件可读性检查不得写成听辨。
- remaining：{kind:scope/human/incomplete,remaining_id,episode,reason,evidence}。具体事项应带related_stable_ids或start_ms/end_ms；已有问题用issue_id引用，不另造重复错误。

## 提交前逐项自检

1. 确实读取并对照过字幕、完整识别稿、对应剧本与上下文；没有只罗列差异或只检查文件。
2. clear只用于已执行的文字审查，不把旧unable换标签；incomplete范围仍明确留着。
3. 不能试听的共性限制在coverage/scope中说明，没有复制成2000多条人工任务。
4. 所有具体人工项都能回答“哪里冲突、为什么必须人工、具体要听什么”。
5. 已有issue_id已逐项处置，问题引用没有复制为新remaining/导入冲突。
6. 稳定ID、项目版本、原文、原片时间和快照哈希与模板一致；没有用重排后的SRT序号定位修改。
7. 确定小修有具体证据；数字/否定/人物事件含义、增删对白、精确边界及人名明显冲突没有混入批量采用。
8. 已有人工作业、备注、草稿、删除和既有取舍得到保留；新证据以新意见提示。
9. 没有宣称未试听的声音已核验，也没有据识别空白判无对白或无中文。
10. JSON可解析、UTF-8、无围栏；各类数量与摘要、coverage及未完成事项一致。
