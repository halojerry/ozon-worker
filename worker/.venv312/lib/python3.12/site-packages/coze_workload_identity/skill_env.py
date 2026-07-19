"""Automatic skill environment variable discovery and injection."""

import inspect
import json
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import requests

from ._debug import coze_debug_print
from .env_keys import COZE_SKILL_ENV_ENDPOINT, DEFAULT_COZE_SKILL_ENV_ENDPOINT

logger = logging.getLogger(__name__)

_load_lock = threading.RLock()
_load_attempted = False
_load_result = False

COZE_OUTBOUND_AUTH_PROXY = "COZE_OUTBOUND_AUTH_PROXY"
COZE_OUTBOUND_AUTH_PROXY_CA = "COZE_OUTBOUND_AUTH_PROXY_CA"
COZE_OUTBOUND_AUTH_PROXY_CA_PATH = "COZE_OUTBOUND_AUTH_PROXY_CA_PATH"
TICKET_FILE_PATH = Path("/source/ticket.json")


class SkillEnvAPIError(RuntimeError):
    """Raised when get_skill_envs returns a non-zero error code."""

    def __init__(self, payload: dict):
        self.payload = payload
        self.error_json = json.dumps(payload, ensure_ascii=False)
        super().__init__(f"GetSkillEnvs error: {self.error_json}")


@dataclass(frozen=True)
class SkillEnvContext:
    """Local metadata needed to fetch environment variables for a skill."""

    agent_id: str
    pat_token: str
    skill_id: Optional[str]
    identity_ticket: Optional[str] = None


@dataclass(frozen=True)
class AgentPathContext:
    """Agent directory and id discovered from a source path."""

    agent_dir: Path
    agent_id: str


@dataclass(frozen=True)
class SkillEnvVars:
    """Environment variables returned by the skill env endpoint."""

    values: dict


def load_skill_env(
    source_path: Optional[str] = None,
    force: bool = False,
    timeout: Any = 10,
) -> bool:
    """Discover the current Coze skill and inject its environment variables.

    The loader is best-effort: missing local metadata or an unavailable endpoint
    does not fail the caller. It returns True only when at least one environment
    variable is applied.
    """
    global _load_attempted, _load_result

    with _load_lock:
        if _load_attempted and not force:
            return _load_result

    loaded = False
    attempted_this_call = False
    try:
        resolved_source_path = source_path or _caller_source_path()
        if not resolved_source_path:
            logger.debug("Skip skill environment loading: caller source path not found")
            return False

        context = _resolve_skill_context(resolved_source_path)
        if not context:
            logger.debug(
                "Skip skill environment loading: no matching agent config for %s",
                resolved_source_path,
            )
            return False

        with _load_lock:
            if _load_attempted and not force:
                return _load_result
            _load_attempted = True
            attempted_this_call = True

        _set_env("agent_id", context.agent_id)
        _set_env("pat_token", context.pat_token)

        if not context.skill_id:
            os.environ.pop("skill_id", None)
            if context.identity_ticket:
                _set_env("identity_ticket", context.identity_ticket)
            logger.debug(
                "Skip skill environment request: no matching skill for %s",
                resolved_source_path,
            )
            loaded = True
            return True

        _set_env("skill_id", context.skill_id)
        if context.identity_ticket:
            _set_env("identity_ticket", context.identity_ticket)

        env_vars = _fetch_skill_env_map(
            context.agent_id,
            context.pat_token,
            context.skill_id,
            context.identity_ticket,
            timeout,
        )
        if not env_vars or not env_vars.values:
            logger.warning(
                "No skill environment variables loaded for agent_id=%s, skill_id=%s",
                context.agent_id,
                context.skill_id,
            )
            return False

        loaded = _apply_skill_env_values(env_vars.values)
        if context.identity_ticket:
            _set_env("identity_ticket", context.identity_ticket)

        if not loaded:
            logger.info(
                "Skill environment variables already exist for agent_id=%s, skill_id=%s",
                context.agent_id,
                context.skill_id,
            )
        return loaded
    except SkillEnvAPIError:
        raise
    except Exception as exc:
        logger.warning("Failed to load skill environment variables: %s", exc)
        return False
    finally:
        if attempted_this_call:
            with _load_lock:
                _load_result = loaded


