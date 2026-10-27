# Evaluations

Online evaluation `airxpact_online_eval` runs on the `DEFAULT` endpoint of the agent runtime.

| Evaluator | Type | Level |
| --- | --- | --- |
| Builtin.GoalSuccessRate, Helpfulness, Correctness, Faithfulness, InstructionFollowing, ToolSelectionAccuracy, ToolParameterAccuracy, Harmfulness | Built-in | Session / trace / tool |
| `airxpact_aviation_quality` (`aviation-quality.prompt.txt`) | LLM-as-a-judge | Trace |
| `airxpact_consistency` (`consistency.prompt.txt`) | LLM-as-a-judge | Trace |

Scales:

- `airxpact_aviation_quality`: 1.0 Excellent, 0.66 Acceptable, 0.33 Weak, 0.0 Unsafe
- `airxpact_consistency`: 1.0 Consistent, 0.5 Minor issues, 0.0 Inconsistent

`acceptance-tests.md` lists the end-to-end scenarios used before each release.
