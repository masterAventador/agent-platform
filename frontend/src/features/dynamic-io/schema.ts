import { z } from 'zod'


export interface JsonObjectSchema {
  type?: string | string[]
  title?: string
  description?: string
  format?: string
  minLength?: number
  maxLength?: number
  pattern?: string
  minimum?: number
  maximum?: number
  exclusiveMinimum?: number
  exclusiveMaximum?: number
  multipleOf?: number
  enum?: unknown[]
  required?: string[]
  properties?: Record<string, JsonObjectSchema>
  items?: JsonObjectSchema
  minItems?: number
  maxItems?: number
  uniqueItems?: boolean
  additionalProperties?: boolean
  contentMediaType?: string
  'x-agent-platform-control'?: string
  'x-agent-platform-view'?: string
}

export interface DynamicField {
  name: string
  label: string
  schema: JsonObjectSchema
  required: boolean
}

export interface DynamicInputResult {
  ok: boolean
  input?: Record<string, unknown>
  error?: string
}

const schemaMetadataKeys = new Set(['$schema', 'title', 'description'])

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function asJsonObjectSchema(value: unknown): JsonObjectSchema | null {
  if (!isRecord(value)) return null
  return value as JsonObjectSchema
}

export function hasEffectiveOutputSchema(schema: unknown): boolean {
  const outputSchema = asJsonObjectSchema(schema)
  if (!outputSchema) return false
  if (Object.keys(outputSchema).length === 0) return false
  const effectiveKeys = Object.keys(outputSchema).filter((key) => !schemaMetadataKeys.has(key))
  if (effectiveKeys.length === 0) return false
  return !(effectiveKeys.length === 1 && effectiveKeys[0] === 'type' && outputSchema.type === 'object')
}

export function hasDynamicInputSchema(schema: unknown): boolean {
  const inputSchema = asJsonObjectSchema(schema)
  if (!inputSchema) return false
  return isRecord(inputSchema.properties) || inputSchema.additionalProperties === false
}

export function dynamicFields(schema: unknown): DynamicField[] {
  const objectSchema = asJsonObjectSchema(schema)
  if (!objectSchema || !isRecord(objectSchema.properties)) return []
  const required = new Set(
    Array.isArray(objectSchema.required)
      ? objectSchema.required.filter((item): item is string => typeof item === 'string')
      : [],
  )
  return Object.entries(objectSchema.properties).flatMap(([name, fieldSchema]) => {
    const field = asJsonObjectSchema(fieldSchema)
    if (!field) return []
    return [{
      name,
      label: typeof field.title === 'string' && field.title.trim() ? field.title : name,
      schema: field,
      required: required.has(name),
    }]
  })
}

export function isFileField(schema: JsonObjectSchema): boolean {
  return schema['x-agent-platform-control'] === 'file'
    || schema.format === 'binary'
    || typeof schema.contentMediaType === 'string'
}

export function collectDynamicInput(
  schema: unknown,
  values: Record<string, unknown>,
  fileValues: Record<string, string>,
): DynamicInputResult {
  const fields = dynamicFields(schema)
  const input: Record<string, unknown> = {}
  for (const field of fields) {
    const rawValue = values[field.name]
    if (isFileField(field.schema)) {
      if (fileValues[field.name]) {
        input[field.name] = fileValues[field.name]
      }
      continue
    }
    const normalized = normalizeFieldValue(field.schema, rawValue)
    if (field.required && normalized === undefined && field.schema.type === 'boolean') {
      input[field.name] = false
    } else if (normalized !== undefined || field.required) {
      input[field.name] = normalized
    }
  }

  const validator = z.object(Object.fromEntries(
    fields.map((field) => [field.name, fieldValidator(field)]),
  )).strict()
  const parsed = validator.safeParse(input)
  if (!parsed.success) {
    const firstIssue = parsed.error.issues[0]
    const firstPath = typeof firstIssue?.path[0] === 'string' ? firstIssue.path[0] : undefined
    const label = fields.find((field) => field.name === firstPath)?.label ?? '任务输入'
    return { ok: false, error: `${label}不符合输入要求` }
  }
  return { ok: true, input: parsed.data }
}

function normalizeFieldValue(schema: JsonObjectSchema, rawValue: unknown): unknown {
  if (schema.type === 'boolean') {
    if (rawValue === '' || rawValue === undefined || rawValue === null) return undefined
    return Boolean(rawValue)
  }
  if (schema.type === 'number' || schema.type === 'integer') {
    if (rawValue === '' || rawValue === undefined || rawValue === null) return undefined
    const value = Number(rawValue)
    return Number.isFinite(value) ? value : rawValue
  }
  if (schema.type === 'array') {
    if (typeof rawValue !== 'string' || rawValue.trim() === '') return undefined
    return rawValue
      .split(/[\n,]/)
      .map((item) => item.trim())
      .filter(Boolean)
      .map((item) => normalizeArrayItem(schema.items, item))
  }
  if (typeof rawValue === 'string') {
    return rawValue.trim() === '' ? undefined : rawValue
  }
  return rawValue
}