def ensure_skill_env_loaded() -> bool:
    """Idempotently load skill environment variables for the current caller."""
    return load_skill_env()


def _caller_source_path() -> Optional[str]:
    package_dir = Path(__file__).resolve().parent
    fallback = None
    for frame_info in inspect.stack()[2:]:
        filename = frame_info.filename
        if not filename:
            continue
        path = Path(filename).resolve()
        try:
            path.relative_to(package_dir)
            continue
        except ValueError:
            path_str = str(path)
            if fallback is None:
                fallback = path_str
            if _agent_dir_from_source_path(path):
                return path_str
    return fallback


def _resolve_skill_context(source_path: str) -> Optional[SkillEnvContext]:
    source = Path(source_path).resolve()
    agent_path = _agent_dir_from_source_path(source)
    if not agent_path:
        return None

    config_path = agent_path.agent_dir / "config.json"
    if not config_path.exists():
        return None

    with config_path.open("r", encoding="utf-8") as f:
        config = json.load(f)

    if not isinstance(config, dict):
        return None

    pat_token = config.get("patToken")
    if not isinstance(pat_token, str) or not pat_token:
        return None

    return SkillEnvContext(
        agent_id=agent_path.agent_id,
        pat_token=pat_token,
        skill_id=_match_skill_id(config.get("skills"), str(source)),
        identity_ticket=_identity_ticket_from_ticket_file(),
    )


def _agent_dir_from_source_path(source: Path) -> Optional[AgentPathContext]:
    parts = source.parts
    for index in range(len(parts) - 2):
        if parts[index] == ".coze" and parts[index + 1] == "agents":
            agent_id = parts[index + 2]
            if agent_id.isdigit():
                return AgentPathContext(
                    agent_dir=Path(*parts[: index + 3]),
                    agent_id=agent_id,
                )
    return None


def _match_skill_id(skills: Any, source_path: str) -> Optional[str]:
    if not isinstance(skills, list):
        return None

    normalized_source = _normalize_path_string(source_path)
    for skill in skills:
        if not isinstance(skill, dict):
            continue

        rel_path = skill.get("relPath")
        if not isinstance(rel_path, str) or not rel_path:
            continue

        if _normalize_path_string(rel_path) not in normalized_source:
            continue

        skill_id = skill.get("skillId") or skill.get("skillID")
        if isinstance(skill_id, str) and skill_id:
            return skill_id

    return None


def _normalize_path_string(path: str) -> str:
    return path.replace("\\", "/").strip("/")


def _fetch_skill_env_map(
    agent_id: str,
    pat_token: str,
    skill_id: str,
    identity_ticket: Optional[str],
    timeout: Any,
) -> Optional[SkillEnvVars]:
    endpoint = os.environ.get(COZE_SKILL_ENV_ENDPOINT) or DEFAULT_COZE_SKILL_ENV_ENDPOINT
    request_body = {
        "agent_id": _request_id(agent_id),
        "skill_id": _request_id(skill_id),
    }
    if identity_ticket:
        request_body["identity_ticket"] = identity_ticket
    request_headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {pat_token}",
    }
    coze_debug_print(
        f"Skill environment request: endpoint={endpoint}, "
        f"json={request_body}, headers={request_headers}"
    )

    session = requests.Session()
    try:
        response = session.post(
            endpoint,
            json=request_body,
            headers=request_headers,
            timeout=timeout,
        )
        if response.status_code != 200:
            logger.warning(
                "Skill environment request failed with status %s",
                response.status_code,
            )
            return None
        payload = response.json()
        coze_debug_print(
            f"Skill environment response: status={response.status_code}, "
            f"body={payload}"
        )
        return _extract_env_map(payload)
    finally:
        session.close()


