import { describe, test, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// framer-motion has SSR quirks in jsdom — render motion.X as plain elements.
vi.mock('framer-motion', () => ({
  motion: new Proxy({}, { get: () => (props: any) => <div {...props} /> }),
}))
// The component only reads selectAnalysisDisplayStatus (a label string) via useSelector.
vi.mock('react-redux', () => ({ useSelector: () => 'Analyzing...' }))

import { AnalysisRunItem } from './AnalysisRunItem'

const entry = (over: any = {}) => ({
  id: 'e1', analysis_date: '2026-01-01', comments_count: 5, positive_pct: 50, status: 'completed', ...over,
})
const props = (over: any = {}) => ({
  entry: entry(), isActive: false, onClick: vi.fn(), onRename: vi.fn(), index: 0, ...over,
})

describe('AnalysisRunItem — partial/failed badge + Retry', () => {
  test('a partial run shows "Partially completed" + a Retry control', () => {
    render(<AnalysisRunItem {...props({ entry: entry({ status: 'partial' }), onRetry: vi.fn() })} />)
    expect(screen.getByText(/partially completed/i)).toBeTruthy()
    expect(screen.getByText(/^retry$/i)).toBeTruthy()
  })

  test('a failed run shows "Failed" + a Retry control', () => {
    render(<AnalysisRunItem {...props({ entry: entry({ status: 'failed' }), onRetry: vi.fn() })} />)
    expect(screen.getByText(/^failed$/i)).toBeTruthy()
    expect(screen.getByText(/^retry$/i)).toBeTruthy()
  })

  test('clicking Retry calls onRetry with the entry id', async () => {
    const onRetry = vi.fn().mockResolvedValue(undefined)
    render(<AnalysisRunItem {...props({ entry: entry({ id: 'analyzing_5', status: 'failed' }), onRetry })} />)
    await userEvent.click(screen.getByText(/^retry$/i))
    expect(onRetry).toHaveBeenCalledWith('analyzing_5')
  })

  test('a completed run shows no Retry button', () => {
    render(<AnalysisRunItem {...props({ entry: entry({ status: 'completed' }), onRetry: vi.fn() })} />)
    expect(screen.queryByText(/^retry$/i)).toBeNull()
  })

  test('no Retry control when onRetry is not provided', () => {
    render(<AnalysisRunItem {...props({ entry: entry({ status: 'failed' }) })} />)
    expect(screen.getByText(/^failed$/i)).toBeTruthy()      // badge still shows
    expect(screen.queryByText(/^retry$/i)).toBeNull()        // but no Retry without a handler
  })
})
