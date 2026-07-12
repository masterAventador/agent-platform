import { Layout, Space, Typography } from 'antd'
import { Link } from 'react-router-dom'

import { BackendStatus } from '../features/system/components/BackendStatus'
import './app.css'

const { Content, Sider } = Layout

export function App() {
  return (
    <Layout className="app-shell">
      <Sider className="app-sidebar" width={240}>
        <Typography.Title className="app-title" level={1}>
          AI 数字员工平台
        </Typography.Title>
        <nav aria-label="主导航">
          <Space className="app-navigation" orientation="vertical" size="middle">
            <Link to="/">工作台</Link>
            <Link to="/employees">数字员工</Link>
            <Link to="/runs">任务中心</Link>
          </Space>
        </nav>
      </Sider>
      <Content className="app-content">
        <Typography.Title level={2}>工作台</Typography.Title>
        <Typography.Paragraph type="secondary">
          平台基础工程已就绪，后续功能将按前后端纵向切片逐步接入。
        </Typography.Paragraph>
        <BackendStatus />
      </Content>
    </Layout>
  )
}
