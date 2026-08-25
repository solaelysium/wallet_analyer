import { describe, expect, it } from 'vitest'
import { formatFeatureValue } from './formatters'


describe('formatFeatureValue', () => {
  it('does not multiply values already stored as percent points', () => {
    const formatted = formatFeatureValue(
      29.531202872488265,
      'percent',
      'stable_token_volume_share_percent',
    )

    expect(formatted).toContain('29,53')
    expect(formatted).not.toContain('2 953')
  })

  it('still converts ratio values to percentages', () => {
    expect(formatFeatureValue(0.2953, 'percent', 'winrate')).toContain('29,53')
  })

  it('renders unavailable strict financial metrics as a dash', () => {
    expect(formatFeatureValue(null, 'currency', 'realized_pnl_usd')).toBe('—')
    expect(formatFeatureValue(null, 'percent', 'roi')).toBe('—')
  })
})
