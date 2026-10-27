#!/usr/bin/env python3
"""Agent Matrix — Agent 执行器

加载 Agent 配置 → 注入 System Prompt → 执行 LLM 调用 → 自检。
"""
from i18n import _
import json, os, sys, time, logging
# WP-2：模块级导入（_execute_batch 与单测 monkeypatch 需要；tools 不反向依赖本模块，无循环）
from agent_matrix.tools import (
    execute_tool, get_tool_concurrency, get_parallel_enabled)
# WP-11：模块级导入（memory_dir 只依赖标准库，不反向依赖本模块，无循环；
# iter_react 的 system prompt 拼接与单测 patch 均需要）
from agent_matrix.memory_dir import load_memory_prompt
logger = logging.getLogger(__name__)


# ============================================================
# WP-9：三循环并轨 —— 内核 transition 枚举
# ============================================================
# 把 planner_loop 的 plan -> execute -> observe -> replan 迭代，与 runner 的
# iter_react() 统一为「带 transition 的事件流」语义：任何"多阶段循环"都产出一
# 串 transition 事件（iter_react 产出 round_start/.../final，PlannerLoop 产出
# plan/execute/observe/replan/converged），调用方按统一协议消费/审计。
# 仅定义枚举与事件形状，不改变任何既有事件序列（缺省路径零变化）。
TRANSITION_PLAN = 'plan'
TRANSITION_EXECUTE = 'execute'
TRANSITION_OBSERVE = 'observe'
TRANSITION_REPLAN = 'replan'
TRANSITION_CONVERGED = 'converged'

# transition 事件统一形状：
#   {'type': 'transition', 'to': TRANSITION_*, 'iteration': int,
#    'detail': { ... }}        # detail 可空，承载阶段数据（如 observe 的失败/完成计数）


# WP-6：检查点目录（项目根 data/agent_runs/），与既有 data/media 约定一致。
def _agent_runs_dir():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, 'data', 'agent_runs')


def load_checkpoint(run_id):
    """WP-6：加载 <run_id>.json 检查点；不存在/损坏返回 None。

    Returns:
        dict  检查点（messages / round / last_text / max_rounds / task_phase /
              user_query / history / agent_id / status / updated_at）
        None  不存在或解析失败（调用方按 404 处理）。
    """
    if not run_id:
        return None
    try:
        path = os.path.join(_agent_runs_dir(), f'{os.path.basename(str(run_id))}.json')
        if not os.path.exists(path):
            return None
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.warning('[checkpoint] load failed: %s', e)
        return None


