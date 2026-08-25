import { describe, expect, it } from 'vitest'
import {
  createManualSource,
  getAggregateStats,
  isWalletAddress,
  parseAddressLines,
  parseImportFile,
} from './importHelpers'

const first = '0x1111111111111111111111111111111111111111'
const second = '0x2222222222222222222222222222222222222222'

describe('wallet import helpers', () => {
  it('recognizes Ethereum wallet addresses', () => {
    expect(isWalletAddress(first)).toBe(true)
    expect(isWalletAddress('0x1234')).toBe(false)
  })

  it('parses lines and counts invalid non-empty values', () => {
    expect(parseAddressLines(`${first}\ninvalid\n\n${second}`)).toEqual({
      addresses: [first, second],
      invalidCount: 1,
    })
  })

  it('aggregates unique, duplicate, and invalid counts across sources', () => {
    const sourceA = createManualSource(`${first}\n${second}\ninvalid`)
    const sourceB = createManualSource(first.toUpperCase().replace('0X', '0x'))
    expect(sourceA).not.toBeNull()
    expect(sourceB).not.toBeNull()
    expect(getAggregateStats([sourceA!, sourceB!])).toMatchObject({
      valid: 3,
      unique: 2,
      duplicates: 1,
      invalid: 1,
    })
  })

  it('reads the strict index and wallet_address tabular format', async () => {
    const file = new File([`index,wallet_address\n1,${first}\n2,invalid`], 'wallets.csv')
    const source = await parseImportFile(file)
    expect(source.addresses).toEqual([first])
    expect(source.invalidCount).toBe(1)
  })

  it('rejects tabular files with extra columns', async () => {
    const file = new File(
      [`index,wallet_address,label\n1,${first},extra`],
      'wallets.csv',
    )
    await expect(parseImportFile(file)).rejects.toThrow('нужны только столбцы')
  })
})
