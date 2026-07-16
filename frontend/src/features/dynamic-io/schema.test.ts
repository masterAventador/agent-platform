import { describe, expect, it } from 'vitest'

import { collectDynamicInput, hasDynamicInputSchema } from './schema'

describe('collectDynamicInput', () => {
  it('does not coerce an untouched optional boolean into false', () => {
    const schema = {
      type: 'object',
      properties: {
        urgent: { type: 'boolean', title: '紧急' },
      },
    }

    expect(collectDynamicInput(schema, {}, {})).toEqual({ ok: true, input: {} })
    expect(collectDynamicInput(schema, { urgent: false }, {})).toEqual({
      ok: true,
      input: { urgent: false },
    })
  })

  it('submits an untouched required boolean as false', () => {
    const schema = {
      type: 'object',
      required: ['confirmed'],
      properties: {
        confirmed: { type: 'boolean', title: '确认执行' },
      },
    }

    expect(collectDynamicInput(schema, {}, {})).toEqual({
      ok: true,
      input: { confirmed: false },
    })
  })

  it('treats an explicit zero-field object schema as dynamic empty input', () => {
    const schema = { type: 'object', additionalProperties: false }

    expect(hasDynamicInputSchema({ type: 'object' })).toBe(false)
    expect(hasDynamicInputSchema(schema)).toBe(true)
    expect(collectDynamicInput(schema, {}, {})).toEqual({ ok: true, input: {} })
  })

  it('applies string pattern and date checks before submitting', () => {
    const schema = {
      type: 'object',
      required: ['code', 'due_date'],
      properties: {
        code: { type: 'string', title: '编号', pattern: '^C-\\d{3}$' },
        due_date: { type: 'string', title: '截止日期', format: 'date' },
      },
    }

    expect(collectDynamicInput(schema, { code: 'X-001', due_date: '2026-02-30' }, {}))
      .toMatchObject({ ok: false, error: '编号不符合输入要求' })
    expect(collectDynamicInput(schema, { code: 'C-001', due_date: '2026-07-20' }, {}))
      .toEqual({ ok: true, input: { code: 'C-001', due_date: '2026-07-20' } })
  })

  it('turns a browser-incompatible pattern into a controlled validation error', () => {
    const schema = {
      type: 'object',
      required: ['code'],
      properties: {
        code: { type: 'string', title: '编号', pattern: '(?P<name>a)' },
      },
    }

    expect(() => collectDynamicInput(schema, { code: 'a' }, {})).not.toThrow()
    expect(collectDynamicInput(schema, { code: 'a' }, {}))
      .toMatchObject({ ok: false, error: '编号不符合输入要求' })
  })

  it('keeps number and integer constraints aligned with the backend JSON Schema', () => {
    const schema = {
      type: 'object',
      required: ['budget', 'level'],
      properties: {
        budget: { type: 'number', title: '预算', minimum: 1, maximum: 10, multipleOf: 0.5 },
        level: { type: 'integer', title: '等级', exclusiveMinimum: 1, exclusiveMaximum: 5 },
      },
    }

    expect(collectDynamicInput(schema, { budget: '1.25', level: '3' }, {}))
      .toMatchObject({ ok: false, error: '预算不符合输入要求' })
    expect(collectDynamicInput(schema, { budget: '1.5', level: '5' }, {}))
      .toMatchObject({ ok: false, error: '等级不符合输入要求' })
    expect(collectDynamicInput(schema, { budget: '1.5', level: '3' }, {}))
      .toEqual({ ok: true, input: { budget: 1.5, level: 3 } })
  })

  it('validates array item type, length and uniqueness', () => {
    const schema = {
      type: 'object',
      required: ['scores'],
      properties: {
        scores: {
          type: 'array',
          title: '评分',
          items: { type: 'number' },
          minItems: 2,
          maxItems: 3,
          uniqueItems: true,
        },
      },
    }

    expect(collectDynamicInput(schema, { scores: '0.8' }, {}))
      .toMatchObject({ ok: false, error: '评分不符合输入要求' })
    expect(collectDynamicInput(schema, { scores: '0.8\n0.8' }, {}))
      .toMatchObject({ ok: false, error: '评分不符合输入要求' })
    expect(collectDynamicInput(schema, { scores: '0.8\n0.9' }, {}))
      .toEqual({ ok: true, input: { scores: [0.8, 0.9] } })
  })
})