function normalizeArrayItem(schema: JsonObjectSchema | undefined, rawValue: string): unknown {
  if (schema?.type === 'number' || schema?.type === 'integer') {
    const value = Number(rawValue)
    return Number.isFinite(value) ? value : rawValue
  }
  if (schema?.type === 'boolean') {
    if (rawValue === 'true') return true
    if (rawValue === 'false') return false
  }
  return rawValue
}

function fieldValidator(field: DynamicField): z.ZodType {
  if (isFileField(field.schema)) {
    const validator = z.string().min(1)
    return field.required ? validator : validator.optional()
  }
  const validator = schemaValidator(field.schema)
  return field.required ? validator : validator.optional()
}

function schemaValidator(schema: JsonObjectSchema | undefined): z.ZodType {
  if (!schema) return z.string()
  let validator: z.ZodType
  switch (schema.type) {
    case 'number':
      validator = numberValidator(schema)
      break
    case 'integer':
      validator = numberValidator(schema, true)
      break
    case 'boolean':
      validator = z.boolean()
      break
    case 'array':
      validator = arrayValidator(schema)
      break
    default:
      validator = stringValidator(schema)
      break
  }
  if (Array.isArray(schema.enum) && schema.enum.length > 0) {
    validator = validator.refine((value) => schema.enum?.some((item) => jsonEqual(item, value)) ?? false)
  }
  return validator
}

function stringValidator(schema: JsonObjectSchema): z.ZodType {
  let validator: z.ZodType = z.string()
  if (typeof schema.minLength === 'number') validator = validator.pipe(z.string().min(schema.minLength))
  if (typeof schema.maxLength === 'number') validator = validator.pipe(z.string().max(schema.maxLength))
  if (typeof schema.pattern === 'string') {
    let regex: RegExp
    try {
      regex = new RegExp(schema.pattern)
    } catch {
      validator = validator.refine(() => false)
      return validator
    }
    validator = validator.pipe(z.string().regex(regex))
  }
  if (schema.format === 'date') {
    validator = validator.pipe(z.string().refine(isValidDateString))
  }
  return validator
}

function numberValidator(schema: JsonObjectSchema, integer = false): z.ZodType {
  let validator: z.ZodType = integer ? z.number().int() : z.number()
  if (typeof schema.minimum === 'number') validator = validator.pipe(z.number().min(schema.minimum))
  if (typeof schema.maximum === 'number') validator = validator.pipe(z.number().max(schema.maximum))
  if (typeof schema.exclusiveMinimum === 'number') {
    const minimum = schema.exclusiveMinimum
    validator = validator.refine((value) => typeof value === 'number' && value > minimum)
  }
  if (typeof schema.exclusiveMaximum === 'number') {
    const maximum = schema.exclusiveMaximum
    validator = validator.refine((value) => typeof value === 'number' && value < maximum)
  }
  if (typeof schema.multipleOf === 'number') {
    const factor = schema.multipleOf
    validator = validator.refine((value) => typeof value === 'number' && isMultipleOf(value, factor))
  }
  return validator
}

function arrayValidator(schema: JsonObjectSchema): z.ZodType {
  let validator: z.ZodType = z.array(schemaValidator(schema.items))
  if (typeof schema.minItems === 'number') validator = validator.pipe(z.array(schemaValidator(schema.items)).min(schema.minItems))
  if (typeof schema.maxItems === 'number') validator = validator.pipe(z.array(schemaValidator(schema.items)).max(schema.maxItems))
  if (schema.uniqueItems === true) {
    validator = validator.refine(
      (items) => Array.isArray(items) && new Set(items.map(stableJsonKey)).size === items.length,
    )
  }
  return validator
}

function isValidDateString(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const date = new Date(`${value}T00:00:00.000Z`)
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value
}

function isMultipleOf(value: number, factor: number): boolean {
  if (factor === 0) return false
  const quotient = value / factor
  return Math.abs(quotient - Math.round(quotient)) < Number.EPSILON * 100
}

function jsonEqual(left: unknown, right: unknown): boolean {
  return stableJsonKey(left) === stableJsonKey(right)
}

function stableJsonKey(value: unknown): string {
  return JSON.stringify(value) ?? String(value)
}
