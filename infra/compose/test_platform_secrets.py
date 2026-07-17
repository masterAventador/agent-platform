"""模型网关密钥的最小权限门禁（C16）。

派生密钥能签发**任意租户**的虚拟 Key，master key 能管理整个网关。二者都必须只发给真正
需要它们的进程；放进共享 backend environment 锚点会被所有服务继承，把凭据面无声扩大。
本门禁直接读 `docker compose config` 的最终结果，因此 YAML 锚点的任何回退都会被抓到。
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "infra/compose/platform.yml"

KEY_SECRET = "AGENT_PLATFORM_MODEL_GATEWAY_KEY_SECRET"
ADMIN_MASTER_KEY = "AGENT_PLATFORM_MODEL_GATEWAY_ADMIN_MASTER_KEY"
# Controller 签发/轮换/撤销租户 Key；Worker 派生本租户 Key 发起推理。其余进程都不需要。
KEY_SECRET_SERVICES = {"worker", "model-gateway-controller"}
# 只有 Controller 通过公开管理 API 对账；API 与 Worker 都不得持有 master key。
ADMIN_MASTER_KEY_SERVICES = {"model-gateway-controller"}


def _resolved_config() -> dict[str, Any]:
    env = dict(
        os.environ,
        POSTGRES_PASSWORD="test-only",
        REDIS_PASSWORD="test-only",
        MINIO_ROOT_PASSWORD="test-only",
        SANDBOX_CONTROLLER_BEARER_SECRET="test-only-secret",
        LITELLM_MASTER_KEY="sk-" + "t" * 32,
        AGENT_PLATFORM_LLM_GATEWAY_API_KEY="sk-" + "w" * 32,
    )
    completed = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "--profile",
            "worker",
            "config",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    config: dict[str, Any] = json.loads(completed.stdout)
    return config


def _services_with(config: dict[str, Any], variable: str) -> set[str]:
    return {
        name
        for name, service in config.get("services", {}).items()
        if variable in (service.get("environment") or {})
    }


class ModelGatewaySecretScopeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.config = _resolved_config()
        except (subprocess.CalledProcessError, FileNotFoundError) as error:
            raise unittest.SkipTest(f"docker compose config unavailable: {error}") from None

    def test_key_derivation_secret_is_limited_to_the_processes_that_need_it(self) -> None:
        self.assertEqual(_services_with(self.config, KEY_SECRET), KEY_SECRET_SERVICES)

    def test_admin_master_key_is_limited_to_the_controller(self) -> None:
        self.assertEqual(
            _services_with(self.config, ADMIN_MASTER_KEY), ADMIN_MASTER_KEY_SERVICES
        )

    def test_the_api_never_receives_any_model_gateway_secret(self) -> None:
        api_environment = self.config["services"]["api"].get("environment") or {}
        self.assertNotIn(KEY_SECRET, api_environment)
        self.assertNotIn(ADMIN_MASTER_KEY, api_environment)


if __name__ == "__main__":
    unittest.main()
