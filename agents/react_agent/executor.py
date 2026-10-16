import concurrent.futures
from typing import Callable, Dict, List, Optional, Set

from .planner import StepNode

class TaskScheduler:
    """任务调度器 - 基于DAG的拓扑排序 + 并行执行

    调度流程：
    1. 对 StepNode 列表做拓扑分层（get_execution_order）
    2. 同一层内无依赖的步骤并行执行
    3. 层间串行（上层全部完成后才进入下一层）
    4. 单步失败可重试
    """

    def __init__(self, steps: List[StepNode], max_workers: int = 3, max_retries: int = 1):
        self.steps = steps
        self.max_workers = max_workers
        self.max_retries = max_retries
        self._step_map: Dict[int, StepNode] = {s.index: s for s in steps}

    def get_execution_order(self) -> List[List[StepNode]]:
        """拓扑分层：返回每一层可并行执行的步骤组

        Returns:
            [[step1], [step2, step3], [step4]]  表示3层，
            第2层的step2和step3可并行
        """
        remaining = set(s.index for s in self.steps)
        completed: Set[int] = set()
        layers: List[List[StepNode]] = []

        while remaining:
            ready = []
            for idx in sorted(remaining):
                step = self._step_map[idx]
                if all(d in completed for d in step.depends_on):
                    ready.append(step)
            if not ready:
                raise ValueError(f"存在循环依赖，无法调度: 剩余步骤 {remaining}")
            layers.append(ready)
            for s in ready:
                remaining.discard(s.index)
                completed.add(s.index)

        return layers

    def execute(
        self,
        execute_fn: Callable[[StepNode], str],
        on_step_start: Optional[Callable[[StepNode], None]] = None,
        on_step_done: Optional[Callable[[StepNode, str], None]] = None,
    ) -> Dict[int, str]:
        """按拓扑分层执行所有步骤

        Args:
            execute_fn:     (StepNode) -> str  单步执行函数
            on_step_start:  (StepNode) -> None  步骤开始回调
            on_step_done:   (StepNode, str) -> None  步骤完成回调

        Returns:
            {step_index: result_str}
        """
        layers = self.get_execution_order()
        results: Dict[int, str] = {}

        for layer_idx, layer in enumerate(layers):
            is_parallel = len(layer) > 1
            if is_parallel:
                print(f"  ⚡ 并行执行层 {layer_idx + 1} ({len(layer)} 个步骤)")
            else:
                print(f"  ▶ 串行执行层 {layer_idx + 1}")

            if len(layer) == 1:
                step = layer[0]
                if on_step_start:
                    on_step_start(step)
                result = self._execute_with_retry(step, execute_fn)
                results[step.index] = result
                if on_step_done:
                    on_step_done(step, result)
            else:
                with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                    futures = {}
                    for step in layer:
                        if on_step_start:
                            on_step_start(step)
                        fut = pool.submit(self._execute_with_retry, step, execute_fn)
                        futures[fut] = step
                    for fut in concurrent.futures.as_completed(futures):
                        step = futures[fut]
                        try:
                            result = fut.result()
                        except Exception as e:
                            result = f"❌ 执行异常: {e}"
                        results[step.index] = result
                        step.result = result
                        if on_step_done:
                            on_step_done(step, result)

        return results

    def _execute_with_retry(self, step: StepNode, execute_fn: Callable[[StepNode], str]) -> str:
        for attempt in range(1, self.max_retries + 1):
            try:
                step.status = "running"
                result = execute_fn(step)
                step.status = "done"
                step.result = result
                return result
            except Exception as e:
                step.error = str(e)
                if attempt < self.max_retries:
                    print(f"    ⚠️ 步骤 {step.index} 第{attempt}次失败，重试中...")
                    continue
                step.status = "failed"
                return f"❌ 步骤 {step.index} 执行失败(已重试{self.max_retries}次): {e}"