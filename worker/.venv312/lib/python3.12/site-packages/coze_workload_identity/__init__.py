"""
Coze Workload Identity SDK for Python

A Python SDK for Coze workload identity authentication using OAuth2.0 token exchange.
"""

from .client import Client
from .exceptions import WorkloadIdentityError, TokenExchangeError, ConfigurationError, TokenRetrievalError
from . import env_keys
from .models import ProjectEnvVar, ProjectEnvVars
from . import requests
from .proxy import HttpsProxy, CaBundlePath
from .skill_env import SkillEnvAPIError, load_skill_env, ensure_skill_env_loaded

__version__ = "0.1.14"
__all__ = [
    "Client",
    "WorkloadIdentityError",
    "TokenExchangeError",
    "ConfigurationError",
    "TokenRetrievalError",
    "env_keys",
    "ProjectEnvVar",
    "ProjectEnvVars",
    "requests",
    "HttpsProxy",
    "CaBundlePath",
    "SkillEnvAPIError",
    "load_skill_env",
    "ensure_skill_env_loaded",
]
