import { describe, test, expect } from 'vitest'
import { workItemPriorityRank, sortWorkItemsByPriority, workItemPriorityNumber } from './workItemPrioritySort'

describe('workItemPriorityNumber', () => {
  test('maps the word form onto 1-4, 1 being most urgent', () => {
    expect(workItemPriorityNumber('critical')).toBe(1)
    expect(workItemPriorityNumber('high')).toBe(2)
    expect(workItemPriorityNumber('medium')).toBe(3)
    expect(workItemPriorityNumber('low')).toBe(4)
  })

  test('is case- and whitespace-insensitive', () => {
    expect(workItemPriorityNumber('  Critical ')).toBe(1)
    expect(workItemPriorityNumber('HIGH')).toBe(2)
  })

  test('normalizes the legacy P0-P3 form the rules engine still emits', () => {
    expect(workItemPriorityNumber('P0')).toBe(1)
    expect(workItemPriorityNumber('p3')).toBe(4)
  })

  test('passes through values that are already numeric', () => {
    expect(workItemPriorityNumber('1')).toBe(1)
    expect(workItemPriorityNumber('4')).toBe(4)
  })

  test('returns null when absent or unrecognized, so the badge is omitted', () => {
    expect(workItemPriorityNumber(undefined)).toBeNull()
    expect(workItemPriorityNumber('')).toBeNull()
    expect(workItemPriorityNumber('banana')).toBeNull()
    expect(workItemPriorityNumber('0')).toBeNull()
    expect(workItemPriorityNumber('9')).toBeNull()
  })
})

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

  test('within the same priority tier, sorts by descending commentCount', () => {
    const input = [
      { id: 1, priority: 'high', commentCount: 3 },
      { id: 2, priority: 'high', commentCount: 20 },
      { id: 3, priority: 'high', commentCount: 8 },
    ]
    const result = sortWorkItemsByPriority(input)
    expect(result.map((i) => i.id)).toEqual([2, 3, 1])
  })

  test('priority still wins over commentCount across tiers', () => {
    const input = [
      { id: 1, priority: 'medium', commentCount: 500 },
      { id: 2, priority: 'critical', commentCount: 1 },
    ]
    const result = sortWorkItemsByPriority(input)
    expect(result.map((i) => i.id)).toEqual([2, 1])
  })

  test('items without commentCount sort after those with a count, within a tier', () => {
    const input = [
      { id: 1, priority: 'low' },
      { id: 2, priority: 'low', commentCount: 5 },
    ]
    const result = sortWorkItemsByPriority(input)
    expect(result.map((i) => i.id)).toEqual([2, 1])
  })
})
