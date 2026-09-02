# Future Incident IQ ticket-adjustment automation

## Candidate use case: category correction

Detect tickets submitted into broad catch-all categories, compare the ticket's actual intent with the current IIQ taxonomy, and recommend a more specific category. A later write-enabled product could apply an approved recommendation.

### Synthetic example

- Ticket: `T-100001` (synthetic)
- Request intent: add a user to a collaboration group
- Current category: `Other Requests > Issue Not Listed`
- Recommended category: `User Accounts > Collaboration > Group membership changes`
- Rationale: the recommended category directly represents group membership additions and changes and is already used by the appropriate support team for similar work.

This is a strong candidate for category correction because the current category is explicitly nonspecific and a substantially more precise active taxonomy value exists.

## Recommended workflow

1. Retrieve the complete ticket and timeline with `iiq_get_technician_ticket_context`.
2. Determine the ticket's primary requested outcome from the original description and subsequent requester clarification. Do not infer intent solely from keywords in the subject.
3. If the current category is a catch-all such as `Issue Not Listed`, use `iiq_find_ticket_filters` once with the most distinctive service or task phrase.
4. Compare a small candidate set against the request. Optionally use recent tickets in the candidate category as supporting evidence, with a tightly bounded date range.
5. Return the proposed category, confidence, rationale, and evidence. Clearly distinguish the recommendation from a completed change.
6. During the pilot phase, require a technician to approve the old-to-new category change.
7. If write access is introduced later, send only the approved category identifier to a narrow adjustment service. Record the ticket number, old category, new category, approving technician, timestamp, confidence, reason, and correlation ID.

## Confidence and stop rules

Recommend a category change only when:

- the requested outcome is clear;
- the candidate is an exact active IIQ taxonomy value;
- the candidate is materially more specific than the current category; and
- there is no conflicting evidence in the timeline.

Stop and request technician review when:

- multiple candidates are similarly plausible;
- the ticket combines unrelated requests;
- the intended affected service, user, asset, or outcome is missing;
- the proposed category would change ownership, SLA, priority, or workflow in an uncertain way;
- sensitive security, HR, legal, or student information is involved; or
- the API returns an unexpected or inactive taxonomy value.

Never allow an AI-generated free-text category or category ID. The adjustment service must accept only a category ID resolved from the current IIQ taxonomy.

## Rollout stages

1. **Recommendation only:** measure agreement between recommendations and technician decisions.
2. **Approval-assisted adjustment:** a technician explicitly approves each proposed change before n8n or another orchestrator calls the adjustment service.
3. **Limited automatic adjustment:** allow only repeatedly validated intent-to-category mappings with a high confidence threshold and a reversible audit trail.
4. **Ongoing review:** monitor incorrect routing, reassignment, reopened tickets, SLA changes, and technician overrides by mapping.

## Efficiency note

The assistant should not fan out across many filter and ticket searches. Prefer one taxonomy lookup followed by at most a few evidence searches. If the candidate remains ambiguous, stop and ask the technician instead of continuing exploratory tool calls.

## Possible future mapping

| Intent | Candidate IIQ category | Initial mode |
|---|---|---|
| Add or remove a member from a collaboration group | `User Accounts > Collaboration > Group membership changes` | Recommend with approval |

Treat this table as a reviewed mapping registry, not as a prompt-only suggestion. Store the exact IIQ category ID alongside the display name when the write-enabled adjustment service is designed.
