# Global Execution Rules

Applies to every task, regardless of role or domain.

## 1. Sample the store before searching
- A query that returns no records is a signal to inspect the data, not to rewrite the query.
- Enumerate the actual values of the filter field first (e.g. `SELECT DISTINCT status`, `priority`, `stage`), then query with the literal the store really uses.
- Never try more than two spelling/casing variants of the same filter. If both return empty, stop and sample values instead.

## 2. Map the task's labels to store enums
- Task wording is not guaranteed to be a store enum. Treat labels such as Critical, Urgent, Done, In Progress as semantic targets that must be mapped.
- Default mapping used by this platform: Critical → `highest`, High → `high`, In Progress → `in_progress`, Done → `done`. Confirm with a sampled enum when the store differs.
- Never assert "there are no Critical items" before you have enumerated the priority/status values.

## 3. Numbers must be traceable to a tool result
- Every number in the final answer must come from a tool result produced in this run.
- Before writing the final answer, re-read each number and compare it with the tool output that produced it; copy the value, do not restate it from memory.
- If two tool results disagree, report the conflict instead of silently choosing one.

## 4. Reading is not the same as reporting
- When a source document contains a hard constraint (deadline, threshold, dependency, consequence), it must appear in the answer with the original date or condition quoted.
- Do not compress a required constraint down to a topic label (e.g. keep "Hard go/no-go deadline: 2026-03-31", not just "Q3 2026").

## 5. State the source before the conclusion
- Declare the data scope you actually queried (which statuses, which date range, which entity) before presenting a conclusion.
- Do not join records by text similarity when an explicit relation field exists.

## 6. Walk the full join chain before answering
- If a required column depends on a relation that the first query did not return, resolve every hop explicitly (e.g. ticket → account → ARR) before writing the answer.
- Never leave a required column blank just because the initial query lacked it; a half-filled table is a failure, not a partial success.
- When a row needs both a direct relation and a second-hop attribute, query both in this run and join on the explicit id field.

## 7. Window questions need creation evidence, not change evidence
- For "new / created / added / opened in this period" questions, filter on the creation timestamp and say so.
- Stage changes, status transitions and `updated_at` are not evidence that something was created in the window.
- When the task asks for both changed and newly created objects, answer both dimensions explicitly rather than substituting one for the other.

## 8. Reproduce the requested output shape exactly
- If the task asks for N columns, produce those N columns; if it asks for one row per entity pair, produce one row per pair.
- Do not restructure a required table into prose, and do not re-aggregate by a different key than the one requested.
- If a requested field cannot be retrieved, keep the column and mark it as not retrieved; do not drop the column silently.

## 9. 关联判据（强制）
1. 只要组件内存在 open 状态的 issue，「关联 issue」就不得填 none；不得因主题不完全一致而放弃，必须在同一组件的 open 集合中选出最接近的一条。
2. 同名但已关闭（Done／Closed／Resolved）的 issue 不能作为放弃匹配的理由，也不是答案；遇到命名式陷阱时，继续在同一组件的 open 集合中查找。

