# Zhongshu Solver Evidence Routing Design

## Goal

Prevent the ZHONGSHU Solver from advancing to Critic when an active Critic finding remains unresolved because evidence is missing. Route that response to ZHONGSHU Analyst so the workflow can collect the missing evidence.

## Design

During Solver revision normalization, inspect the required Critic finding resolutions. `READY_FOR_CRITIC` is valid only when every required resolution has `status=resolved`. If any required resolution is explicitly or implicitly unresolved, normalize the action to `REQUEST_ANALYST_EVIDENCE` and preserve the materialized current plan. The existing transition policy then sends the accepted event to `ZHONGSHU_ANALYST`.

The Solver response remains structurally validated: missing or duplicate finding resolutions are still rejected as contract errors. Only a complete, well-formed response with unresolved findings is rerouted. The Analyst prompt already has a feedback path for `REQUEST_ANALYST_EVIDENCE`; the consumed Solver payload will update the stored review action and preserve the candidate plan so the next Analyst turn has the required context.

## Testing

Add regression coverage for an unresolved revision finding being normalized to `REQUEST_ANALYST_EVIDENCE`, for the resolved case remaining `READY_FOR_CRITIC`, and for the resulting state-machine transition to `ZHONGSHU_ANALYST`.
