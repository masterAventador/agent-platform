from __future__ import annotations

import docker

from agent_platform.sandbox.controller.api import create_controller_app
from agent_platform.sandbox.controller.config import ControllerSettings

settings = ControllerSettings.from_env()
app = create_controller_app(settings=settings, docker_client=docker.from_env())
