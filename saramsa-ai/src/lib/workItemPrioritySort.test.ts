import { describe, test, expect } from 'vitest'
import { workItemPriorityRank, sortWorkItemsByPriority } from './workItemPrioritySort'

describe('workItemPriorityRank', () => {
  test('returns 0 for "critical"', () => {
    expect(workItemPriorityRank('critical')).toBe(0)
  })

  test('returns 4 for unknown priorities', () => {
    expect(workItemPriorityRank('banana')).toBe(4)
  })

  test('defaults undefined to "medium" (rank 2)', () => {
    expect(workItemPriorityRank(undefined)).toBe(2)
  })
})

describe('sortWorkItemsByPriority', () => {
  test('sorts critical → high → medium → low', () => {
    const input = [
      { id: 1, priority: 'low' },
      { id: 2, priority: 'critical' },
      { id: 3, priority: 'medium' },
      { id: 4, priority: 'high' },
    ]
    const result = sortWorkItemsByPriority(input)
    expect(result.map((i) => i.id)).toEqual([2, 4, 3, 1])
  })

  test('does not mutate the original array', () => {
    const input = [{ id: 1, priority: 'low' }, { id: 2, priority: 'high' }]
    sortWorkItemsByPriority(input)
    expect(input[0].priority).toBe('low')
  })
})
