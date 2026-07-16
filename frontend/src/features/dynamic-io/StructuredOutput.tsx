import { Button, Card, Descriptions, Space, Table, Typography } from 'antd'

import { getPlatformAdapter } from '../../platform'
import { asJsonObjectSchema, hasEffectiveOutputSchema } from './schema'


interface StructuredOutputProps {
  runId: string
  outputSchema: unknown
  output: unknown
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function firstArray(value: unknown): unknown[] | null {
  if (!isRecord(value)) return null
  for (const item of Object.values(value)) {
    if (Array.isArray(item)) return item
  }
  return null
}

function scalarText(value: unknown): string {
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return JSON.stringify(value)
}

function scalarEntries(value: unknown): [string, unknown][] {
  if (!isRecord(value)) return []
  return Object.entries(value).filter(([, item]) => !Array.isArray(item))
}

function ScalarSummary({ output }: { output: unknown }) {
  const entries = scalarEntries(output)
  if (!entries.length) return null
  return (
    <Descriptions column={1} size="small" className="run-structured-summary">
      {entries.map(([key, value]) => (
        <Descriptions.Item key={key} label={key}>
          {scalarText(value)}
        </Descriptions.Item>
      ))}
    </Descriptions>
  )
}

export function StructuredOutput({ runId, outputSchema, output }: StructuredOutputProps) {
  const schema = asJsonObjectSchema(outputSchema)
  if (!schema || !hasEffectiveOutputSchema(schema) || output === undefined || output === null) {
    return null
  }

  const json = JSON.stringify(output, null, 2)
  return (
    <Card
      className="run-structured-output"
      title="结构化结果"
      extra={(
        <Button
          onClick={async () => {
            await getPlatformAdapter().saveFile({
              suggestedName: `${runId}-output.json`,
              bytes: new TextEncoder().encode(json),
            })
          }}
        >
          导出 JSON
        </Button>
      )}
    >
      {schema['x-agent-platform-view'] === 'cards' ? (
        <CardOutput output={output} />
      ) : schema['x-agent-platform-view'] === 'table' ? (
        <TableOutput output={output} />
      ) : (
        <Typography.Paragraph className="run-structured-json">
          <pre>{json}</pre>
        </Typography.Paragraph>
      )}
    </Card>
  )
}

function CardOutput({ output }: { output: unknown }) {
  const rows = firstArray(output)
  if (!rows?.length) {
    return (
      <Typography.Paragraph className="run-structured-json">
        <pre>{JSON.stringify(output, null, 2)}</pre>
      </Typography.Paragraph>
    )
  }
  return (
    <Space direction="vertical" size="middle" className="run-structured-cards">
      <ScalarSummary output={output} />
      {rows.map((item, index) => {
        const record = isRecord(item) ? item : { value: item }
        const title = typeof record.title === 'string'
          ? record.title
          : typeof record.name === 'string'
            ? record.name
            : `结果 ${index + 1}`
        return (
          <Card key={index} size="small" title={title}>
            <Descriptions column={1} size="small">
              {Object.entries(record).filter(([key]) => key !== 'title').map(([key, value]) => (
                <Descriptions.Item key={key} label={key}>
                  {scalarText(value)}
                </Descriptions.Item>
              ))}
            </Descriptions>
          </Card>
        )
      })}
    </Space>
  )
}

function TableOutput({ output }: { output: unknown }) {
  const rows = firstArray(output)
  if (!rows?.length) {
    return (
      <Typography.Paragraph className="run-structured-json">
        <pre>{JSON.stringify(output, null, 2)}</pre>
      </Typography.Paragraph>
    )
  }
  const records = rows.map((item) => (isRecord(item) ? item : { value: item }))
  const keys = Array.from(new Set(records.flatMap((record) => Object.keys(record))))
  return (
    <Space direction="vertical" size="middle" className="run-structured-table-view">
      <ScalarSummary output={output} />
      <Table
        dataSource={records.map((record, index) => ({ key: index, ...record }))}
        pagination={false}
        columns={keys.map((key) => ({
          key,
          dataIndex: key,
          title: key,
          render: (value: unknown) => scalarText(value),
        }))}
      />
    </Space>
  )
}
