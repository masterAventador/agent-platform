import axios from 'axios'

import { getApiErrorMessage } from '../../auth/api/errors'


interface EmployeeApiErrorBody {
  detail?: {
    code?: string
  } | Array<{
    loc?: Array<string | number>
  }>
}

export const EMPLOYEE_CONFIGURATION_UNAVAILABLE_MESSAGE =
  '当前员工配置包含尚未开放的工作模式或能力，请切换为自主执行并关闭未接通能力后重试'
export const EMPLOYEE_MODEL_ALIAS_UNAVAILABLE_MESSAGE =
  '该平台模型未启用，请联系管理员或选择可用模型后重试'
export const EMPLOYEE_MODEL_ALIAS_INVALID_MESSAGE =
  '模型别名无效，请使用小写字母或数字开头，且只包含小写字母、数字、点、下划线或连字符（最长 64 个字符）'
export const EMPLOYEE_VALIDATION_FAILED_MESSAGE =
  '员工配置格式无效，请检查表单内容后重试'

export class EmployeeConfigurationUnavailableError extends Error {
  constructor() {
    super('employee_configuration_unavailable')
    this.name = 'EmployeeConfigurationUnavailableError'
  }
}

export function getEmployeeApiErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof EmployeeConfigurationUnavailableError) {
    return EMPLOYEE_CONFIGURATION_UNAVAILABLE_MESSAGE
  }
  if (axios.isAxiosError<EmployeeApiErrorBody>(error)) {
    const detail = error.response?.data.detail
    if (!Array.isArray(detail) && detail?.code === 'employee_configuration_unavailable') {
      return EMPLOYEE_CONFIGURATION_UNAVAILABLE_MESSAGE
    }
    if (!Array.isArray(detail) && detail?.code === 'employee_model_alias_unavailable') {
      return EMPLOYEE_MODEL_ALIAS_UNAVAILABLE_MESSAGE
    }
    if (error.response?.status === 422) {
      const modelAliasInvalid = Array.isArray(detail) && detail.some((issue) => (
        issue.loc?.includes('model') && issue.loc.includes('alias')
      ))
      return modelAliasInvalid
        ? EMPLOYEE_MODEL_ALIAS_INVALID_MESSAGE
        : EMPLOYEE_VALIDATION_FAILED_MESSAGE
    }
  }
  return getApiErrorMessage(error, fallback)
}
