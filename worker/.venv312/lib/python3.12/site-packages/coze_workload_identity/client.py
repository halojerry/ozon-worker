"""Client implementation for workload identity authentication."""

import os
import time
import threading
import logging
from typing import Optional, Dict, Any, Union
from datetime import datetime, timedelta

import requests

from .exceptions import ConfigurationError, TokenRetrievalError, TokenExchangeError
from .env_keys import *
from .models import ProjectEnvVars, ProjectEnvVar
from .skill_env import ensure_skill_env_loaded

logger = logging.getLogger(__name__)


class TokenCache:
    """Thread-safe token cache with expiration support."""

    def __init__(self):
        self._cache: Dict[str, Any] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> Optional[str]:
        """Get token from cache if it exists and is not expired."""
        with self._lock:
            if key not in self._cache:
                return None

            token_info = self._cache[key]
            expires_at = token_info.get('expires_at')

            if expires_at and time.time() < expires_at:
                return token_info.get('token')
            else:
                # Token expired, remove from cache
                del self._cache[key]
                return None

    def set(self, key: str, token: str, expires_in: int):
        """Set token in cache with expiration time."""
        with self._lock:
            expires_at = time.time() + expires_in - 60  # 1 minute buffer
            self._cache[key] = {
                'token': token,
                'expires_at': expires_at
            }


# Global process-level token cache instance
# This will be shared across all Client instances in the same process
_global_token_cache: Optional[TokenCache] = None
_global_cache_lock = threading.Lock()


def get_global_token_cache() -> TokenCache:
    """Get the global process-level token cache instance.

    This ensures all Client instances in the same process share the same cache,
    improving performance by avoiding redundant token requests.

    Returns:
        TokenCache: The global shared token cache instance
    """
    global _global_token_cache

    if _global_token_cache is None:
        with _global_cache_lock:
            if _global_token_cache is None:
                _global_token_cache = TokenCache()
                logger.debug("Initialized global process-level token cache")

    return _global_token_cache


