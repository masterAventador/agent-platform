import { Result } from 'antd'


function responseStatus(error: unknown): number | undefined {
  if (typeof error !== 'object' || error === null || !('response' in error)) return undefined
  const response = error.response
  if (typeof response !== 'object' || response === null || !('status' in response)) return undefined
  return typeof response.status === 'number' ? response.status : undefined
}

export function ResourceAccessError({
  error,
  resourceName,
}: {
  error: unknown
  resourceName: string
}) {
  const status = responseStatus(error)
  if (status === 403) {
    return (
      <Result
        status="403"
        title={`无权访问${resourceName}`}
        subTitle="当前工作区没有查看此内容的权限。"
      />
    )
  }
  if (status === 404) {
    return (
      <Result
        status="404"
        title={`${resourceName}不存在或无权访问`}
        subTitle="该内容可能已被删除，或不属于当前工作区。"
      />
    )
  }
  return (
    <Result
      status="error"
      title={`${resourceName}加载失败`}
      subTitle="暂时无法加载此内容，请稍后重试。"
    />
  )
}
