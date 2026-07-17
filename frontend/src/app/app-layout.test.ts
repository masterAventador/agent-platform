/// <reference types="node" />

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const appStyles = readFileSync(resolve(process.cwd(), 'src/app/app.css'), 'utf8')
const globalStyles = readFileSync(resolve(process.cwd(), 'src/index.css'), 'utf8')

function declarationsFor(styles: string, selector: string): string {
  const ruleStart = styles.indexOf(`${selector} {`)

  expect(ruleStart, `missing CSS rule for ${selector}`).toBeGreaterThanOrEqual(0)
  const declarationsStart = styles.indexOf('{', ruleStart) + 1
  const declarationsEnd = styles.indexOf('}', declarationsStart)
  return styles.slice(declarationsStart, declarationsEnd)
}

function valueFor(styles: string, selector: string, property: string): string | undefined {
  const declaration = declarationsFor(styles, selector)
    .split(';')
    .map((line) => line.trim())
    .find((line) => line.startsWith(`${property}:`))

  return declaration?.slice(property.length + 1).trim()
}

describe('application shell layout', () => {
  it('keeps the navigation and content in independent viewport scroll regions', () => {
    expect(valueFor(appStyles, '.app-shell', 'height')).toBe('100vh')
    expect(valueFor(appStyles, '.app-shell', 'overflow')).toBe('hidden')
    expect(valueFor(appStyles, '.app-sidebar', 'overflow-y')).toBe('auto')
    expect(valueFor(appStyles, '.app-content', 'overflow-y')).toBe('auto')
  })

  it('contains overscroll inside each region instead of bouncing the whole viewport', () => {
    expect(valueFor(globalStyles, 'html', 'overscroll-behavior')).toBe('none')
    expect(valueFor(globalStyles, 'body', 'overscroll-behavior')).toBe('none')
    expect(valueFor(appStyles, '.app-shell', 'overscroll-behavior')).toBe('none')
    expect(valueFor(appStyles, '.app-sidebar', 'overscroll-behavior-y')).toBe('contain')
    expect(valueFor(appStyles, '.app-content', 'overscroll-behavior-y')).toBe('contain')
  })
})
