import { Button, Card, Descriptions, Flex, Space, Spin, Tag, Typography } from 'antd'
import { useNavigate, useParams } from 'react-router-dom'

import { useEmployee, usePublishEmployee } from '../api/queries'
import './employees.css'


const modeLabels = {
  autonomous: '自主执行',
  workflow: '固定流程',
  hybrid: '混合协作',
} as const

export function EmployeeDetailPage() {
  const { employeeId } = useParams()
  const employee = useEmployee(employeeId)
  const publish = usePublishEmployee(employeeId ?? '')
  const navigate = useNavigate()

  if (employee.isPending || !employee.data) {
    return <Flex className="employee-loading" justify="center"><Spin /></Flex>
  }

  const data = employee.data
  const published = data.status === 'published'

  return (
    <section>
      <Flex align="center" justify="space-between" gap={16}>
        <div>
          <Space align="center">
            <Typography.Title level={2}>{data.name}</Typography.Title>
            <Tag color={published ? 'success' : 'default'}>{published ? '已发布' : '草稿'}</Tag>
          </Space>
          <Typography.Text type="secondary">{data.definition.role_description}</Typography.Text>
        </div>
        <Space>
          <Button onClick={() => navigate(`/employees/${data.id}/edit`)}>编辑</Button>
          <Button type="primary" loading={publish.isPending} onClick={() => publish.mutate()}>
            发布员工
          </Button>
        </Space>
      </Flex>

      <Card className="employee-detail-card" title="员工配置">
        <Descriptions column={2}>
          <Descriptions.Item label="工作模式">
            {modeLabels[data.definition.work_mode]}
          </Descriptions.Item>
          <Descriptions.Item label="发布版本">
            {data.published_version ? `版本 ${data.published_version}` : '尚未发布'}
          </Descriptions.Item>
          <Descriptions.Item label="模型">
            {data.definition.model.provider} / {data.definition.model.name}
          </Descriptions.Item>
          <Descriptions.Item label="可见范围">企业成员</Descriptions.Item>
        </Descriptions>
        <Typography.Title level={4}>系统指令</Typography.Title>
        <Typography.Paragraph className="employee-prompt">
          {data.definition.system_prompt}
        </Typography.Paragraph>
      </Card>
    </section>
  )
}
