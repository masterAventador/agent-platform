import axios from 'axios'

import { getApiErrorMessage } from '../../auth/api/errors'


interface EmployeeApiErrorBody {
  detail?: {
    code?: string
  }
}

export const EMPLOYEE_CONFIGURATION_UNAVAILABLE_MESSAGE =
  '当前员工配置包含尚未开放的工作模式或能力，请切换为自主执行并关闭未接通能力后重试'

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
  if (
    axios.isAxiosError<EmployeeApiErrorBody>(error)
    && error.response?.data.detail?.code === 'employee_configuration_unavailable'
  ) {
    return EMPLOYEE_CONFIGURATION_UNAVAILABLE_MESSAGE
  }
  return getApiErrorMessage(error, fallback)
}