def _extract_env_map(payload: Any) -> Optional[SkillEnvVars]:
    if not isinstance(payload, dict):
        logger.warning("Invalid skill environment response: expected JSON object")
        return None

    error_code = _payload_error_code(payload)
    if not _is_success_error_code(error_code):
        error = SkillEnvAPIError(payload)
        coze_debug_print(error.error_json)
        raise error

    data = payload.get("data", payload)
    if not isinstance(data, dict):
        logger.warning("Invalid skill environment response: missing or invalid data")
        return None

    env_map = data.get("envs") or data.get("env") or data.get("variables")
    if env_map is None and _is_string_map(data):
        env_map = data

    if isinstance(env_map, dict) and _is_string_map(env_map):
        return SkillEnvVars(values=dict(env_map))

    logger.warning("Invalid skill environment response: missing env map")
    return None


def _request_id(value: str):
    if value.isdigit():
        return int(value)
    return value


def _optional_string(value: Any) -> Optional[str]:
    if isinstance(value, str) and value:
        return value
    return None


def _identity_ticket_from_ticket_file() -> Optional[str]:
    if not TICKET_FILE_PATH.exists():
        return None

    try:
        with TICKET_FILE_PATH.open("r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as exc:
        logger.warning("Failed to read identity ticket file %s: %s", TICKET_FILE_PATH, exc)
        return None

    if not isinstance(payload, dict):
        logger.warning("Invalid identity ticket file %s: expected JSON object", TICKET_FILE_PATH)
        return None

    identity_ticket = _optional_string(payload.get("IDENTITY_TICKET"))
    if not identity_ticket:
        logger.warning(
            "Invalid identity ticket file %s: missing IDENTITY_TICKET",
            TICKET_FILE_PATH,
        )
    return identity_ticket


def _payload_error_code(payload: dict):
    base_resp = payload.get("BaseResp")
    if isinstance(base_resp, dict) and "StatusCode" in base_resp:
        return base_resp.get("StatusCode")
    return 0


def _is_success_error_code(value: Any) -> bool:
    return value == 0 or value == "0"


def _set_env(key: str, value: str):
    env_keys = [key]
    if key == "identity_ticket":
        env_keys.append("IDENTITY_TICKET")
    elif key == "IDENTITY_TICKET":
        env_keys.append("identity_ticket")

    for env_key in env_keys:
        os.environ[env_key] = value


def _apply_skill_env_values(values: dict) -> bool:
    loaded = False
    original_proxy = os.environ.get(COZE_OUTBOUND_AUTH_PROXY)
    original_ca_path = os.environ.get(COZE_OUTBOUND_AUTH_PROXY_CA_PATH)

    for key, value in values.items():
        if key == COZE_OUTBOUND_AUTH_PROXY and original_proxy:
            continue
        if key in (COZE_OUTBOUND_AUTH_PROXY_CA, COZE_OUTBOUND_AUTH_PROXY_CA_PATH):
            continue

        _set_env(key, value)
        loaded = True

    if not original_ca_path:
        if values.get(COZE_OUTBOUND_AUTH_PROXY_CA):
            _set_env(
                COZE_OUTBOUND_AUTH_PROXY_CA,
                values[COZE_OUTBOUND_AUTH_PROXY_CA],
            )
            loaded = True
        elif values.get(COZE_OUTBOUND_AUTH_PROXY_CA_PATH):
            _set_env(
                COZE_OUTBOUND_AUTH_PROXY_CA_PATH,
                values[COZE_OUTBOUND_AUTH_PROXY_CA_PATH],
            )
            loaded = True

    return loaded


def _is_string_map(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return all(isinstance(k, str) and isinstance(v, str) for k, v in value.items())


def _reset_skill_env_for_tests():
    """Reset process-level loader state for tests."""
    global _load_attempted, _load_result
    with _load_lock:
        _load_attempted = False
        _load_result = False