class Client:
    """Workload Identity SDK Client."""

    def __init__(self, timeout: Union[float, tuple] = (5, 30)):
        """Initialize the client with configuration from environment variables.

        Args:
            timeout: HTTP request timeout in seconds.
                     - int/float: applies to both connect and read (e.g. timeout=10)
                     - tuple: (connect_timeout, read_timeout) (e.g. timeout=(5, 30))
                     Default is (5, 30): 5s to establish connection, 30s to read response.
        """
        ensure_skill_env_loaded()
        self._load_configuration()
        self._token_cache = get_global_token_cache()
        self._session = requests.Session()
        self._timeout = timeout

        logger.debug(f"Workload Identity Client initialized with timeout={timeout}s")

    def _load_configuration(self):
        """Load required configuration from environment variables."""
        required_vars = {
            COZE_WORKLOAD_IDENTITY_CLIENT_ID: 'client_id',
            COZE_WORKLOAD_IDENTITY_CLIENT_SECRET: 'client_secret',
            COZE_WORKLOAD_IDENTITY_TOKEN_ENDPOINT: 'token_endpoint',
            COZE_WORKLOAD_ACCESS_TOKEN_ENDPOINT: 'access_token_endpoint'
        }

        missing_vars = []
        for env_var, attr_name in required_vars.items():
            value = os.environ.get(env_var)
            if not value:
                missing_vars.append(env_var)
            else:
                setattr(self, f"_{attr_name}", value)

        if missing_vars:
            raise ConfigurationError(
                f"Missing required environment variables: {', '.join(missing_vars)}"
            )

        # Load lane configuration
        self._lane_env = os.environ.get(COZE_SERVER_ENV, DEFAULT_COZE_SERVER_ENV)
        logger.debug(f"Lane environment: {self._lane_env}")

        # Load integration credential endpoint
        self._outbound_auth_endpoint = os.environ.get(COZE_OUTBOUND_AUTH_ENDPOINT)
        logger.debug(f"Outbound auth endpoint: {self._outbound_auth_endpoint}")

    def _make_token_request(self, endpoint: str, data: Dict[str, str]) -> Dict[str, Any]:
        """Make a token request to the specified endpoint."""
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'User-Agent': 'coze-workload-identity/0.1.14'
        }

        # Add lane headers if COZE_SERVER_ENV is set and not NONE
        if self._lane_env != DEFAULT_COZE_SERVER_ENV:
            headers['x-tt-env'] = self._lane_env
            if self._lane_env.startswith('ppe_'):
                headers['x-use-ppe'] = '1'
            elif self._lane_env.startswith('boe_'):
                # boe_ lanes don't need x-use-ppe header
                pass

        try:
            response = self._session.post(endpoint, data=data, headers=headers, timeout=self._timeout)
            response.raise_for_status()

            result = response.json()
            if 'error' in result:
                raise TokenRetrievalError(f"Token request failed: {result.get('error_description', result['error'])}")

            return result

        except requests.exceptions.RequestException as e:
            logger.error(f"Token request failed: {e}")
            raise TokenRetrievalError(f"Failed to retrieve token: {e}")
        except ValueError as e:
            logger.error(f"Failed to parse token response: {e}")
            raise TokenRetrievalError(f"Invalid token response format: {e}")

    def _get_id_token(self) -> str:
        """Get ID token using client credentials (no caching)."""
        logger.debug("Requesting new ID token")

        data = {
            'client_id': self._client_id,
            'client_secret': self._client_secret,
            'grant_type': 'client_credentials'
        }

        result = self._make_token_request(self._token_endpoint, data)

        id_token = result.get('access_token')
        if not id_token:
            raise TokenRetrievalError("No access_token found in ID token response")

        expires_in = result.get('expires_in', 3600)  # Default to 1 hour
        logger.debug(f"ID token retrieved, expires in {expires_in} seconds")
        return id_token

    def get_access_token(self) -> str:
        """
        Get access token using OAuth2.0 token exchange.

        Returns:
            str: The access token

        Raises:
            ConfigurationError: If required configuration is missing
            TokenRetrievalError: If token retrieval fails
            TokenExchangeError: If token exchange fails
        """
        cache_key = "access_token"
        cached_token = self._token_cache.get(cache_key)

        if cached_token:
            logger.debug("Using cached access token")
            return cached_token

        logger.debug("Requesting new access token")

        # Step 1: Get ID token
        id_token = self._get_id_token()

        # Step 2: Exchange ID token for access token
        data = {
            'client_id': self._client_id,
            'subject_token': id_token,
            'subject_token_type': 'urn:ietf:params:oauth:token-type:id_token',
            'grant_type': 'urn:ietf:params:oauth:grant-type:token-exchange'
        }

        result = self._make_token_request(self._access_token_endpoint, data)

        access_token = result.get('access_token')
        if not access_token:
            raise TokenExchangeError("No access_token found in access token response")

        expires_in = result.get('expires_in', 3600)  # Default to 1 hour
        self._token_cache.set(cache_key, access_token, expires_in)

        logger.debug(f"Access token retrieved, expires in {expires_in} seconds")
        return access_token

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()

    def get_integration_credential(self, integration_name: str) -> str:
        """
        Get integration credential for the specified integration.

        Args:
            integration_name: Name of the integration

        Returns:
            str: The integration credential

        Raises:
            ConfigurationError: If COZE_OUTBOUND_AUTH_ENDPOINT is not configured
            TokenRetrievalError: If access token retrieval fails
            Exception: If integration credential API returns non-200 status or invalid response
        """
        if not self._outbound_auth_endpoint:
            raise ConfigurationError(
                f"{COZE_OUTBOUND_AUTH_ENDPOINT} environment variable is required for integration credentials"
            )

        # Step 1: Get access token
        access_token = self.get_access_token()

        # Step 2: Prepare request for integration credential
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {access_token}'
        }

        # Add lane headers if configured
        if self._lane_env != DEFAULT_COZE_SERVER_ENV:
            headers['x-tt-env'] = self._lane_env
            if self._lane_env.startswith('ppe_'):
                headers['x-use-ppe'] = '1'

        data = {
            "integration_name": integration_name
        }

        url = f"{self._outbound_auth_endpoint}/integration"

        try:
            response = self._session.post(
                url,
                json=data,
                headers=headers,
                timeout=self._timeout
            )

            # Handle HTTP status codes
            if response.status_code != 200:
                error_msg = f"Integration credential request failed with status {response.status_code}"
                try:
                    error_data = response.json()
                    if isinstance(error_data, dict) and 'msg' in error_data:
                        error_msg += f": {error_data['msg']}"
                except:
                    pass

                logger.error(f"Integration credential request failed with status {response.status_code}: {error_msg}")

                # Distinguish between 4xx and 5xx errors
                if 400 <= response.status_code < 500:
                    raise Exception(f"Client error ({response.status_code}): {error_msg}")
                else:
                    raise Exception(f"Server error ({response.status_code}): {error_msg}")

            result = response.json()

            # Validate response structure
            if not isinstance(result, dict):
                raise Exception("Invalid response format: expected JSON object")

            if result.get('code') != 0:
                msg = result.get('msg', 'Unknown error')
                raise Exception(f"Integration credential API error: code={result.get('code')}, msg={msg}")

            if 'data' not in result or not isinstance(result['data'], dict):
                raise Exception("Invalid response format: missing or invalid 'data' field")

            if 'credential' not in result['data']:
                raise Exception("Invalid response format: missing 'credential' field in data")

            credential = result['data']['credential']
            if not isinstance(credential, str):
                raise Exception("Invalid response format: credential must be a string")

            logger.debug(f"Integration credential retrieved for {integration_name}")
            return credential

        except requests.exceptions.RequestException as e:
            logger.error(f"Integration credential request failed: {e}")
            raise Exception(f"Failed to retrieve integration credential: {e}")
        except ValueError as e:
            logger.error(f"Failed to parse integration credential response: {e}")
            raise Exception(f"Invalid integration credential response format: {e}")

    def get_project_env_vars(self) -> ProjectEnvVars:
        """
        Get project environment variables.

        Returns:
            ProjectEnvVars: Object containing project environment variables

        Raises:
            ConfigurationError: If COZE_OUTBOUND_AUTH_ENDPOINT is not configured
            TokenRetrievalError: If access token retrieval fails
            Exception: If project environment variables API returns non-200 status or invalid response
        """
        if not self._outbound_auth_endpoint:
            raise ConfigurationError(
                f"{COZE_OUTBOUND_AUTH_ENDPOINT} environment variable is required for project environment variables"
            )

        # Step 1: Get access token
        access_token = self.get_access_token()

        # Step 2: Prepare request for project environment variables
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {access_token}'
        }

        # Add lane headers if configured
        if self._lane_env != DEFAULT_COZE_SERVER_ENV:
            headers['x-tt-env'] = self._lane_env
            if self._lane_env.startswith('ppe_'):
                headers['x-use-ppe'] = '1'

        # Build URL without query parameter
        url = f"{self._outbound_auth_endpoint}/env"

        try:
            response = self._session.get(
                url,
                headers=headers,
                timeout=self._timeout
            )

            # Handle HTTP status codes
            if response.status_code != 200:
                error_msg = f"Project environment variables request failed with status {response.status_code}"
                try:
                    error_data = response.json()
                    if isinstance(error_data, dict) and 'msg' in error_data:
                        error_msg += f": {error_data['msg']}"
                except:
                    pass

                logger.error(f"Project environment variables request failed with status {response.status_code}: {error_msg}")

                # Distinguish between 4xx and 5xx errors
                if 400 <= response.status_code < 500:
                    raise Exception(f"Client error ({response.status_code}): {error_msg}")
                else:
                    raise Exception(f"Server error ({response.status_code}): {error_msg}")

            result = response.json()

            # Validate response structure
            if not isinstance(result, dict):
                raise Exception("Invalid response format: expected JSON object")

            if result.get('code') != 0:
                msg = result.get('msg', 'Unknown error')
                raise Exception(f"Project environment variables API error: code={result.get('code')}, msg={msg}")

            if 'data' not in result or not isinstance(result['data'], dict):
                raise Exception("Invalid response format: missing or invalid 'data' field")

            if 'secrets' not in result['data'] or not isinstance(result['data']['secrets'], list):
                raise Exception("Invalid response format: missing or invalid 'secrets' field in data")

            # Convert secrets to ProjectEnvVar objects
            env_vars = []
            for i, secret in enumerate(result['data']['secrets']):
                if not isinstance(secret, dict):
                    raise Exception(f"Invalid response format: secrets[{i}] must be a dict")

                if 'key' not in secret or 'value' not in secret:
                    raise Exception(f"Invalid response format: secrets[{i}] must contain 'key' and 'value' keys")

                if not isinstance(secret['key'], str) or not isinstance(secret['value'], str):
                    raise Exception(f"Invalid response format: secrets[{i}] key and value must be strings")

                env_vars.append(ProjectEnvVar(key=secret['key'], value=secret['value']))

            logger.debug("Project environment variables retrieved")
            return ProjectEnvVars(vars=env_vars)

        except requests.exceptions.RequestException as e:
            logger.error(f"Project environment variables request failed: {e}")
            raise Exception(f"Failed to retrieve project environment variables: {e}")
        except ValueError as e:
            logger.error(f"Failed to parse project environment variables response: {e}")
            raise Exception(f"Invalid project environment variables response format: {e}")

    def close(self):
        """Close the client and cleanup resources."""
        if hasattr(self, '_session'):
            self._session.close()
        logger.debug("Workload Identity Client closed")
