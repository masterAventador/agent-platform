import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  Flex,
  Modal,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
} from 'antd'
import { useState, type HTMLAttributes } from 'react'
import { Link } from 'react-router-dom'

import { getApiErrorMessage } from '../../auth/api/errors'
import type { RunDeadLetter, ReplayedRun } from '../api/dead-letters'
import { useReplayRunDeadLetter, useRunDeadLetters } from '../api/queries'
import './dead-letters.css'

type TestableRowAttributes = HTMLAttributes<HTMLTableRowElement> & {
  'data-testid': string
}


const formatDateTime = (value: string) => new Date(value).toLocaleString('zh-CN', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
})

const canReplay = (record: RunDeadLetter) => (
  !record.is_malformed
  && record.settled_run_id !== null
  && record.replayed_run_id === null
)

export function DeadLettersPage() {
  const deadLetters = useRunDeadLetters()
  const replay = useReplayRunDeadLetter()
  const [selected, setSelected] = useState<RunDeadLetter | null>(null)
  const [lastReplay, setLastReplay] = useState<ReplayedRun | null>(null)

  const closeConfirmation = () => {
    if (replay.isPending) return
    setSelected(null)
    replay.reset()
  }

  const confirmReplay = async () => {
    if (selected === null || replay.isPending) return
    try {
      const result = await replay.mutateAsync(selected.id)
      setLastReplay(result)
      setSelected(null)
    } catch {
      // Mutation 错误在确认弹窗内统一展示。
    }
  }

  const columns = [
    {
      title: '失败时间',
      dataIndex: 'failed_at',
      key: 'failed_at',
      render: (value: string) => <time dateTime={value}>{formatDateTime(value)}</time>,
    },
    {
      title: '原任务',
      key: 'original_run',
      render: (_: unknown, record: RunDeadLetter) => record.original_run_id === null ? (
        <Typography.Text type="secondary">无法识别</Typography.Text>
      ) : (
        <Space orientation="vertical" size={0}>
          <Typography.Text code>{record.original_run_id.slice(0, 8)}</Typography.Text>
          <Link to={`/runs/${record.original_run_id}`}>查看原任务</Link>
        </Space>
      ),
    },
    {
      title: '错误类型',
      key: 'error_type',
      render: (_: unknown, record: RunDeadLetter) => (
        <Space orientation="vertical" size={2}>
          <Typography.Text>{record.error_type}</Typography.Text>
          {record.is_malformed && <Tag color="warning">格式异常</Tag>}
        </Space>
      ),
    },
    { title: '尝试次数', dataIndex: 'attempts', key: 'attempts' },
    {
      title: '结算状态',
      key: 'settlement',
      render: (_: unknown, record: RunDeadLetter) => (
        <Tag color={record.settled_run_id === null ? 'warning' : 'success'}>
          {record.settled_run_id === null ? '待结算' : '已结算'}
        </Tag>
      ),
    },
    {
      title: '镜像状态',
      key: 'mirror',
      render: (_: unknown, record: RunDeadLetter) => (
        <Tag color={record.mirrored_at === null ? 'default' : 'success'}>
          {record.mirrored_at === null ? '待镜像' : '已镜像'}
        </Tag>
      ),
    },
    {
      title: '重放状态',
      key: 'replay',
      render: (_: unknown, record: RunDeadLetter) => {
        if (record.is_malformed) return <Tag>禁止重放</Tag>
        if (record.replayed_run_id !== null) {
          return <Link to={`/runs/${record.replayed_run_id}`}>查看新任务</Link>
        }
        return <Tag color="default">未重放</Tag>
      },
    },
    {
      title: '操作',
      key: 'action',
      render: (_: unknown, record: RunDeadLetter) => (
        <Button
          disabled={!canReplay(record) || replay.isPending}
          loading={replay.isPending && selected?.id === record.id}
          onClick={() => {
            replay.reset()
            setSelected(record)
          }}
        >
          重放任务
        </Button>
      ),
    },
  ]

  return (
    <section>
      <Typography.Title level={2}>死信管理</Typography.Title>
      <Typography.Paragraph type="secondary">
        查看处理失败的任务投递，并在确认后安全重放已结算记录。
      </Typography.Paragraph>

      {lastReplay !== null && (
        <Alert
          className="dead-letter-alert"
          type="success"
          showIcon
          title="任务重放成功"
          description={<Link to={`/runs/${lastReplay.run_id}`}>查看新任务</Link>}
          closable
          onClose={() => setLastReplay(null)}
        />
      )}
      {deadLetters.isPending ? (
        <Flex className="dead-letter-loading" justify="center">
          <Spin aria-label="正在加载死信任务" />
        </Flex>
      ) : deadLetters.isError ? (
        <Alert
          className="dead-letter-alert"
          type="error"
          showIcon
          title={getApiErrorMessage(deadLetters.error, '死信列表加载失败')}
          action={(
            <Button size="small" onClick={() => void deadLetters.refetch()}>
              重新加载
            </Button>
          )}
        />
      ) : deadLetters.data?.length ? (
        <Card className="dead-letter-card">
          <Table<RunDeadLetter>
            rowKey="id"
            pagination={false}
            scroll={{ x: 1080 }}
            dataSource={deadLetters.data}
            columns={columns}
            onRow={(record): TestableRowAttributes => ({
              'data-testid': `dead-letter-row-${record.id}`,
            })}
          />
        </Card>
      ) : (
        <Card className="dead-letter-card"><Empty description="当前没有死信任务" /></Card>
      )}

      <Modal
        title="确认重放任务"
        open={selected !== null}
        okText="确认重放"
        cancelText="取消"
        confirmLoading={replay.isPending}
        okButtonProps={{ disabled: replay.isPending }}
        mask={{ closable: !replay.isPending }}
        onOk={confirmReplay}
        onCancel={closeConfirmation}
      >
        <Typography.Paragraph>
          系统将基于原任务创建一条新任务，原任务和当前死信记录不会被修改。
        </Typography.Paragraph>
        {selected !== null && (
          <Descriptions column={1} size="small">
            <Descriptions.Item label="死信标识">
              <Typography.Text code>{selected.id.slice(0, 8)}</Typography.Text>
            </Descriptions.Item>
            <Descriptions.Item label="原任务">
              {selected.original_run_id === null ? (
                <Typography.Text type="secondary">无法识别</Typography.Text>
              ) : (
                <Typography.Text code>{selected.original_run_id.slice(0, 8)}</Typography.Text>
              )}
            </Descriptions.Item>
          </Descriptions>
        )}
        {replay.isError && (
          <Alert
            type="error"
            showIcon
            title={getApiErrorMessage(replay.error, '任务重放失败')}
          />
        )}
      </Modal>
    </section>
  )
}
