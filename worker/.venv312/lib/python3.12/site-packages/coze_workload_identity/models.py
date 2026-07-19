"""
Data models for the Coze Workload Identity SDK.
"""

from typing import List, Dict, Any
from dataclasses import dataclass


@dataclass
class ProjectEnvVar:
    """
    Represents a single project environment variable.

    Attributes:
        key: The environment variable key
        value: The environment variable value
    """
    key: str
    value: str

    def __post_init__(self):
        """Validate the data after initialization."""
        if not isinstance(self.key, str):
            raise ValueError(f"key must be a string, got {type(self.key)}")
        if not isinstance(self.value, str):
            raise ValueError(f"value must be a string, got {type(self.value)}")


@dataclass
class ProjectEnvVars:
    """
    Represents a collection of project environment variables.

    Attributes:
        vars: List of ProjectEnvVar objects
    """
    vars: List[ProjectEnvVar]

    def __post_init__(self):
        """Validate the data after initialization."""
        if not isinstance(self.vars, list):
            raise ValueError(f"vars must be a list, got {type(self.vars)}")

        for i, var in enumerate(self.vars):
            if not isinstance(var, ProjectEnvVar):
                raise ValueError(f"vars[{i}] must be a ProjectEnvVar object, got {type(var)}")

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format."""
        return {
            "vars": [{"key": var.key, "value": var.value} for var in self.vars]
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProjectEnvVars":
        """Create from dictionary format."""
        if not isinstance(data, dict):
            raise ValueError(f"data must be a dict, got {type(data)}")

        if "vars" not in data:
            raise ValueError("data must contain 'vars' key")

        if not isinstance(data["vars"], list):
            raise ValueError(f"data['vars'] must be a list, got {type(data['vars'])}")

        vars_list = []
        for i, var_data in enumerate(data["vars"]):
            if not isinstance(var_data, dict):
                raise ValueError(f"data['vars'][{i}] must be a dict, got {type(var_data)}")

            if "key" not in var_data or "value" not in var_data:
                raise ValueError(f"data['vars'][{i}] must contain 'key' and 'value' keys")

            if not isinstance(var_data["key"], str):
                raise ValueError(f"data['vars'][{i}]['key'] must be a string, got {type(var_data['key'])}")

            if not isinstance(var_data["value"], str):
                raise ValueError(f"data['vars'][{i}]['value'] must be a string, got {type(var_data['value'])}")

            vars_list.append(ProjectEnvVar(key=var_data["key"], value=var_data["value"]))

        return cls(vars=vars_list)

    def get(self, key: str, default: str = None) -> str:
        """Get environment variable value by key."""
        for var in self.vars:
            if var.key == key:
                return var.value
        return default

    def __len__(self) -> int:
        """Return the number of environment variables."""
        return len(self.vars)

    def __iter__(self):
        """Iterate over the environment variables."""
        return iter(self.vars)

    def __getitem__(self, key: str) -> str:
        """Get environment variable value by key using bracket notation."""
        value = self.get(key)
        if value is None:
            raise KeyError(f"Environment variable '{key}' not found")
        return value

    def __contains__(self, key: str) -> bool:
        """Check if environment variable exists."""
        return self.get(key) is not None