import { Button, Card, Empty, Flex, Space, Spin, Tag, Typography } from 'antd'
import { useNavigate } from 'react-router-dom'

import { useEmployees } from '../api/queries'
import './employees.css'


const statusLabels = {
  draft: { color: 'default', text: '草稿' },
  published: { color: 'success', text: '已发布' },
} as const

export function EmployeesPage({ canManageWorkspace }: { canManageWorkspace: boolean }) {
  const employees = useEmployees()
  const navigate = useNavigate()

  return (
    <section>
      <Flex align="center" justify="space-between" gap={16}>
        <div>
          <Typography.Title level={2}>数字员工</Typography.Title>
          <Typography.Text type="secondary">
            创建、配置并发布企业可复用的 AI 数字员工
          </Typography.Text>
        </div>
        {canManageWorkspace && (
          <Button type="primary" onClick={() => navigate('/employees/new')}>
            创建数字员工
          </Button>
        )}
      </Flex>

      {employees.isPending ? (
        <Flex className="employee-loading" justify="center"><Spin /></Flex>
      ) : employees.data?.length ? (
        <div className="employee-list employee-list-grid">
          {employees.data.map((employee) => {
            const status = statusLabels[employee.status]
            return (
              <Card
                key={employee.id}
                hoverable
                onClick={() => navigate(`/employees/${employee.id}`)}
                title={employee.name}
                extra={<Tag color={status.color}>{status.text}</Tag>}
              >
                <Space orientation="vertical" size={12}>
                  <Typography.Paragraph ellipsis={{ rows: 2 }}>
                    {employee.definition.role_description}
                  </Typography.Paragraph>
                  <Typography.Text type="secondary">
                    {employee.published_version
                      ? `已发布版本 ${employee.published_version}`
                      : '尚未发布'}
                  </Typography.Text>
                </Space>
              </Card>
            )
          })}
        </div>
      ) : (
        <Card className="employee-empty">
          <Empty description="还没有数字员工" />
        </Card>
      )}
    </section>
  )
}