class AgentRunner:
    """Agent 执行器：负责一次 Agent 对话的完整生命周期"""

    def __init__(self, agent_config: dict, db_models=None):
        """
        agent_config: agent_matrix 行字典
        db_models: models 模块引用（用于日志记录）
        """
        self.config = agent_config
        self.agent_id = agent_config.get('id', 0)
        self.name = agent_config.get('name', 'Unnamed Agent')
        self.role_type = agent_config.get('role_type', 'sub')
        self.domain = agent_config.get('domain', 'general')

        # 延迟加载 engine，避免循环依赖
        self._engine = None
        self._engine_ready = False
        self.models = db_models
        # v1.2.0：可观测关联键（单实例单任务，串行路径无并发覆盖风险）
        self._trace_id = ''
        self._session_id = ''

    def _get_engine(self):
        if not self._engine:
            sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
            from agent_matrix.engine import UnifiedLLM
            self._engine = UnifiedLLM(self.config)
            self._engine_ready = self._engine.is_ready()
        return self._engine

    def is_ready(self):
        self._get_engine()
        return self._engine_ready

    def execute(self, task: dict, history: list = None):
        """执行一次 Agent 任务（wrapper：结束后发射 AGENT_TASK_COMPLETED 事件）。"""
        result = self._execute_impl(task, history)
        self._emit_task_completed(task, result)
        return result

    def _execute_impl(self, task: dict, history: list = None):
        """
        执行一次 Agent 任务

        task: {
            'task_id': 'AT-...',
            'title': '...',
            'description': '...',
            'input_data': {...},
        }
        history: 可选的历史对话 (用于多轮)

        Returns: {
            'status': 'completed' | 'failed',
            'response': '...',
            'confidence': 0.0-1.0,
            'self_review': '...',
            'logs': [...]
        }
        """
        logs = []
        task_id = task.get('task_id', 'unknown')
        # v1.2.0：把 task_id 作为 LLM 用量日志的关联键（NeuralFlow 归因主键）
        self._trace_id = task_id if task_id and task_id != 'unknown' else ''
        self._session_id = task.get('session_id') or ''
        # WP-8：预算与子代理深度经实例属性透传到 _execute_batch 的 context，
        # 供 spawn_subagent 工具使用（单实例单任务，串行路径无并发覆盖风险）
        self._current_token_budget = task.get('token_budget', 0)

        # 1. 日志：开始执行
        self._log(task_id, 'info', 'execution', f'🤖 {self.name} starts executing the task')
        logs.append(f'[{self.name}] Received task: {task.get("title", "")}')

        # 2. 构建完整的 Prompt
        user_query = self._build_query(task)
        logs.append(f'[Prompt] Construction completed')

        # 3. 调用 LLM
        engine = self._get_engine()
        if not engine or not engine.is_ready():
            err_msg = f'AI Engine Not Ready ({self.config.get("provider", "?")}/{self.config.get("model_name", "?")})'
            self._log(task_id, 'error', 'execution', err_msg)
            return self._fail(err_msg, logs)

        self._log(task_id, 'info', 'api_call',
                   f'Call {self.config.get("provider")}/{self.config.get("model_name")}')

        # 按 allowed_tools 白名单决定是否启用 ReAct 工具循环
        # B-03: 模型/上游错误（401/5xx/网络异常/Error 前缀）走有限重试，受 max_retries 约束
        tools = self._get_tools()
        api_max_retries = task.get('max_retries', 2)
        response = None
        for attempt in range(api_max_retries + 1):
            try:
                if tools:
                    logs.append(f'[Tools] Enabled {len(tools)} tools, entering ReAct loop')
                    # C-2: max_rounds configurable (task/agent config > default 5)
                    _max_rounds = int(task.get('max_tool_rounds')
                                      or self.config.get('max_tool_rounds')
                                      or self.config.get('max_turns') or 5)
                    response = self._run_react_loop(engine, user_query, history, tools, logs, task_id,
                                                    max_rounds=_max_rounds,
                                                    task_phase=task.get('phase', ''),
                                                    gates=self._build_gates(task, task_id))
                elif history:
                    response = engine.ask_with_history(
                        history, user_query,
                        trace_id=self._trace_id, session_id=self._session_id)
                else:
                    response = engine.ask(
                        user_query, trace_id=self._trace_id,
                        session_id=self._session_id)
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                logger.error(f"[{self.name}] LLM execute failed (attempt {attempt + 1}): {e}\n{tb}")
                logs.append(f'[ERROR] attempt {attempt + 1}: {e}')
                logs.append(f'[TRACEBACK] {tb[:500]}')
                response = f'Error: {e}'

            if isinstance(response, str) and response.startswith('Error:'):
                self._log(task_id, 'warn', 'api_call',
                          f'LLM error (attempt {attempt + 1}): {response[:200]}')
                if attempt < api_max_retries:
                    logs.append(f'[Retry #{attempt + 1}] upstream/model error, retrying')
                    time.sleep(min(2 * (attempt + 1), 8))
                    continue
                self._log(task_id, 'error', 'execution', response)
                return self._fail(f'{response} (after {attempt + 1} attempts)', logs)
            break

        if not isinstance(response, str):
            response = '' if response is None else str(response)
            logs.append(f'[LLM] Non-string response coerced to str (len={len(response)})')

        logs.append(f'[LLM] Response length: {len(response)} characters')
        self._log(task_id, 'info', 'execution', f'LLM Response Completed ({len(response)} characters)')

        # 4. 自检 (Self-Critique) — Direct Reply 任务跳过
        if task.get('skip_critique'):
            self._log(task_id, 'info', 'execution', f'✅ Direct reply, confidence=1.0')
            return {
                'status': 'completed',
                'response': response,
                'confidence': 1.0,
                'self_review': 'Direct reply (auto-passed)',
                'logs': logs,
                'retries': 0,
                'failed': False
            }

        self_review = self._self_critique(response, task)
        logs.append(f'[Self-Critique] {self_review.get("review", "None")}')

        confidence = self_review.get('confidence', 0.85)
        self._log(task_id, 'info', 'self_review',
                   f'Self-check Complete: confidence={confidence}, review={self_review.get("review", "")[:100]}')

        # 5. 如果置信度过低，重试
        retries = 0
        max_retries = task.get('max_retries', 2)
        while confidence < 0.7 and retries < max_retries:
            retries += 1
            logs.append(f'[Retry #{retries}] confidence={confidence} < 0.7，重新执行')
            self._log(task_id, 'warn', 'execution',
                       f'重试 #{retries}: confidence={confidence} < 0.7')

            # 用更明确的 prompt 重试，带入自检发现的问题点
            issues = self_review.get('issues', [])
            issues_str = ('\n'.join(f'- {i}' for i in issues)) if issues else '(No specific issue list)'
            retry_query = (
                f"The previous result was not satisfactory (confidence: {confidence}).\n"
                f"Self-review feedback: {self_review.get('review', '')}\n"
                f"Issues to improve:\n{issues_str}\n"
                f"Suggestion: {self_review.get('suggestion', '') or 'None'}\n\n"
                f"Re-execute the task addressing the issues above.\n\n"
                f"Original task: {user_query}"
            )
            # A3.1：重试必须沿用首轮的执行形态（带工具则继续走 ReAct，带历史则继续带）
            if tools:
                response = self._run_react_loop(engine, retry_query, history,
                                                tools, logs, task_id,
                                                task_phase=task.get('phase', ''),
                                                gates=self._build_gates(task, task_id))
            elif history:
                response = engine.ask_with_history(
                    history, retry_query,
                    trace_id=self._trace_id, session_id=self._session_id)
            else:
                response = engine.ask(
                    retry_query, trace_id=self._trace_id,
                    session_id=self._session_id)
            if not isinstance(response, str):        # A3.2：None 兜底
                response = '' if response is None else str(response)
            if response.startswith('Error:'):
                break
            self_review = self._self_critique(response, task)
            confidence = self_review.get('confidence', 0.85)
            logs.append(f'[Retry #{retries}] 新 confidence={confidence}')
            self._log(task_id, 'info', 'self_review',
                       f'重试 #{retries} 后 confidence={confidence}')

        # 6. 更新统计
        if self.models:
            self.models.update_agent_stats(self.agent_id, success=(confidence >= 0.7))

        if confidence >= 0.7:
            self._log(task_id, 'info', 'execution', f'✅ Task completed, confidence={confidence}')
            return {
                'status': 'completed',
                'response': response,
                'confidence': confidence,
                'self_review': self_review.get('review', ''),
                'logs': logs,
                'retries': retries,
                'failed': False
            }
        else:
            err_msg = f'After {retries} retries, confidence={confidence} is still below threshold'
            self._log(task_id, 'error', 'execution', err_msg)
            return self._fail(err_msg, logs, confidence)

    def _emit_task_completed(self, task, result):
        """发射 agent.task.completed 事件（内核补丁 B），供 memory_engine 等订阅。

        成功/失败路径都会触发；任何异常仅告警，不影响原执行流程。
        """
        try:
            from plugin_manager.event_bus import get_event_bus, EventName
            ev = getattr(EventName, 'AGENT_TASK_COMPLETED', None)
            if ev is None:
                return
            get_event_bus().emit(ev, task=task, result=result or {},
                                 agent_id=self.agent_id, agent_name=self.name)
        except Exception as e:
            logger.warning(f'[{self.name}] emit task completed failed: {e}')

    def _get_tools(self):
        """按 Agent 的 allowed_tools 返回可用工具 schema，无则返回 []"""
        try:
            from agent_matrix.tools import get_tools_for_agent
            allowed = self.config.get('allowed_tools')
            parsed = allowed
            if isinstance(parsed, str):
                try:
                    parsed = json.loads(parsed)
                except (json.JSONDecodeError, TypeError):
                    parsed = None
            self.config['allowed_tools_parsed'] = parsed if isinstance(parsed, list) else None
            # WP-3：按代理的 disallowed_tools 再裁剪一层
            from agent_matrix.tools import resolve_agent_tools
            return get_tools_for_agent(resolve_agent_tools(self.config, allowed))
        except Exception as e:
            logger.warning(f"[{self.name}] 加载工具失败，退回单轮: {e}")
            return []

    def _execute_batch(self, tool_calls, logs, task_id, tight, round_i,
                       parallel_enabled=False, task_phase=''):
        """WP-2：执行一批 tool_calls，返回按模型产出顺序的 [(tc, result)]。

        调度规则：
          - 仅当开关开启、批量 >1、且全部工具并发安全时才并行；
          - 并行中任一 future 抛异常 → 未启动/未完成的兄弟任务以合成错误
            取消（<tool_use_error>Cancelled: parallel tool call errored），
            已完成的结果正常保留；
          - 串行路径与 WP-2 引入前的逐个执行逐字节一致。
        工具级错误不触发取消（execute_tool 自身已兜底为错误字符串，
        交由模型在下一轮自行处理）。
        """
        allowed = self.config.get('allowed_tools_parsed')
        context = {'agent_id': self.agent_id, 'task_id': task_id,
                   'name': self.name, 'task_phase': task_phase,
                   # WP-8：透传父白名单 / 预算额度 / 子代理深度（spawn_subagent 使用）
                   'allowed_tools_parsed': allowed,
                   'token_budget': getattr(self, '_current_token_budget', 0),
                   'subagent_depth': getattr(self, '_subagent_depth', 0)}

        def _run_one(tc):
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except (json.JSONDecodeError, TypeError):
                args = {}
            self._log(task_id, 'info', 'tool_call', f'Call tool {name} args={args}')
            logs.append(f'[ReAct #{round_i}] 调用工具 {name}')
            return execute_tool(name, args, allowed, context=context)

        names = [tc.function.name for tc in tool_calls]
        do_parallel = (parallel_enabled and len(tool_calls) > 1
                       and all(get_tool_concurrency(n)[0] for n in names))
        results = [None] * len(tool_calls)
        if do_parallel:
            import concurrent.futures as _cf
            logs.append(f'[ReAct #{round_i}] 并行执行 {len(tool_calls)} 个工具')
            with _cf.ThreadPoolExecutor(max_workers=min(4, len(tool_calls))) as pool:
                futs = {pool.submit(_run_one, tc): i for i, tc in enumerate(tool_calls)}
                aborted = False
                for fut in _cf.as_completed(futs):
                    i = futs[fut]
                    if aborted:
                        results[i] = '<tool_use_error>Cancelled: parallel tool call errored</tool_use_error>'
                        continue
                    try:
                        results[i] = fut.result()
                    except Exception as e:
                        aborted = True
                        results[i] = ('<tool_use_error>Cancelled: parallel tool '
                                      f'call errored ({e})</tool_use_error>')
            for i, r in enumerate(results):
                if r is None:
                    results[i] = '<tool_use_error>Cancelled: parallel tool call errored</tool_use_error>'
        else:
            for i, tc in enumerate(tool_calls):
                results[i] = _run_one(tc)
        return list(zip(tool_calls, results))

    def _make_summarizer(self, engine):
        """WP-1：fit_messages 的摘要回调（仅 compress_level>=2 时接线）。

        把被截断丢弃的旧消息交给当前引擎压缩为要点。指令为英文（WP-0
        语言政策），输出语言跟随内容本身。任何异常返回 ''（fail-open，
        对齐 context_manager 铁律：绝不阻断主流程）。
        """
        def _summarize(dropped):
            try:
                blob_parts = []
                for m in dropped:
                    if not isinstance(m, dict):
                        continue
                    c = m.get('content')
                    if c:
                        blob_parts.append(str(c))
                    for tc in (m.get('tool_calls') or []):
                        fn = tc.get('function') if isinstance(tc, dict) else None
                        if isinstance(fn, dict) and fn.get('arguments'):
                            blob_parts.append(str(fn.get('arguments')))
                blob = '\n'.join(blob_parts)[:6000]
                if not blob.strip():
                    return ''
                raw = engine.ask(
                    'Summarize the following earlier conversation fragments '
                    '(messages and tool results) into at most 8 concise bullet '
                    'points. Reply in the same language as the content. Preserve '
                    'facts, numbers, file paths, and decisions. Output bullets '
                    'only, no preamble.\n\n' + blob,
                    temperature=0.2, trace_id=self._trace_id,
                    session_id=self._session_id)
                if isinstance(raw, str) and raw and not raw.startswith('Error:'):
                    return raw.strip()[:2000]
            except Exception as e:
                logger.warning(f'[{self.name}] context summarize failed: {e}')
            return ''

        return _summarize

    def _save_checkpoint(self, run_id, messages, round_i, last_text,
                         user_query, history, max_rounds, task_phase,
                         status='running'):
        """WP-6：原子写检查点 data/agent_runs/<run_id>.json（tmp + os.replace）。

        run_id 为 None（薄包装同步路径）时跳过，保持升级前行为零影响。
        写失败仅告警不阻断执行（fail-open，内核铁律）。
        """
        if not run_id:
            return
        try:
            from datetime import datetime, timezone
            ckpt_dir = _agent_runs_dir()
            os.makedirs(ckpt_dir, exist_ok=True)
            path = os.path.join(ckpt_dir, f'{os.path.basename(str(run_id))}.json')
            tmp = path + '.tmp'
            payload = {
                'run_id': str(run_id),
                'round': int(round_i),
                'last_text': last_text or '',
                'messages': messages,
                'user_query': user_query,
                'history': history,
                'max_rounds': int(max_rounds),
                'task_phase': task_phase or '',
                'agent_id': self.config.get('id'),
                'status': status,
                'updated_at': datetime.now(timezone.utc).isoformat(),
            }
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(tmp, path)
        except Exception as e:
            logger.warning(f'[{self.name}] 检查点写入失败: {e}')

    def _run_react_loop(self, engine, user_query, history, tools, logs, task_id,
                        max_rounds=5, task_phase='', cancel_event=None, gates=None,
                        run_id=None):
        """ReAct 工具循环（WP-4 薄包装）：消费 iter_react() 聚合，行为与升级前逐字节兼容。

        对 orchestrator / worker / A3.1 重试路径保持原签名与返回语义：
        正常返回终态文本；生成器内部异常原样上抛（由 _execute_impl 捕获）。
        cancel_event 传入时支持 WP-5 中断：取消返回已有文本。
        gates 传入时支持 WP-7 停机闸门（缺省 None = 关闭，行为零变化）。
        run_id 传入时支持 WP-6 检查点标识 / WP-8 子代理独立 run_id（缺省 None = 不落盘）。
        """
        last_text = ''
        for ev in self.iter_react(engine, user_query, history, tools, logs, task_id,
                                  max_rounds=max_rounds, task_phase=task_phase,
                                  stream=False, cancel_event=cancel_event, gates=gates,
                                  run_id=run_id):
            t = ev.get('type')
            if t == 'error':
                exc = ev.get('exc')
                if exc is not None:
                    raise exc
                return 'Error: ' + ev.get('message', '')
            if t == 'assistant_delta' and ev.get('text'):
                last_text = ev['text']
            if t in ('final', 'cancelled'):
                return ev.get('text', '') or last_text
        return last_text

    def iter_react(self, engine, user_query, history, tools, logs, task_id,
                   max_rounds=5, task_phase='', stream=False, cancel_event=None,
                   run_id=None, checkpoint=None, gates=None):
        """WP-4/WP-5/WP-6/WP-7：ReAct 循环事件生成器内核。

        语义与升级前 _run_react_loop 完全一致（同一套预算裁剪 / 工具执行 /
        消息回灌），只是产出从"单个返回字符串"改为"逐事件 yield"：

          round_start     {'type':'round_start', 'round': i}
          assistant_delta {'type':'assistant_delta', 'text': str}
          tool_call       {'type':'tool_call', 'index':.., 'id':.., 'name':.., 'arguments':..}
          tool_result     {'type':'tool_result', 'tool_call_id':.., 'name':.., 'content':..}
          final           {'type':'final', 'text': str}
          error           {'type':'error', 'message': str, 'exc': Exception|None}
          cancelled       {'type':'cancelled', 'reason': str, 'text': str}   # WP-5

        stream=False：默认路径，经 _run_react_loop 薄包装消费（同步 chat_with_tools，
        逐字节兼容）；stream=True：每轮走 engine.chat_stream + aggregate_stream_message
        聚合，由 /agent/stream SSE 端点消费。两条路径共用同一套后续逻辑。

        cancel_event（threading.Event，可选）：WP-5 中断闸门。三个检查点：
        每轮开始前 / 工具执行前 / 工具执行后。中断时保证 messages 无孤儿——
        工具执行前取消会对全部 tool_call 合成 role=tool 回填，再发 cancelled 终态。

        run_id（str，可选）：WP-6 检查点标识。非空时每轮结束原子写
        data/agent_runs/<run_id>.json，final/error/cancelled 时落终态 status。

        checkpoint（dict，可选）：WP-6 恢复入口。非空时跳过消息重建，直接以
        检查点 messages / round / last_text 续跑（round+1 起）。run_id、
        max_rounds、task_phase 优先取检查点内值。

        gates（LoopGates，可选）：WP-7 停机闸门组。缺省 None = 全部关闭，
        行为与升级前逐字节一致。挂点：轮前预算闸门 / 模型失败熔断重试 /
        轮末边际递减空转判定。
        """
        from agent_matrix.context_manager import (
            fit_messages, estimate_tokens, get_context_budget,
            get_effective_budget, get_compress_level,
            microcompact_tool_results, auto_compact_messages)
        from agent_matrix.engine import aggregate_stream_message
        from types import SimpleNamespace

        def _as_tool_call_obj(tc):
            """聚合器产出的 dict → 带属性对象，供 _execute_batch 复用。"""
            fn = tc.get('function') or {}
            return SimpleNamespace(
                id=tc.get('id', ''),
                type=tc.get('type', 'function'),
                function=SimpleNamespace(name=fn.get('name', ''),
                                         arguments=fn.get('arguments', '')))

        # WP-6：检查点恢复 → 直接沿用已持久化 messages 续跑（跳过重建）；
        # 否则按系统提示 + history + user_query 构建初始消息。
        _ck_user_query = user_query
        _ck_history = history
        if checkpoint and isinstance(checkpoint, dict) and checkpoint.get('messages'):
            messages = list(checkpoint['messages'])
            start_round = int(checkpoint.get('round', 0)) + 1
            run_id = run_id or checkpoint.get('run_id')
            max_rounds = int(checkpoint.get('max_rounds') or max_rounds)
            task_phase = checkpoint.get('task_phase') or task_phase
            last_text = checkpoint.get('last_text', '') or ''
            _ck_user_query = checkpoint.get('user_query', user_query)
            _ck_history = checkpoint.get('history', history)
        else:
            # WP-11：system = config system_prompt + 持久记忆分节（并存模式，
            # 分节头标注来源；空记忆/加载失败均按原样，fail-open 不阻断）。
            system_content = self.config.get('system_prompt', '')
            try:
                _memory = load_memory_prompt(self.agent_id)
                if _memory:
                    system_content = (system_content + '\n\n' + _memory
                                      if system_content else _memory)
            except Exception as e:
                logger.warning(f'[{self.name}] 加载持久记忆失败，按无记忆处理: {e}')
            messages = [{"role": "system", "content": system_content}]
            if history:
                for h in history:
                    role = 'user' if h.get('role') == 'user' else 'assistant'
                    messages.append({"role": role, "content": h.get('content', '')})
            messages.append({"role": "user", "content": user_query})
            start_round = 1
            last_text = ''

        # WP-A（6.3）：预算只读一次（循环外），循环内复用，避免每轮打库。
        # 读取失败按关闭处理（0 = 直通），与引入前行为逐字节一致。
        # L2'：窗口感知收口 —— min(system 预算, 模型窗口−预留)。system
        # 预算 = 0（关闭）必须保持 0：get_effective_budget 对 <=0 退化为
        # 窗口上限，直接喂会把"关闭"变成"启用"，故先守卫再收口。
        # -1（哨兵，窗口自适应）经 get_effective_budget 后即为窗口上限，
        # 属预期行为，一并放行。
        try:
            _sys_budget = get_context_budget()
            ctx_budget = (get_effective_budget(
                {'provider': self.config.get('provider'),
                 'model': self.config.get('model_name')},
                _sys_budget) if _sys_budget != 0 else 0)
        except Exception as e:
            logger.warning(f'[{self.name}] 读取上下文预算失败，按关闭处理: {e}')
            ctx_budget = 0
        # WP-1：压缩等级只读一次（0=关 1=仅截断 2=截断+微压缩+摘要
        # 3=再叠加 WP-10 主动压缩）。读取失败按 1（仅截断）处理，
        # 与 WP-A 引入前的实际行为一致。
        try:
            compress_level = get_compress_level()
        except Exception as e:
            logger.warning(f'[{self.name}] 读取压缩等级失败，按仅截断处理: {e}')
            compress_level = 1
        _summarize = self._make_summarizer(engine) if (compress_level >= 2 and ctx_budget > 0) else None
        # WP-2：并发开关每任务只读一次（默认关闭 = 串行路径，与原实现一致）
        try:
            parallel_enabled = get_parallel_enabled()
        except Exception as e:
            logger.warning(f'[{self.name}] 读取并发开关失败，按串行处理: {e}')
            parallel_enabled = False
        TOOL_RESULT_NORMAL = 4000
        TOOL_RESULT_TIGHT = 1200

        def _cancelled():
            """WP-5：取消闸门（未注册 cancel_event 时永不触发）。"""
            return cancel_event is not None and cancel_event.is_set()

        def _backfill_interrupted(tool_calls):
            """中断时为本轮所有 tool_call 合成 role=tool 回填（协议结构合法性）。"""
            for tc in tool_calls:
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "content": "Interrupted by user/timeout"})

        # WP-6：checkpoint 恢复时从 start_round 续跑；正常路径从 1 开始。
        for round_i in range(start_round, max_rounds + 1):
            # WP-5：检查点 1 —— 每轮开始前取消
            if _cancelled():
                self._save_checkpoint(run_id, messages, round_i - 1, last_text,
                                      _ck_user_query, _ck_history, max_rounds,
                                      task_phase, status='cancelled')
                yield {'type': 'cancelled', 'reason': 'user_cancelled', 'text': last_text}
                return
            yield {'type': 'round_start', 'round': round_i}

            # WP-7：预算闸门 —— 消费 ≥ 阈值即停机（续轮推送预算提示）
            # WP-12：soft 模式不停机，改为注入一次 nudge 软收尾
            if gates:
                _gate_reason = gates.budget_stop()
                if _gate_reason:
                    last_text = _gate_reason
                    self._save_checkpoint(run_id, messages, round_i - 1, last_text,
                                          _ck_user_query, _ck_history, max_rounds,
                                          task_phase, status='completed')
                    yield {'type': 'final', 'text': _gate_reason}
                    return
                _nudge = gates.budget_nudge()
                if _nudge:
                    messages.append({'role': 'system', 'content': _nudge})
                    logger.info(f'[{self.name}] {_nudge}')

            # WP-A：每轮进模型前按预算裁剪。循环内会追加 assistant(tool_calls) 与 tool
            # 响应，正是配对风险所在 —— fit_messages 按不可分割单元整体保留或丢弃。
            # WP-10：compress_level>=3 时先做主动压缩（中段全量摘要重建），
            # 再走原两级；等级 <3 时本分支不执行，行为逐字节不变。
            tight = False
            if ctx_budget > 0:
                if compress_level >= 3:
                    messages = auto_compact_messages(
                        messages, ctx_budget, summarize=_summarize, keep_recent=6)
                if compress_level >= 2:
                    messages, _cleared = microcompact_tool_results(
                        messages, budget=ctx_budget, keep_recent=6)
                messages = fit_messages(messages, ctx_budget, keep_recent=6,
                                        summarize=_summarize)
                tight = estimate_tokens(messages) >= int(ctx_budget * 0.9)

            # ── 模型调用：流式聚合 / 同步 双路径，产出统一事件 ──
            try:
                if stream:
                    chunk_iter = engine.chat_stream(
                        messages, tools=tools, tool_choice='auto', module='tool_call',
                        trace_id=self._trace_id, session_id=self._session_id)
                    msg = aggregate_stream_message(chunk_iter)
                    if isinstance(msg, str):      # Error: 前缀错误帧
                        raise RuntimeError(msg)
                    if msg is None:
                        logs.append(f'[ReAct #{round_i}] 工具调用返回空，退回普通对话')
                        fallback = engine.ask(user_query,
                                              trace_id=self._trace_id,
                                              session_id=self._session_id)
                        self._save_checkpoint(run_id, messages, round_i - 1, last_text,
                                              _ck_user_query, _ck_history, max_rounds,
                                              task_phase, status='completed')
                        yield {'type': 'final',
                               'text': fallback if not last_text else last_text}
                        return
                    content = msg.get('content', '')
                    if content:
                        last_text = content
                        yield {'type': 'assistant_delta', 'text': content}
                    tc_dicts = msg.get('tool_calls') or []
                    for idx, tc in enumerate(tc_dicts):
                        yield {'type': 'tool_call', 'index': idx,
                               'id': tc.get('id', ''),
                               'name': (tc.get('function') or {}).get('name', ''),
                               'arguments': (tc.get('function') or {}).get('arguments', '')}
                    tool_calls = [_as_tool_call_obj(tc) for tc in tc_dicts]
                    assistant_msg = msg
                else:
                    resp = engine.chat_with_tools(
                        messages, tools, trace_id=self._trace_id,
                        session_id=self._session_id)
                    if resp is None:
                        logs.append(f'[ReAct #{round_i}] 工具调用返回空，退回普通对话')
                        fallback = engine.ask(user_query,
                                              trace_id=self._trace_id,
                                              session_id=self._session_id)
                        self._save_checkpoint(run_id, messages, round_i - 1, last_text,
                                              _ck_user_query, _ck_history, max_rounds,
                                              task_phase, status='completed')
                        yield {'type': 'final',
                               'text': fallback if not last_text else last_text}
                        return
                    choice_msg = resp.choices[0].message
                    if choice_msg.content:
                        last_text = choice_msg.content
                        yield {'type': 'assistant_delta', 'text': choice_msg.content}
                    raw_tcs = getattr(choice_msg, 'tool_calls', None) or []
                    for idx, tc in enumerate(raw_tcs):
                        yield {'type': 'tool_call', 'index': idx, 'id': tc.id,
                               'name': tc.function.name,
                               'arguments': tc.function.arguments}
                    tool_calls = raw_tcs
                    assistant_msg = {
                        "role": "assistant",
                        "content": choice_msg.content or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments
                                }
                            } for tc in raw_tcs
                        ]
                    }
            except Exception as e:
                # WP-7：FailureBreaker —— 连续失败重试、达阈值熔断终止；
                # 缺省关闭时保持 WP-5 行为（失败即 error 终止）。
                if gates and gates.failure.enabled:
                    gates.failure.record_failure(e)
                    logs.append(f'[ReAct #{round_i}] LLM failed '
                                f'({gates.failure.fail_count()}), retry')
                    if gates.failure.should_trip():
                        logger.warning(f'[{self.name}] ReAct round {round_i} '
                                       f'circuit broken: {e}')
                        self._save_checkpoint(run_id, messages, round_i - 1,
                                              last_text, _ck_user_query,
                                              _ck_history, max_rounds,
                                              task_phase, status='error')
                        yield {'type': 'error',
                               'message': f'LLM failed {gates.failure.fail_count()} '
                                          f'consecutive times, circuit broken: {e}',
                               'exc': e}
                        return
                    logger.warning(f'[{self.name}] ReAct round {round_i} '
                                   f'failed ({gates.failure.fail_count()}), retrying next round: {e}')
                    continue
                logger.warning(f'[{self.name}] ReAct round {round_i} failed: {e}')
                self._save_checkpoint(run_id, messages, round_i - 1, last_text,
                                      _ck_user_query, _ck_history, max_rounds,
                                      task_phase, status='error')
                yield {'type': 'error', 'message': str(e), 'exc': e}
                return

            # 模型未请求工具 → 终态答复
            if not tool_calls:
                self._log(task_id, 'info', 'execution',
                          f'ReAct ended at round {round_i} (No More Tool Calls)')
                self._save_checkpoint(run_id, messages, round_i, last_text,
                                      _ck_user_query, _ck_history, max_rounds,
                                      task_phase, status='completed')
                yield {'type': 'final', 'text': last_text or ''}
                return

            # 把 assistant 的 tool_calls 消息追加回上下文
            messages.append(assistant_msg)

            # WP-5：检查点 2 —— 工具执行前取消 → 全部合成回填（无孤儿）
            if _cancelled():
                _backfill_interrupted(tool_calls)
                self._save_checkpoint(run_id, messages, round_i - 1, last_text,
                                      _ck_user_query, _ck_history, max_rounds,
                                      task_phase, status='cancelled')
                yield {'type': 'cancelled', 'reason': 'user_cancelled', 'text': last_text}
                return

            # 执行工具并按序回灌（WP-2：并发窗口；串行路径与原实现一致）
            for tc, result in self._execute_batch(
                    tool_calls, logs, task_id, tight, round_i,
                    parallel_enabled=parallel_enabled):
                result_content = str(result)[:TOOL_RESULT_TIGHT if tight else TOOL_RESULT_NORMAL]
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    # WP-A：预算紧张时收紧单条工具结果上限，避免观察回灌挤爆上下文
                    "content": result_content
                })
                yield {'type': 'tool_result', 'tool_call_id': tc.id,
                       'name': tc.function.name, 'content': result_content}

            # WP-5：检查点 3 —— 工具执行后取消（本轮已正常回填，无孤儿）
            if _cancelled():
                self._save_checkpoint(run_id, messages, round_i - 1, last_text,
                                      _ck_user_query, _ck_history, max_rounds,
                                      task_phase, status='cancelled')
                yield {'type': 'cancelled', 'reason': 'user_cancelled', 'text': last_text}
                return

            # WP-7：边际递减闸门 —— 连续 N 轮消费增量低且无新工具 → 空转停机
            if gates:
                _gate_spent = gates.token.spent() if gates.token.enabled else 0
                gates.observe(_gate_spent, [tc.function.name for tc in tool_calls])
                _gate_reason = gates.diminish_stop()
                if _gate_reason:
                    last_text = _gate_reason
                    self._save_checkpoint(run_id, messages, round_i, last_text,
                                          _ck_user_query, _ck_history, max_rounds,
                                          task_phase, status='completed')
                    yield {'type': 'final', 'text': _gate_reason}
                    return

            # WP-6：每轮结束持久化检查点（断流/崩溃后可恢复续跑）
            self._save_checkpoint(run_id, messages, round_i, last_text,
                                  _ck_user_query, _ck_history, max_rounds,
                                  task_phase, status='running')

        # 达到轮次上限，做最后一次无工具收尾
        logs.append(f'[ReAct] Reached maximum rounds {max_rounds}, forced termination')
        final = engine.chat_with_tools(messages, tools,
                                       trace_id=self._trace_id,
                                       session_id=self._session_id)
        if final is not None:
            final_choice = final.choices[0].message
            if final_choice.content:
                last_text = final_choice.content
        self._save_checkpoint(run_id, messages, max_rounds, last_text,
                              _ck_user_query, _ck_history, max_rounds,
                              task_phase, status='completed')
        yield {'type': 'final',
               'text': last_text or _('(Tool loop reached limit, no final response generated)')}

    def _build_query(self, task):
        """构造发给 LLM 的用户消息"""
        parts = [f"## Task: {task.get('title', '')}"]

        description = task.get('description', '')
        if description:
            parts.append(f"\n{description}")

        input_data = task.get('input_data', {})
        if isinstance(input_data, str):
            try:
                input_data = json.loads(input_data)
            except (json.JSONDecodeError, TypeError):
                pass
        if isinstance(input_data, dict) and input_data:
            parts.append("\n### Input parameters:")
            for k, v in input_data.items():
                if isinstance(v, str) and len(v) > 500:
                    parts.append(f"- {k}: {v[:500]}...")
                else:
                    parts.append(f"- {k}: {v}")

        expected = task.get('expected_output', {})
        if isinstance(expected, str):
            try:
                expected = json.loads(expected)
            except (json.JSONDecodeError, TypeError):
                pass
        if expected:
            parts.append(f"\n### Expected output:\n{json.dumps(expected, ensure_ascii=False, indent=2)}")

        parts.append("\nExecute the task strictly according to the requirements above. Output the final result when done.")
        return '\n'.join(parts)

    def _self_critique(self, response, task):
        """自检：先做规则初判，灰区(0.5~0.8)时再让 LLM 做结构化自评"""
        expected = task.get('expected_output', {})
        if isinstance(expected, str):
            try:
                expected = json.loads(expected)
            except (json.JSONDecodeError, TypeError):
                expected = {}

        # 1. 规则初判：检查输出长度和格式
        review_parts = []
        confidence = 0.85

        if len(response) < 50:
            review_parts.append("Output is too short (<50 characters)")
            confidence = max(0.3, confidence - 0.3)
        else:
            review_parts.append(f"Output length is reasonable ({len(response)} characters)")

        if isinstance(expected, dict) and expected.get('fields'):
            # 检查是否包含期望字段
            matched = sum(1 for f in expected['fields'] if f in response.lower())
            field_ratio = matched / len(expected['fields'])
            if field_ratio < 0.5:
                review_parts.append(f"Low expected field match rate ({matched}/{len(expected['fields'])})")
                confidence = max(0.4, confidence - 0.2)

        if 'Error' in response or _('Error') in response or _('Failed"') in response:
            review_parts.append(_("Output contains error/failure information"))
            confidence = max(0.2, confidence - 0.3)

        confidence = round(confidence, 2)
        result = {
            'confidence': confidence,
            'review': '; '.join(review_parts),
            'issues': [],
            'suggestion': ''
        }

        # 2. 灰区触发 LLM 结构化自评（仅 0.5~0.8 之间，控制成本）
        if 0.5 <= confidence <= 0.8:
            llm_review = self._llm_critique(response, task)
            if llm_review:
                result.update(llm_review)

        return result

    def _llm_critique(self, response, task):
        """让 LLM 对输出做结构化自评，失败时返回 None 由规则结果兜底"""
        engine = self._get_engine()
        if not engine or not engine.is_ready():
            return None

        critique_prompt = (
            "You are a strict quality reviewer. Evaluate whether the TASK below was "
            "fulfilled by the OUTPUT. Output pure JSON only (no markdown code fences), "
            "in this format:\n"
            '{"confidence": 0.0-1.0, "issues": ["issue 1", ...], "suggestion": "improvement suggestion"}\n\n'
            f"[TASK] {task.get('title', '')}\n{task.get('description', '')}\n\n"
            f"[OUTPUT]\n{response[:2000]}"
        )
        try:
            raw = engine.ask(critique_prompt, temperature=0.2,
                             trace_id=self._trace_id, session_id=self._session_id)
            if not raw or raw.startswith('Error:'):
                return None
            import re as _re
            match = _re.search(r'\{[\s\S]*\}', raw)
            if not match:
                return None
            data = json.loads(match.group())
            conf = float(data.get('confidence', data.get('confidence_', 0.85)))
            conf = round(max(0.0, min(1.0, conf)), 2)
            issues = data.get('issues', []) or []
            suggestion = data.get('suggestion', '') or ''
            review = 'LLM self-evaluation: ' + (suggestion or 'Approved')
            if issues:
                review += ' | Issues: ' + '; '.join(str(i) for i in issues)
            return {
                'confidence': conf,
                'review': review,
                'issues': issues,
                'suggestion': suggestion
            }
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            logger.warning(f"[{self.name}] LLM 自评解析失败，回退规则结果: {e}")
            return None

    def _log(self, task_id, level, log_type, message):
        if self.models:
            try:
                self.models.add_log(task_id, self.agent_id, level, log_type, message)
            except Exception:
                pass

    def _build_gates(self, task, task_id):
        """WP-7：构造三闸门组（env 可配、缺省关闭）。构造异常按关闭处理。"""
        try:
            from agent_matrix.loop_gates import build_gates
            return build_gates(task, task_id)
        except Exception as e:
            logger.warning(f'[{self.name}] gates build failed, disabled: {e}')
            return None

    def _fail(self, error, logs, confidence=0.0):
        return {
            'status': 'failed',
            'response': error,
            'confidence': confidence,
            'self_review': '',
            'logs': logs + [f'[FAIL] {error}'],
            'failed': True
        }
